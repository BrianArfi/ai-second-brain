#!/usr/bin/env python3
"""ledger_sync.py - push a ledger change out to every view of it, immediately.

Why this exists
---------------
`ledger_lock.py` fixed *corruption*: two writers no longer drop each other's
records. It did nothing about *latency*. After a record changed, four things
still had to happen before anyone else could see it, and all four were left to
a cron job or to whoever remembered:

    journal/state/<ledger>.json      <- the write itself, instant
    journal/state/*.index.json       <- stale until state_index.py ran
    journal/master_followup_tracker.md
                                     <- stale until render_followup_tracker.py ran
    origin/main                      <- stale until something committed + pushed

Between the write and the last of those, every other session and every cron job
reading this repo saw the old value. On a repo where several Claude sessions and
19 cron jobs run against the same tree, "stale until the next daily run" means a
closed commitment gets chased again, a signed-off decision gets re-litigated, and
a briefing reports an item that was finished hours earlier.

So: the moment a ledger mutates, re-render everything derived from it and get it
onto origin/main. One function, called by the ledger CLIs themselves, so it
cannot be forgotten.

Commands
--------
    ledger_sync.py sync [--ledger NAME] [--reason "close COM-0123"]
        Re-render derived views, commit the state paths, pull --rebase if
        origin moved, push. Safe to run any time; a no-op when nothing changed.

    ledger_sync.py check
        Report drift without changing anything. Exit 0 clean, 1 drifted.
        Used by the Stop hook.

    ledger_sync.py refresh [--max-age S]
        The read side. Throttled fetch; pull when origin has newer ledger
        commits and pulling is safe. Used by the PreToolUse freshness hook.

    ledger_sync.py status
        Human-readable summary of last sync per ledger.

Safety
------
* Only the paths in SYNC_PATHS are ever staged. Whatever else is dirty in the
  tree is left exactly as it was, so this never sweeps up in-progress work.
* Never raises into the caller. A ledger write must not fail because the network
  was down; failures are recorded in journal/state/ledger_sync.json and surfaced
  by the Stop hook instead.
* `LEDGER_SYNC_OFFLINE=1` skips all network git. `LEDGER_SYNC_DISABLE=1` skips
  everything including rendering.
"""

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
STATE_DIR = os.path.join(BASE_DIR, 'journal', 'state')
HEARTBEAT = os.path.join(STATE_DIR, 'ledger_sync.json')
FETCH_STAMP = os.path.join(STATE_DIR, '.locks', 'ledger_fetch.stamp')
BG_LOCK = os.path.join(STATE_DIR, '.locks', 'ledger_bg_sync.lock')
BG_LOG = os.path.join(STATE_DIR, '.locks', 'ledger_bg_sync.log')

WIB = timezone(timedelta(hours=7))
GIT_LOCAL_TIMEOUT = 20
GIT_NET_TIMEOUT = 30
DEFAULT_FETCH_MAX_AGE = 45.0

# The ledgers this module knows how to propagate. Key = lock name used by
# ledger_lock.py, so the two stay in step.
LEDGERS = {
    'commitments': 'journal/state/commitments.json',
    'waiting_on': 'journal/state/waiting_on.json',
    'decisions': 'journal/state/decisions.json',
    'chase_queue': 'journal/state/chase_queue.json',
}

# Everything staged by a sync. Nothing outside this list is ever touched.
SYNC_PATHS = [
    'journal/state',
    'journal/master_followup_tracker.md',
]

RENDERERS = [
    (['python3', os.path.join(BASE_DIR, '.agent/scripts/state_index.py'), '--write'],
     'state indexes'),
    (['python3', os.path.join(
        BASE_DIR,
        '.agent/skills/project-tracking-update/scripts/render_followup_tracker.py')],
     'master_followup_tracker.md'),
]

# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def now_wib():
    return datetime.now(WIB).strftime('%Y-%m-%d %H:%M:%S WIB')

def git(args, timeout=GIT_LOCAL_TIMEOUT):
    """Run git with an arg list. Returns (rc, stdout, stderr); rc None on failure
    to even run git (missing binary, timeout)."""
    try:
        p = subprocess.run(['git', *args], cwd=BASE_DIR, capture_output=True,
                           text=True, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return None, '', str(exc)

def file_digest(path):
    try:
        with open(path, 'rb') as f:
            return hashlib.sha1(f.read()).hexdigest()
    except OSError:
        return None

def snapshot(ledgers=None):
    """Digest of each ledger file, for "did this process actually change
    anything" comparisons."""
    names = ledgers or list(LEDGERS)
    return {n: file_digest(os.path.join(BASE_DIR, LEDGERS[n]))
            for n in names if n in LEDGERS}

def load_heartbeat():
    try:
        with open(HEARTBEAT, 'r', encoding='utf-8') as f:
            data = json.load(f)
        data.setdefault('ledgers', {})
        return data
    except (OSError, ValueError):
        return {'ledgers': {}, 'last_error': None, 'pending_push': False}

def save_heartbeat(data):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = HEARTBEAT + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=1, ensure_ascii=False, sort_keys=True)
        f.write('\n')
    os.replace(tmp, HEARTBEAT)

def disabled():
    return os.environ.get('LEDGER_SYNC_DISABLE') == '1'

def offline():
    return os.environ.get('LEDGER_SYNC_OFFLINE') == '1'

def guard_disabled():
    return os.environ.get('LEDGER_GUARD_DISABLE') == '1'

def guard_warn_only():
    """Warn-only is the default while the guard beds in. Set
    LEDGER_GUARD_WARN_ONLY=0 to let it actually refuse a commit."""
    return os.environ.get('LEDGER_GUARD_WARN_ONLY', '1') != '0'

# --------------------------------------------------------------------------
# deletion guard: the one check that spans both machines
# --------------------------------------------------------------------------
#
# On 17 Aug 2026 two records were added on macOS at 11:53 WIB (d4152a8a,
# b6172f83) and deleted fourteen minutes later by the WSL cron sweep (b2644a58,
# 50 deletions and 0 insertions, -"id": "WAIT-0342" and -"id": "WAIT-0343").
# Nothing was corrupt and nothing raced on one filesystem. The WSL box simply
# read a `waiting_on.json` that had never seen the two records, wrote its whole
# snapshot back, and git carried the deletion forward.
#
# That is the failure `ledger_lock.py` cannot see, by construction: `fcntl` is a
# single-filesystem primitive, so it fires zero times across two clones. A
# content digest fares no better -- the bytes on the WSL disk genuinely had not
# changed between its read and its write.
#
# What DOES span both machines is git, because every ledger mutation here is
# already a commit. So the check is: before committing, compare the record ids
# on disk against HEAD and against origin/main, and refuse when one has
# vanished. This sits below every other layer, which is the point -- it catches
# cron, a hand-edit, the headless worker in weekly_ledger_audit.py, and the
# other machine, none of which a lock or a Claude Code hook can reach.
#
# The exemption is the record's STATUS, not the invoking command. An earlier
# draft gated on the command name (close/drop/delete/prune) and was wrong:
# `waiting_watchdog.cmd_sweep` calls `prune()` on every hourly run, deleting
# answered and dropped items past DROPPED_RETENTION_DAYS. A command allowlist
# either includes `sweep`, which waves through the exact incident above, or
# excludes it and fires every hour until someone switches the guard off.
#
# Keying on status is both correct and stronger: it is command-agnostic, so a
# hand-edit that drops an open record is caught the same way a sweep is.

# Where the records live, and which statuses may legitimately disappear.
# `None` for terminal_statuses means "this ledger is not keyed by record id";
# see chase_queue below.
LEDGER_RECORDS = {
    'commitments': {'container': 'items', 'terminal': ('done', 'dropped')},
    'waiting_on': {'container': 'items', 'terminal': ('answered', 'dropped')},
    'decisions': {'container': 'items', 'terminal': ('decided', 'superseded')},
    # chase_queue's `queue` is keyed by owner slug and rebuilt from scratch on
    # every `build`, so an entry leaving is the normal case rather than a loss.
    # The records it is derived FROM live in waiting_on, which is guarded above.
    'chase_queue': None,
}

def _records_at(ledger, ref):
    """`{id: status}` for a ledger as of a git ref, or None when it cannot be
    read. None means "no opinion" and is never treated as an empty ledger: a
    file missing from a ref (the ledger did not exist yet) would otherwise read
    as every record having been deleted."""
    spec = LEDGER_RECORDS.get(ledger)
    if not spec:
        return None
    path = LEDGERS.get(ledger)
    if not path:
        return None
    if ref is None:
        try:
            with open(os.path.join(BASE_DIR, path), 'r', encoding='utf-8') as f:
                text = f.read()
        except OSError:
            return None
    else:
        rc, text, _ = git(['show', f'{ref}:{path}'], timeout=GIT_LOCAL_TIMEOUT)
        if rc != 0:
            return None
    try:
        items = (json.loads(text) or {}).get(spec['container']) or {}
    except ValueError:
        return None
    if not isinstance(items, dict):
        return None
    return {k: (v.get('status') if isinstance(v, dict) else None)
            for k, v in items.items()}

def _next_seq_at(ledger, ref):
    path = LEDGERS.get(ledger)
    if not path:
        return None
    if ref is None:
        try:
            with open(os.path.join(BASE_DIR, path), 'r', encoding='utf-8') as f:
                text = f.read()
        except OSError:
            return None
    else:
        rc, text, _ = git(['show', f'{ref}:{path}'], timeout=GIT_LOCAL_TIMEOUT)
        if rc != 0:
            return None
    try:
        val = (json.loads(text) or {}).get('next_seq')
    except ValueError:
        return None
    return val if isinstance(val, int) else None

def check_deletions(ledger):
    """Records that vanished while still live, compared against HEAD and
    origin/main. Returns a list of human-readable problems; empty is good.

    Never raises. A check that cannot run returns no problems rather than
    blocking a sync, because a guard that fails closed on a git hiccup would
    strand exactly the work `worktree_sync.py` exists to protect.
    """
    if guard_disabled() or ledger not in LEDGER_RECORDS or not LEDGER_RECORDS[ledger]:
        return []
    terminal = LEDGER_RECORDS[ledger]['terminal']
    problems = []
    try:
        current = _records_at(ledger, None)
        if current is None:
            return []
        for ref in ('HEAD', 'origin/main'):
            was = _records_at(ledger, ref)
            if not was:
                continue
            lost = [(rid, st) for rid, st in was.items()
                    if rid not in current and st not in terminal]
            if lost:
                lost.sort()
                shown = ', '.join(f'{rid} ({st or "no status"})' for rid, st in lost[:8])
                more = f' and {len(lost) - 8} more' if len(lost) > 8 else ''
                problems.append(
                    f'{len(lost)} live record(s) present in {ref} are missing from '
                    f'{ledger}.json: {shown}{more}')
            before = _next_seq_at(ledger, ref)
            now = _next_seq_at(ledger, None)
            if before is not None and now is not None and now < before:
                problems.append(
                    f'{ledger}.next_seq went backwards ({before} in {ref} -> {now}); '
                    'the next record created would reuse an id that already exists')
    except Exception as exc:                       # never break a ledger CLI
        sys.stderr.write(f'[ledger_sync] deletion guard could not run: {exc}\n')
        return []
    return problems

def report_deletions(ledger, problems):
    """Print the refusal and record it. Returns True when the commit must stop."""
    if not problems:
        return False
    warn_only = guard_warn_only()
    head = ('WARNING (warn-only)' if warn_only
            else 'REFUSING TO COMMIT')
    lines = [f'[ledger_sync] {head}: records disappeared without being closed.']
    lines += [f'  - {p}' for p in problems]
    lines.append('  This is the cross-machine lost-update signature: something read '
                 'a stale copy of the ledger and wrote its whole snapshot back.')
    lines.append('  Recover with:  git show origin/main:journal/state/'
                 f'{ledger}.json > /tmp/{ledger}.origin.json   (then re-add the '
                 'missing records via the CLI)')
    if warn_only:
        lines.append('  Committing anyway because LEDGER_GUARD_WARN_ONLY is on. '
                     'Set LEDGER_GUARD_WARN_ONLY=0 to make this block.')
    else:
        lines.append('  Nothing has been committed and the file on disk is '
                     'untouched, so no work is lost. Set LEDGER_GUARD_DISABLE=1 '
                     'to override.')
    sys.stderr.write('\n'.join(lines) + '\n')
    try:
        hb = load_heartbeat()
        hb['last_guard_trip'] = {'wib': now_wib(), 'ledger': ledger,
                                 'problems': problems, 'blocked': not warn_only}
        save_heartbeat(hb)
    except Exception:
        pass
    return not warn_only

# --------------------------------------------------------------------------
# render
# --------------------------------------------------------------------------

def render_derived(verbose=False):
    """Re-run every generator that reads a ledger. Each is idempotent and takes
    no lock (they invoke the ledgers' read-only `report`), so this is safe to
    call from inside a process already holding a ledger lock."""
    problems = []
    for cmd, label in RENDERERS:
        if not os.path.exists(cmd[1]):
            continue
        try:
            p = subprocess.run(cmd, cwd=BASE_DIR, capture_output=True,
                               text=True, timeout=120)
            if p.returncode != 0:
                problems.append(f'{label}: exit {p.returncode} {p.stderr.strip()[:200]}')
            elif verbose:
                print(f'  rendered {label}')
        except (subprocess.TimeoutExpired, OSError) as exc:
            problems.append(f'{label}: {exc}')
    return problems

# --------------------------------------------------------------------------
# git side
# --------------------------------------------------------------------------

def dirty_sync_paths():
    """Porcelain lines for the synced paths only."""
    rc, out, _ = git(['status', '--porcelain', '--', *SYNC_PATHS])
    if rc != 0 or not out:
        return []
    return [ln for ln in out.splitlines() if ln.strip()]

def on_main():
    rc, out, _ = git(['rev-parse', '--abbrev-ref', 'HEAD'], timeout=10)
    return rc == 0 and out.strip() == 'main'

def behind_count():
    """Commits on origin/main not in HEAD. -1 if unknown."""
    rc, out, _ = git(['rev-list', '--count', 'HEAD..origin/main'], timeout=10)
    if rc != 0:
        return -1
    try:
        return int(out.strip())
    except ValueError:
        return -1

def ahead_count():
    rc, out, _ = git(['rev-list', '--count', 'origin/main..HEAD'], timeout=10)
    if rc != 0:
        return -1
    try:
        return int(out.strip())
    except ValueError:
        return -1

def fetch(max_age=0.0):
    """Throttled `git fetch origin main`. Returns True if the remote ref is
    considered current (either just fetched, or fetched recently enough)."""
    if offline():
        return False
    try:
        age = time.time() - os.path.getmtime(FETCH_STAMP)
        if max_age and age < max_age:
            return True
    except OSError:
        pass
    rc, _, _ = git(['fetch', '--quiet', 'origin', 'main'], timeout=GIT_NET_TIMEOUT)
    if rc == 0:
        try:
            os.makedirs(os.path.dirname(FETCH_STAMP), exist_ok=True)
            with open(FETCH_STAMP, 'w') as f:
                f.write(str(time.time()))
        except OSError:
            pass
        return True
    return False

# Files this script writes on every single sync, on every machine, which therefore
# conflict against each other constantly and carry no information worth merging.
# `ledger_sync.json` is this machine's own "when did I last sync" bookkeeping, and
# the tracker is a generated view re-rendered from the ledgers a few lines later.
# Taking upstream's copy of either loses nothing: the next render rewrites both.
#
# This list must never grow to include a real ledger. Those are the records
# themselves, and silently discarding one side of a conflict there is the exact
# lost-update failure the lock in `ledger_lock.py` exists to prevent.
REGENERATED_ON_CONFLICT = (
    'journal/state/ledger_sync.json',
    'journal/master_followup_tracker.md',
)

@contextlib.contextmanager
def all_ledger_locks(timeout=25.0):
    """Hold every ledger lock for the duration of a git operation that rewrites
    ledger files on disk.

    Why all four, and why here. `ledger_lock.py` stops two writers dropping each
    other's records, but it only guards processes that go through the ledger
    CLIs. This module reaches the same files by a completely different route: a
    `git pull --rebase` replaces `commitments.json`, `waiting_on.json` and the
    rest wholesale, and it did so holding no lock at all. So a cron job that had
    read 420 records and was about to write 421 could have the file swapped
    underneath it and then write its stale copy back over the rebase -- the exact
    lost-update this repo already lost records to, arriving by the one path the
    lock was not watching.

    Re-entrant by way of `ledger_lock.held_locks()`: every ledger CLI already
    holds its own lock before it calls this module, and `flock` is per
    file-description, so re-taking that one would hang the process against
    itself.

    Best-effort, deliberately. If the locks cannot be had in `timeout`, this
    yields False and the caller declines the git operation rather than running it
    unprotected or raising into a ledger CLI that must never fail because a sync
    could not run.
    """
    sys.path.insert(0, os.path.join(BASE_DIR, '.agent', 'scripts'))
    try:
        from ledger_lock import ledger_lock, held_locks
    except Exception:
        # No lock module is a reason to be careful, not a reason to stop syncing:
        # this is exactly the state the repo was in before the lock existed.
        yield True
        return

    already = held_locks()
    wanted = [n for n in sorted(LEDGERS) if n not in already]
    stack = contextlib.ExitStack()
    try:
        with stack:
            for name in wanted:                 # sorted: a fixed order cannot deadlock
                stack.enter_context(ledger_lock(name, timeout=timeout))
            yield True
    except TimeoutError:
        yield False

def unmerged_paths():
    """Paths git considers conflicted right now, newest source of truth for 'is the
    tree safe to commit'. Empty list on any git failure, because a check that cannot
    run must not be read as a clean tree by accident -- callers treat non-empty as
    'stop', so the conservative direction here is to let other guards speak."""
    rc, out, _ = git(['diff', '--name-only', '--diff-filter=U'],
                     timeout=GIT_LOCAL_TIMEOUT)
    if rc != 0:
        return []
    return [p for p in out.strip().splitlines() if p.strip()]

def rebase_in_progress():
    for marker in ('rebase-merge', 'rebase-apply'):
        if os.path.isdir(os.path.join(BASE_DIR, '.git', marker)):
            return True
    return False


# A rebase state dir older than this is abandoned, not live. A live rebase run
# by this module finishes or aborts within its git timeouts (30s); five minutes
# is two orders of magnitude past that.
STUCK_REBASE_MAX_AGE = 300.0


def recover_stuck_rebase(max_age=STUCK_REBASE_MAX_AGE):
    """Abort a rebase that some earlier, killed process left behind.

    On 2 Sep 2026 a `rebase --autostash` was killed mid-flight (its parent hook
    hit the harness timeout), leaving `.git/rebase-merge` behind and HEAD
    detached at origin/main. For the next hour every sync path declined to act:
    `commit_and_push` said "not on main; left uncommitted" and
    `refresh_before_read` bailed on `not on_main()` -- and the one function that
    knows how to clear a stuck rebase (`_rebase_onto_origin_locked`) sits BEHIND
    those bail-outs, so nothing ever reached it. Ledger writes piled up
    uncommitted until an unrelated `cmd_refresh` happened to run.

    Aborting is safe here: whatever the dead rebase was replaying still exists
    on origin (it had just been fetched) and in this repo's reflog, and abort
    restores the pre-rebase branch and autostash. The age check keeps this from
    shooting down a rebase another process is actively running; the ledger lock
    covers the ones this module runs itself.

    Returns True when a stuck rebase was found and cleared.
    """
    stale = False
    now = time.time()
    for marker in ('rebase-merge', 'rebase-apply'):
        path = os.path.join(BASE_DIR, '.git', marker)
        try:
            if os.path.isdir(path) and (now - os.path.getmtime(path)) > max_age:
                stale = True
        except OSError:
            pass
    if not stale:
        return False
    with all_ledger_locks(timeout=5.0) as got:
        if not got:
            return False          # someone is mid-write; let them deal with it
        if not rebase_in_progress():
            return False          # cleared while we waited for the lock
        rc, _, err = git(['rebase', '--abort'], timeout=GIT_LOCAL_TIMEOUT)
        if rc == 0:
            sys.stderr.write('[ledger_sync] aborted a stuck rebase left by an '
                             'earlier run; back on the branch.\n')
            return True
        # A rebase dir with no head-name is not a rebase git can abort (the
        # process died before writing its state); git's own hint for this is to
        # remove the directory. Only that truncated case is removed by hand.
        cleared = False
        for marker in ('rebase-merge', 'rebase-apply'):
            path = os.path.join(BASE_DIR, '.git', marker)
            if (os.path.isdir(path)
                    and not os.path.exists(os.path.join(path, 'head-name'))):
                import shutil
                shutil.rmtree(path, ignore_errors=True)
                cleared = True
        if cleared and not rebase_in_progress():
            sys.stderr.write('[ledger_sync] removed a truncated rebase state '
                             'dir left by a killed process.\n')
            return True
        sys.stderr.write(f'[ledger_sync] found a stuck rebase but could not '
                         f'abort it: {(err or "")[:120]}\n')
        return False


def resolve_regenerated_conflicts():
    """Take upstream's side of the two files this script rewrites anyway.

    Returns True when every conflicted path was one of them and the rebase can
    carry on. A conflict anywhere else returns False untouched, so a real ledger
    collision still stops the sync and gets a human.
    """
    rc, out, _ = git(['diff', '--name-only', '--diff-filter=U'], timeout=GIT_LOCAL_TIMEOUT)
    if rc != 0:
        return False
    conflicted = [p for p in out.strip().splitlines() if p.strip()]
    if not conflicted or any(p not in REGENERATED_ON_CONFLICT for p in conflicted):
        return False
    for path in conflicted:
        # `--ours` mid-rebase is the upstream side: the commits already on origin.
        if git(['checkout', '--ours', '--', path], timeout=GIT_LOCAL_TIMEOUT)[0] != 0:
            git(['checkout', '--theirs', '--', path], timeout=GIT_LOCAL_TIMEOUT)
        git(['add', '--', path], timeout=GIT_LOCAL_TIMEOUT)
    return True

def rebase_onto_origin():
    """Pull with rebase, autostashing whatever else is in progress.

    Returns (ok, message). On failure the rebase is aborted so the tree is never
    left mid-rebase; if the autostash could not be reapplied it stays in
    `git stash list` and the message says so.
    """
    # Every git command below can rewrite a ledger file on disk, so none of them
    # runs unprotected. Declining is the right answer on timeout: a sync that did
    # not happen is visible in the heartbeat and recoverable on the next run, and
    # a rebase racing a cron mid read-modify-write is neither.
    with all_ledger_locks() as got:
        if not got:
            return False, ('another process is writing the ledgers; skipped this '
                           'rebase rather than racing it')
        return _rebase_onto_origin_locked()

def _rebase_onto_origin_locked():
    # A rebase left in progress by an earlier run makes `git pull --rebase` fail
    # with "Cannot rebase onto multiple branches", which says nothing about the
    # actual cause and sent five syncs in one day into local-only limbo. Clear it
    # first: whatever it was rebasing is still on origin, and this function is
    # about to fetch it again.
    if rebase_in_progress():
        git(['rebase', '--abort'], timeout=GIT_LOCAL_TIMEOUT)

    # Fetch into the remote-tracking ref, then rebase onto THAT -- never onto
    # FETCH_HEAD, which is what `git pull --rebase` does.
    #
    # This is the real cause of "Cannot rebase onto multiple branches", and the
    # comment above used to blame a leftover rebase for it, which is why aborting
    # first never stopped it recurring. `git pull` fetches and then rebases in two
    # steps, and FETCH_HEAD is a single shared file: any other process running a
    # bare `git fetch origin` in that window rewrites it with every branch in the
    # repo (5 entries here, the extras marked not-for-merge), and the rebase then
    # sees several branches and refuses. With seven cron jobs and several sessions
    # against one checkout, landing in that window is a matter of when.
    #
    # `refs/remotes/origin/main` cannot be turned into "multiple branches" by
    # anyone else's fetch, so the rebase target is stable no matter who else is
    # touching this repo at the same moment.
    rc, _, err = git(['fetch', '--quiet', 'origin',
                      '+refs/heads/main:refs/remotes/origin/main'],
                     timeout=GIT_NET_TIMEOUT)
    if rc != 0:
        return False, f'fetch failed: {(err or "")[:160]}'
    rc, out, err = git(['rebase', '--autostash', 'origin/main'],
                       timeout=GIT_NET_TIMEOUT)
    if rc == 0:
        # Exit 0 is not the same as "the tree is clean". When `--autostash` cannot
        # re-apply what it parked, git reports it as a WARNING, keeps the stash, and
        # still exits 0 -- leaving conflict markers in the working tree. Returning
        # success there is how `<<<<<<< HEAD` gets staged by the caller and pushed
        # into a real ledger, which is the worst thing this script can do.
        stuck = unmerged_paths()
        if stuck:
            return False, ('rebased, but re-applying local changes conflicted in '
                           + ', '.join(stuck[:4])
                           + '; your work is safe in `git stash list`. Resolve it and '
                             'run `ledger_sync.py sync` again')
        return True, 'rebased onto origin/main'

    # Conflicts confined to the files this script regenerates are not a disagreement
    # about anything, they are two machines stamping the same bookkeeping. Resolve
    # and carry on rather than abandoning the whole sync.
    steps = 0
    while rc != 0 and rebase_in_progress() and resolve_regenerated_conflicts():
        steps += 1
        if steps > 20:                       # a stuck rebase must not spin forever
            break
        rc, out, err = git(['-c', 'core.editor=true', 'rebase', '--continue'],
                           timeout=GIT_LOCAL_TIMEOUT)
        if rc == 0 and not rebase_in_progress():
            return True, f'rebased onto origin/main ({steps} generated-file conflict(s) taken from origin)'

    # Do not leave a half-finished rebase behind.
    inprogress, _, _ = git(['rev-parse', '--verify', '--quiet', 'REBASE_HEAD'],
                           timeout=10)
    if inprogress == 0:
        git(['rebase', '--abort'], timeout=GIT_LOCAL_TIMEOUT)
    rc_stash, stash_out, _ = git(['stash', 'list'], timeout=10)
    tail = (err or out or '').strip().splitlines()
    detail = tail[-1] if tail else 'unknown error'
    if rc_stash == 0 and stash_out.strip():
        detail += ' (an autostash may be left in `git stash list`)'
    return False, f'rebase failed: {detail}'

def commit_and_push(reason, do_push=True):
    """Stage SYNC_PATHS only, commit, get it onto origin/main.

    Returns a dict describing what happened; never raises.
    """
    result = {'committed': False, 'pushed': False, 'commit': None,
              'note': None, 'error': None}

    rc, _, _ = git(['rev-parse', '--git-dir'], timeout=10)
    if rc != 0:
        result['error'] = 'not a git repo'
        return result

    if not on_main():
        # A dead rebase leaves HEAD detached; clear it rather than skipping
        # every sync until a human notices (2 Sep 2026: one hour of
        # "not on main; left uncommitted" from a rebase whose process was gone).
        if not (recover_stuck_rebase() and on_main()):
            result['note'] = 'not on main; left uncommitted'
            return result

    if not dirty_sync_paths():
        result['note'] = 'nothing to commit'
        # Still push if a previous run committed but could not push.
        if do_push and not offline() and fetch(max_age=DEFAULT_FETCH_MAX_AGE):
            if ahead_count() > 0:
                result.update(_push())
        return result

    rc, _, err = git(['add', '--', *SYNC_PATHS])
    if rc != 0:
        result['error'] = f'git add failed: {err[:200]}'
        return result

    rc, out, _ = git(['diff', '--cached', '--name-only', '--', *SYNC_PATHS])
    if rc != 0 or not out.strip():
        result['note'] = 'nothing staged'
        return result
    n_files = len([ln for ln in out.splitlines() if ln.strip()])

    subject = f'chore(ledger): {reason}' if reason else 'chore(ledger): sync state'
    subject = subject[:100]
    body = (f'{n_files} state/tracker file(s) synced at {now_wib()}.\n\n'
            'Auto-committed by .agent/scripts/ledger_sync.py so other sessions '
            'and cron jobs read this change immediately instead of the previous '
            "run's values.")
    # `--only -- SYNC_PATHS`, matching the `git add` above. The add is correctly
    # scoped, but a bare `git commit` publishes the WHOLE index, and the index is
    # not this script's to assume: anything the owner or a concurrent session had
    # staged and not yet committed would ride along into an auto-pushed commit
    # labelled as a ledger sync. The docstring's promise at the top of this file --
    # "Whatever else is dirty in the tree is left exactly as it was" -- was true of
    # dirty files and not of staged ones until this pathspec was added.
    rc, _, err = git(['commit', '--no-verify', '--only',
                      '-m', subject, '-m', body, '--', *SYNC_PATHS])
    if rc != 0:
        result['error'] = f'git commit failed: {err[:200]}'
        return result
    result['committed'] = True
    rc, out, _ = git(['rev-parse', '--short', 'HEAD'], timeout=10)
    result['commit'] = out.strip() if rc == 0 else None

    if not do_push or offline():
        result['note'] = 'push skipped'
        return result

    result.update(_push())
    return result

# How many times a rejected push is re-tried before giving up. Every writer of this
# branch is a short automated run, so a rejection means someone landed in the
# window between our fetch and our push, and the answer is to take their work and
# go again rather than to stop. Three attempts covers the observed contention on
# this repo (several Claude sessions plus seven cron jobs) while still terminating.
PUSH_ATTEMPTS = 3

def _push():
    """Get the current commit onto origin/main, conceding to whoever got there first.

    A push can be rejected for two different reasons that read almost the same in
    git's output, and both are ordinary here rather than exceptional:

      * we are behind, git says "fetch first";
      * the ref moved between the server's check and its update, and GitHub says
        "cannot lock ref 'refs/heads/main': is at X but expected Y". That message
        looks like a force-with-lease failure and is not one -- a plain push races
        the same way.

    Either way the fix is identical: rebase onto what landed and try again. This
    used to give up after the first rejection, which is how five syncs in one day
    ended as commits nobody else could see.
    """
    out = {'pushed': False, 'error': None, 'note': None}
    if not fetch(max_age=0.0):
        out['note'] = 'fetch failed; commit is local only'
        return out

    last_err = ''
    for attempt in range(1, PUSH_ATTEMPTS + 1):
        if behind_count() > 0:
            ok, msg = rebase_onto_origin()
            if not ok:
                out['error'] = msg + '; commit is local only'
                return out
        rc, _, err = git(['push', 'origin', 'main'], timeout=GIT_NET_TIMEOUT)
        if rc == 0:
            out['pushed'] = True
            if attempt > 1:
                out['note'] = f'pushed on attempt {attempt} after losing a race'
            return out
        last_err = err
        # Re-read origin before deciding anything: the rejection itself is the
        # news that our view of the branch is stale.
        if attempt < PUSH_ATTEMPTS and not fetch(max_age=0.0):
            break

    out['error'] = (f'push failed after {PUSH_ATTEMPTS} attempts: '
                    f'{last_err[:200]}; commit is local only')
    return out

# --------------------------------------------------------------------------
# public entry point used by the ledger CLIs
# --------------------------------------------------------------------------

def sync_after_mutation(ledger, reason='', before=None, verbose=False):
    """Called by a ledger CLI right after a mutating command finishes.

    `before` is an optional snapshot() taken before the command ran. When it is
    supplied and nothing in it changed, this returns immediately without
    rendering or touching git, so read-only-in-practice runs (a sweep that found
    nothing) stay cheap.
    """
    if disabled():
        return {'skipped': 'LEDGER_SYNC_DISABLE=1'}
    try:
        if before is not None:
            after = snapshot(list(before))
            if after == before:
                return {'skipped': 'no ledger change'}
        return _do_sync(ledger, reason, verbose=verbose)
    except Exception as exc:                      # never break the caller
        try:
            hb = load_heartbeat()
            hb['last_error'] = f'{now_wib()}: {exc}'
            save_heartbeat(hb)
        except Exception:
            pass
        sys.stderr.write(f'[ledger_sync] non-fatal: {exc}\n')
        return {'error': str(exc)}

def refresh_before_read(ledger):
    """Pull newer ledger commits BEFORE the command reads the file.

    This is the half of the 17 Aug incident that no lock could have covered.
    `run_and_sync` used to snapshot, run, and only then sync -- so a cron sweep
    read whatever its local disk happened to hold and wrote that whole snapshot
    back. On 17 Aug the WSL host's copy of `waiting_on.json` had never seen two
    records macOS had added fourteen minutes earlier, and the sweep's write
    deleted them (b2644a58).

    Interactive sessions were already covered: `.claude/hooks/ledger_freshness.py`
    calls `refresh` as a PreToolUse hook. Cron was not, and cron has no hooks and
    no scheduled `git pull` of its own on the WSL host. This closes that gap for
    both.

    Best-effort by design. A ledger command must never fail because the network
    was down, so every failure path here logs and proceeds; the deletion guard in
    `_do_sync` is the backstop that catches a stale write regardless.
    """
    if disabled() or offline() or ledger not in LEDGERS:
        return
    try:
        if not on_main() and recover_stuck_rebase():
            pass                  # cleared a dead rebase; re-check below
        if not on_main() or dirty_sync_paths():
            # Uncommitted ledger changes mean a rebase could conflict. Leave it
            # alone: this run's own sync will deal with it on the way out.
            return
        if not fetch(max_age=DEFAULT_FETCH_MAX_AGE):
            return
        rc, out, _ = git(['rev-list', '--count', 'HEAD..origin/main', '--', *SYNC_PATHS],
                         timeout=10)
        if rc != 0 or not out.strip().isdigit() or int(out.strip()) == 0:
            return
        n = int(out.strip())
        ok, msg = rebase_onto_origin()
        if ok:
            sys.stderr.write(f'[ledger_sync] pulled {n} newer ledger commit(s) '
                             f'before reading {ledger}.\n')
        else:
            sys.stderr.write(f'[ledger_sync] could not refresh before reading '
                             f'{ledger} ({msg}); proceeding on the local copy.\n')
    except Exception as exc:
        sys.stderr.write(f'[ledger_sync] refresh-before-read skipped: {exc}\n')

def run_and_sync(ledger, main_fn, argv=None):
    """Wrapper the ledger CLIs use in place of a bare `main()`.

    Snapshots the ledger, runs the command, and propagates the change on the way
    out - including when the command calls sys.exit(), which is why this is a
    try/finally rather than a trailing call. If the command changed nothing the
    propagation is a no-op, so read-only-in-practice runs stay cheap.
    """
    argv = sys.argv[1:] if argv is None else argv
    refresh_before_read(ledger)
    before = snapshot([ledger])
    reason = ' '.join(argv).strip()[:80] or 'update'
    try:
        main_fn()
    finally:
        res = sync_after_mutation(ledger, f'{ledger}: {reason}', before=before)
        if res and not res.get('skipped'):
            sys.stderr.write(format_result(ledger, reason, res) + '\n')

def _do_sync(ledger, reason, verbose=False, do_push=True):
    # Before anything is rendered, committed or pushed: did this run make a live
    # record disappear? Checked here rather than inside `commit_and_push` so a
    # refusal leaves the tree exactly as the CLI left it -- file on disk intact,
    # nothing staged, nothing pushed -- and the run can simply be repeated.
    for name in ([ledger] if ledger and ledger != 'all' else list(LEDGERS)):
        if report_deletions(name, check_deletions(name)):
            return {'committed': False, 'pushed': False, 'commit': None,
                    'error': f'deletion guard blocked the {name} commit',
                    'note': 'nothing committed; the ledger on disk is unchanged',
                    'render_problems': None, 'guard_blocked': True}

    problems = render_derived(verbose=verbose)
    git_result = commit_and_push(reason or (f'{ledger} updated' if ledger else ''),
                                 do_push=do_push)

    hb = load_heartbeat()
    entry = {
        'last_sync_epoch': time.time(),
        'last_sync_wib': now_wib(),
        'reason': reason or None,
        'commit': git_result.get('commit'),
        'pushed': git_result.get('pushed', False),
    }
    for name in ([ledger] if ledger and ledger != 'all' else list(LEDGERS)):
        if name in LEDGERS:
            hb['ledgers'][name] = dict(entry)
    hb['last_render_problems'] = problems or None
    hb['last_error'] = git_result.get('error')
    hb['pending_push'] = bool(git_result.get('committed')
                              and not git_result.get('pushed'))
    hb['last_sync_wib'] = entry['last_sync_wib']
    save_heartbeat(hb)

    # The heartbeat write above dirties journal/state again, so it has to be
    # committed too or the file stays permanently dirty.
    #
    # Whether that is an amend depends on one thing: has the commit already left
    # this machine. Amending an unpushed commit is free. Amending a PUSHED one
    # rewrites history that other clones already have, and the only way to
    # publish the rewrite is a force push -- which is what this used to do, and
    # which loses whatever another session pushed in the meantime. The comment
    # justifying it ("nothing else has landed in between") is not true in this
    # repo: several Claude sessions and seven cron jobs push this branch, and the
    # rejections it produced ("cannot lock ref 'refs/heads/main': is at X but
    # expected Y") were exactly that assumption failing.
    #
    # So: amend only while the commit is still local, otherwise write the
    # heartbeat as its own ordinary commit and fast-forward. One extra commit in
    # the log is a much smaller price than a force push racing other writers.
    #
    # Every commit below carries `--only -- HEARTBEAT`. Without a pathspec, `git
    # commit` publishes whatever happens to be in the index, and the index is not
    # this script's to assume: the owner or a concurrent session may have run `git add`
    # on something and paused to review it. Any ledger CLI firing in that window --
    # one `commitment_ledger.py close`, or any of the seven ledger cron jobs -- would
    # push their half-reviewed file to origin/main under the message
    # 'chore(ledger): sync heartbeat'.
    if git_result.get('committed') and dirty_sync_paths():
        git(['add', '--', HEARTBEAT])
        if git_result.get('pushed'):
            rc, _, err = git(['commit', '--no-verify', '--only', '-m',
                              'chore(ledger): sync heartbeat', '--', HEARTBEAT],
                             timeout=GIT_LOCAL_TIMEOUT)
            if rc == 0:
                rc, _, err = git(['push', 'origin', 'main'], timeout=GIT_NET_TIMEOUT)
                if rc != 0:
                    # Someone landed between the two pushes. Rebase onto them and
                    # try once more; the heartbeat is ours alone, so this cannot
                    # conflict with anything except the generated files that
                    # `rebase_onto_origin` already knows how to resolve.
                    ok, msg = rebase_onto_origin()
                    if ok:
                        rc, _, err = git(['push', 'origin', 'main'],
                                         timeout=GIT_NET_TIMEOUT)
                    else:
                        err = msg
                    if rc != 0:
                        git_result['error'] = f'heartbeat push failed: {err[:160]}'
        else:
            git(['commit', '--no-verify', '--amend', '--no-edit', '--only',
                 '--', HEARTBEAT],
                timeout=GIT_LOCAL_TIMEOUT)

    out = dict(git_result)
    out['render_problems'] = problems
    if verbose:
        print(format_result(ledger, reason, out))
    return out

def format_result(ledger, reason, res):
    bits = []
    if res.get('committed'):
        bits.append(f"committed {res.get('commit') or ''}".strip())
    if res.get('pushed'):
        bits.append('pushed to origin/main')
    if res.get('note'):
        bits.append(res['note'])
    if res.get('error'):
        bits.append(f"ERROR {res['error']}")
    if res.get('render_problems'):
        bits.append('render problems: ' + '; '.join(res['render_problems']))
    tag = f'{ledger}' + (f' ({reason})' if reason else '')
    return f"[ledger_sync] {tag}: " + ('; '.join(bits) or 'nothing to do')

# --------------------------------------------------------------------------
# check / refresh / status
# --------------------------------------------------------------------------

def cmd_check(args):
    """Report drift. Exit 1 when anything is out of sync."""
    issues = []

    dirty = dirty_sync_paths()
    if dirty:
        issues.append(
            f'{len(dirty)} uncommitted change(s) under the ledger paths - other '
            'sessions cannot see them:\n    ' + '\n    '.join(dirty[:12]))

    hb = load_heartbeat()
    if hb.get('pending_push'):
        issues.append('a previous sync committed but could not push; '
                      'origin/main is behind this checkout')
    if hb.get('last_error'):
        issues.append(f"last sync error: {hb['last_error']}")
    if hb.get('last_render_problems'):
        issues.append('renderer problems: '
                      + '; '.join(hb['last_render_problems']))

    if not args.no_fetch and fetch(max_age=DEFAULT_FETCH_MAX_AGE):
        b = behind_count()
        if b > 0:
            rc, out, _ = git(['rev-list', '--count', 'HEAD..origin/main', '--',
                              *SYNC_PATHS], timeout=10)
            state_commits = out.strip() if rc == 0 else '?'
            issues.append(f'origin/main is {b} commit(s) ahead '
                          f'({state_commits} touching the ledgers) - this '
                          'checkout is reading stale records')
        a = ahead_count()
        if a > 0:
            issues.append(f'{a} local commit(s) not pushed')

    if not issues:
        print('[ledger_sync] in sync: ledgers, derived views, and origin/main agree.')
        return 0
    print('[ledger_sync] DRIFT:')
    for i in issues:
        print(f'  - {i}')
    print('\n  Fix: python3 .agent/scripts/ledger_sync.py sync --reason "<what changed>"')
    return 1

def cmd_refresh(args):
    """Read side: make sure this checkout is not about to read stale ledgers."""
    if disabled() or offline():
        print('[ledger_sync] refresh skipped (offline/disabled).')
        return 0
    if not fetch(max_age=args.max_age):
        print('[ledger_sync] could not reach origin; reading local ledgers as-is.')
        return 0
    b = behind_count()
    if b <= 0:
        return 0
    rc, out, _ = git(['rev-list', '--count', 'HEAD..origin/main', '--', *SYNC_PATHS],
                     timeout=10)
    ledger_commits = int(out.strip()) if rc == 0 and out.strip().isdigit() else 0
    if ledger_commits == 0 and not args.always:
        print(f'[ledger_sync] origin/main is {b} commit(s) ahead but none touch '
              'the ledgers; local records are current.')
        return 0
    if dirty_sync_paths():
        print(f'[ledger_sync] STALE: origin/main has {ledger_commits} newer '
              'ledger commit(s), but this checkout has uncommitted ledger '
              'changes, so pulling could conflict.\n'
              '  Run: python3 .agent/scripts/ledger_sync.py sync --reason '
              '"<what changed>"  (then re-read)')
        return 1
    ok, msg = rebase_onto_origin()
    if ok:
        print(f'[ledger_sync] pulled {b} commit(s) from origin/main '
              f'({ledger_commits} touching the ledgers). Records are now current.')
        return 0
    print(f'[ledger_sync] STALE: {msg}. Local ledgers may be behind origin/main.')
    return 1

def cmd_status(args):
    hb = load_heartbeat()
    print('Ledger sync status')
    print('==================')
    for name in LEDGERS:
        e = hb.get('ledgers', {}).get(name)
        if not e:
            print(f'  {name:<14} never synced by this tool')
            continue
        age = time.time() - e.get('last_sync_epoch', 0)
        mark = 'pushed' if e.get('pushed') else 'LOCAL ONLY'
        print(f'  {name:<14} {e.get("last_sync_wib")}  ({age/60:.0f} min ago)  '
              f'{mark}  {e.get("reason") or ""}')
    if hb.get('last_error'):
        print(f'\n  last error: {hb["last_error"]}')
    if hb.get('pending_push'):
        print('  pending push: yes')
    return 0

# --------------------------------------------------------------------------
# background sync: take the sync off the turn's critical path
# --------------------------------------------------------------------------
#
# A sync is propagation, and nothing in the turn that triggered it reads the
# result. Run in the foreground from the Stop hook it cost 6.7 seconds of wall
# clock on every single turn on an idle machine, and far more on a slow link or
# a loaded one: roughly half in the renderers (`render_followup_tracker.py`
# shells out to three ledger CLIs) and half in `git fetch` plus `push`. The
# person who triggered the turn waited for all of it before they could read the
# reply, for work whose result they never see.
#
# So the Stop hook now detaches it. Correctness is unchanged, because every
# guarantee this module makes is enforced inside the child: the ledger locks,
# the deletion guard, the rebase-and-retry push. The only thing given up is
# seeing the result inside the same turn, and `bg_last_result()` hands the
# previous run's outcome to the next turn so a failure still surfaces.
#
# One sync at a time. Two detached children would race on the index and on the
# push, so the child takes an exclusive non-blocking lock and a second one exits
# rather than queueing. Whatever it would have committed is still dirty in the
# tree, so the next turn's sync picks it up.

def _bg_child():
    return os.environ.get('LEDGER_SYNC_BG_CHILD') == '1'


@contextlib.contextmanager
def bg_lock():
    """Exclusive, non-blocking. Yields False when another sync already holds it."""
    try:
        os.makedirs(os.path.dirname(BG_LOCK), exist_ok=True)
        fh = open(BG_LOCK, 'w')
    except OSError:
        yield True          # cannot lock: better to sync than to skip silently
        return
    try:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            try:
                fcntl.flock(fh, fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        fh.close()


def bg_last_result():
    """The last detached sync's final line, so the next turn can see a failure."""
    try:
        with open(BG_LOG, encoding='utf-8') as fh:
            lines = [ln.strip() for ln in fh if ln.strip()]
        return lines[-1] if lines else None
    except OSError:
        return None


def spawn_background_sync(ledger, reason):
    """Re-exec this script detached and return immediately.

    `start_new_session=True` is what makes it outlive the hook: without its own
    session the child is in the hook's process group and dies with it, which
    would turn every sync into a half-written one.
    """
    env = dict(os.environ, LEDGER_SYNC_BG_CHILD='1')
    cmd = [sys.executable, os.path.abspath(__file__), 'sync',
           '--ledger', ledger, '--reason', reason]
    try:
        os.makedirs(os.path.dirname(BG_LOG), exist_ok=True)
        log = open(BG_LOG, 'w')
    except OSError:
        log = subprocess.DEVNULL
    try:
        subprocess.Popen(cmd, cwd=BASE_DIR, env=env, stdin=subprocess.DEVNULL,
                         stdout=log, stderr=subprocess.STDOUT,
                         start_new_session=True)
        return True
    except OSError:
        return False
    finally:
        if log is not subprocess.DEVNULL:
            try:
                log.close()
            except OSError:
                pass


def cmd_sync(args):
    if disabled():
        print('[ledger_sync] skipped: LEDGER_SYNC_DISABLE=1')
        return 0

    # Asked to detach, and not already the detached child: hand off and return.
    if getattr(args, 'background', False) and not _bg_child():
        previous = bg_last_result()
        if spawn_background_sync(args.ledger, args.reason):
            print('[ledger_sync] syncing in the background')
        else:
            res = _do_sync(args.ledger, args.reason, verbose=False,
                           do_push=not args.no_push)
            print(format_result(args.ledger, args.reason, res))
            return 1 if res.get('error') else 0
        if previous and ('error' in previous or 'failed' in previous):
            print(f'[ledger_sync] previous background sync: {previous}')
        return 0

    if _bg_child():
        with bg_lock() as got:
            if not got:
                print('[ledger_sync] another sync is already running; skipped')
                return 0
            res = _do_sync(args.ledger, args.reason, verbose=False,
                           do_push=not args.no_push)
            print(format_result(args.ledger, args.reason, res))
            return 1 if res.get('error') else 0

    res = _do_sync(args.ledger, args.reason, verbose=False,
                   do_push=not args.no_push)
    print(format_result(args.ledger, args.reason, res))
    return 1 if res.get('error') else 0

def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = p.add_subparsers(dest='cmd')

    sp = sub.add_parser('sync', help='render derived views, commit, push')
    sp.add_argument('--ledger', default='all',
                    choices=list(LEDGERS) + ['all'])
    sp.add_argument('--reason', default='', help='one line: what changed')
    sp.add_argument('--no-push', action='store_true')
    sp.add_argument('--background', action='store_true',
                    help='detach and sync in a separate process; returns at once')

    cp = sub.add_parser('check', help='report drift, change nothing')
    cp.add_argument('--no-fetch', action='store_true')

    rp = sub.add_parser('refresh', help='pull newer ledger commits if any')
    rp.add_argument('--max-age', type=float, default=DEFAULT_FETCH_MAX_AGE,
                    help='seconds; skip the fetch if one ran this recently')
    rp.add_argument('--always', action='store_true',
                    help='pull even when no incoming commit touches a ledger')

    sub.add_parser('status', help='when each ledger last synced')

    args = p.parse_args()
    handler = {'sync': cmd_sync, 'check': cmd_check, 'refresh': cmd_refresh,
               'status': cmd_status}.get(args.cmd or 'status')
    if args.cmd is None:
        args = p.parse_args(['status'])
    sys.exit(handler(args))

if __name__ == '__main__':
    main()
