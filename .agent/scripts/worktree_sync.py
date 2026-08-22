#!/usr/bin/env python3
"""Commit and push everything a session produced, not only the ledgers.

Why this exists: on 11 Aug 2026 seven parallel sessions left 49 uncommitted
paths in this checkout. `ledger_sync.py` had already pushed the four state
ledgers, so `ledger_sync status` reported everything healthy while Dashboard.md,
the Clients/ documents and every journal note written that day existed on one
laptop only. origin/main is the source of truth (CLAUDE.md): until a file is
pushed, no other session, agent, cron job or machine can see it.

ledger_sync.py deliberately stages a fixed SYNC_PATHS allowlist, because a
ledger write has to land as its own reviewable commit. This script covers the
complement -- the rest of the working tree -- at end of turn.

Two things make an unattended blanket commit safe enough to run on every turn:

  * A secret screen. Anything whose filename or contents look like a credential
    is left uncommitted and named in the output, instead of being pushed to a
    remote and then rewritten out of history. .gitignore already covers the
    credentials that exist today; the screen is for the ones added tomorrow.
  * One writer at a time. Sessions run in parallel in this checkout, so the
    whole read-stage-commit-push cycle holds a shared 'git' lock from
    ledger_lock.py. Without it, two Stop hooks race on .git/index.lock and one
    of them silently commits nothing.

Usage:
    worktree_sync.py status                  # what would be committed, no writes
    worktree_sync.py sync                    # commit + push
    worktree_sync.py sync --dry-run          # same report as status
    worktree_sync.py sync --reason "..."     # commit subject tail
    worktree_sync.py sync --no-push          # commit locally only

Kill switches: WORKTREE_SYNC_DISABLE=1 skips everything (exit 0);
LEDGER_SYNC_OFFLINE=1 commits but makes no network call.
"""
import argparse
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(BASE_DIR, '.agent', 'scripts'))

from ledger_lock import ledger_lock                      # noqa: E402
import ledger_sync as LS                                 # noqa: E402

WIB = timezone(timedelta(hours=7))
LOCK_NAME = 'git'
LOCK_TIMEOUT = 90.0

# 5 MiB. Anything larger is a render, an export or a binary dump; those belong
# in an ignore rule or on the artifact host, not in git history.
MAX_BYTES = 5 * 1024 * 1024
CONTENT_SCAN_BYTES = 512 * 1024

# Filename shapes that are credentials essentially every time. Deliberately
# filename-anchored rather than "any path containing the word token", so that
# journal/notes/2026-08-12-jira-token-rotation.md is not quarantined as a leak.
SECRET_FILE_RE = re.compile(r'''(?ix)
      (^|/)\.env(\..+)?$
    | (^|/)\.netrc$
    | (^|/)id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$
    | (^|/)[^/]*credentials?[^/]*\.(json|ya?ml|ini|cfg|env)$
    | (^|/)token[^/]*\.(json|txt|env)$
    | (^|/)[^/]*\.(pem|p12|pfx|key|keystore|jks|token)$
    | (^|/)client_secret[^/]*\.json$
    | (^|/)[^/]*cookie[^/]*$
    | (^|/)service[_-]?account[^/]*\.json$
''')

# Live credential material, matched in file contents. Each pattern requires the
# delimiter that follows a real token, so prose that merely names a scheme
# ("the owner's user token, xoxp") does not trip it.
SECRET_CONTENT_RES = [
    re.compile(r'xox[baprs]-[A-Za-z0-9-]{10,}'),
    re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----'),
    re.compile(r'\bAKIA[0-9A-Z]{16}\b'),
    re.compile(r'\bghp_[A-Za-z0-9]{30,}\b'),
    re.compile(r'\bgithub_pat_[A-Za-z0-9_]{30,}\b'),
    re.compile(r'\bglpat-[A-Za-z0-9_-]{15,}\b'),
    re.compile(r'\bsk-(proj-)?[A-Za-z0-9_-]{30,}\b'),
    re.compile(r'\bAIza[0-9A-Za-z_-]{30,}\b'),
    re.compile(r'"private_key"\s*:\s*"-----BEGIN'),
]

# Regenerable churn. Left uncommitted rather than screened, because these are
# not dangerous, just noise that would put a commit on every turn forever.
NOISE_RE = re.compile(r'''(?ix)
      \.(log|tmp|swp|pyc|pyo|bak)$
    | (^|/)\.DS_Store$
    | ~$
''')

def now_wib():
    return datetime.now(WIB).strftime('%Y-%m-%d %H:%M:%S WIB')

def disabled():
    return os.environ.get('WORKTREE_SYNC_DISABLE', '').strip() not in ('', '0', 'false')

def mid_operation():
    """True while a rebase, merge, cherry-pick or bisect is in flight. Committing
    into one of those turns a recoverable conflict into a tangled history."""
    rc, gitdir, _ = LS.git(['rev-parse', '--git-dir'], timeout=10)
    if rc != 0:
        return False
    root = gitdir if os.path.isabs(gitdir) else os.path.join(BASE_DIR, gitdir)
    markers = ('rebase-merge', 'rebase-apply', 'MERGE_HEAD', 'CHERRY_PICK_HEAD',
               'BISECT_LOG', 'REVERT_HEAD')
    return any(os.path.exists(os.path.join(root, m)) for m in markers)

def git_raw(args, timeout=LS.GIT_LOCAL_TIMEOUT):
    """git, with stdout returned byte-for-byte.

    ledger_sync.git() strips its output, which is right for reading a rev or a
    branch name and wrong here: a porcelain status code is two columns wide and
    the first is a space for the very common "modified, not staged" case, so
    stripping shifts every path one character left and silently drops the first
    letter of the filename.
    """
    try:
        p = subprocess.run(['git', *args], cwd=BASE_DIR, capture_output=True,
                           text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return None, '', str(exc)

def porcelain():
    """Every dirty path in the tree, individual untracked files included.

    -z because filenames here contain spaces (Clients/, inbox/ exports). Records
    are NUL separated; a rename or copy is followed by a second record holding
    the old path, which is consumed and reported alongside the new one.
    """
    rc, out, err = git_raw(['status', '--porcelain=v1', '-z', '--untracked-files=all'])
    if rc != 0:
        return None, f'git status failed: {err[:200]}'
    if not out:
        return [], None

    records = [r for r in out.split('\0') if r]
    entries, i = [], 0
    while i < len(records):
        rec = records[i]
        i += 1
        if len(rec) < 4:
            continue
        code, path = rec[:2], rec[3:]
        if code[0] in ('R', 'C') and i < len(records):
            entries.append((code, path, records[i]))
            i += 1
        else:
            entries.append((code, path, None))
    return entries, None

def content_flags(abspath):
    """Secret markers found in the first CONTENT_SCAN_BYTES, or [] if none."""
    try:
        with open(abspath, 'rb') as fh:
            blob = fh.read(CONTENT_SCAN_BYTES)
    except OSError:
        return []
    if b'\0' in blob:                       # binary: no useful text scan
        return []
    text = blob.decode('utf-8', 'replace')
    return [r.pattern for r in SECRET_CONTENT_RES if r.search(text)]

def classify(entries):
    """Split the dirty tree into what to commit and what to leave, with a reason
    for every exclusion. Nothing is dropped silently."""
    staged, skipped = [], []
    for code, path, oldpath in entries:
        deleted = 'D' in code
        abspath = os.path.join(BASE_DIR, path)

        if NOISE_RE.search(path):
            skipped.append((path, 'transient/regenerable'))
            continue
        if SECRET_FILE_RE.search(path):
            skipped.append((path, 'SECRET: filename looks like a credential'))
            continue

        # A deletion has no contents left to screen, and removing a file is
        # never the leak. Everything else gets read.
        if not deleted and os.path.isfile(abspath):
            try:
                if os.path.getsize(abspath) > MAX_BYTES:
                    mb = os.path.getsize(abspath) / (1024 * 1024)
                    skipped.append((path, f'{mb:.1f} MB, over the {MAX_BYTES // (1024 * 1024)} MB cap'))
                    continue
            except OSError:
                pass
            hits = content_flags(abspath)
            if hits:
                skipped.append((path, f'SECRET: contents match {hits[0]}'))
                continue

        staged.append(path)
        if oldpath:
            staged.append(oldpath)
    return staged, skipped

# A Dashboard day heading: "## ☀️ August 18 (Selasa, Pagi): <narrative>".
# Only the date and the phase identify the section. The headline after the colon
# is LLM-written Indonesian prose, ~200 characters and different every time, so
# matching on the whole heading finds nothing: two sessions writing the same
# morning would never produce byte-identical titles. That is why an earlier
# version of this check, which compared full heading strings, would have caught
# nothing at all.
DAY_SECTION_RE = re.compile(
    r'^##\s+\S*\s*(January|February|March|April|May|June|July|August|September|'
    r'October|November|December)\s+(\d{1,2})\b.*?\((?:[^,)]+),\s*(Pagi|Malam|Sore|Siang)\)',
    re.MULTILINE)

# Files where a repeated day section means two sessions appended instead of one
# replacing. Dashboard.md is the one that has actually happened.
DAY_SECTION_FILES = ('Dashboard.md',)

def duplicate_day_sections(paths):
    """Day sections appearing more than once in a file about to be committed.

    This is the half of the concurrency problem no lock can see. `git.lock`
    already serialises the commit itself, and on 18 Aug it did so perfectly:
    two Stop hooks 84 seconds apart, one atomic commit, and the duplicate rode
    along inside it. Serialising the commit says nothing about two sessions
    having already written the same section into the working tree.

    Checked here rather than in a PreToolUse hook on Write|Edit because every
    writer converges on this function whatever produced the change -- MultiEdit,
    a Bash heredoc, a python3 script, or another machine's commit arriving by
    pull. A tool-level hook sees two of those paths and misses the rest.

    Warns, never blocks. Under `defaultMode: bypassPermissions` a deny would not
    reach the owner anyway, and refusing this commit would strand the whole turn's
    work, which is the exact failure this script exists to prevent.
    """
    dupes = []
    for path in paths:
        if os.path.basename(path) not in DAY_SECTION_FILES:
            continue
        try:
            with open(os.path.join(BASE_DIR, path), 'r', encoding='utf-8') as fh:
                text = fh.read()
        except OSError:
            continue
        seen = {}
        for month, day, phase in DAY_SECTION_RE.findall(text):
            seen[(month, day, phase)] = seen.get((month, day, phase), 0) + 1
        for (month, day, phase), n in sorted(seen.items()):
            if n > 1:
                dupes.append((path, f'{month} {day} ({phase})', n))
    return dupes

def areas(paths, limit=3):
    """Top-level areas touched, for the commit subject."""
    tops = []
    for p in paths:
        top = p.split('/', 1)[0] if '/' in p else p
        if top not in tops:
            tops.append(top)
    if len(tops) <= limit:
        return ', '.join(tops)
    return ', '.join(tops[:limit]) + f' +{len(tops) - limit} more'

def git_mutate(args, attempts=4, delay=0.6):
    """A staging or commit call, retried while .git/index.lock is held.

    The 'git' lock above serialises worktree_sync against itself, but not against
    ledger_sync.py, which commits journal/state from the same Stop hook chain and
    from every ledger CLI. Two gits touching one index is a transient failure, not
    a real one, so back off and try again rather than dropping the turn's work.
    """
    last = ''
    for attempt in range(attempts):
        rc, out, err = LS.git(args)
        if rc == 0:
            return rc, out, err
        last = err
        if 'index.lock' not in (err or ''):
            return rc, out, err
        time.sleep(delay * (attempt + 1))
    return 1, '', last

def add_paths(paths, chunk=150):
    """Stage in chunks so a long list cannot blow the argv limit. -A so that
    deletions stage as deletions.

    `-f` is load-bearing, and it is safe here for one specific reason: every path
    in this list came from `git status --porcelain --untracked-files=all`, which
    never reports an ignored file unless it is already tracked. So nothing can
    reach this function that git was right to refuse.

    Without it, a tracked file living under a directory that matches a
    `.gitignore` rule -- `meeting-recorder/scratch/prompt_draft.txt` under the
    `scratch/` rule is the live example -- makes `git add` exit 1 while STILL
    staging the file correctly. The non-zero was read as a failure, so the whole
    sync aborted and committed nothing, abandoning every other path in the run.
    That is the same "work exists on one laptop only" failure this script was
    written to prevent, arriving through the script itself: the Stop hook does not
    surface the error, so it failed silently every time a meeting recorder had
    written scratch files, which is every day there is a meeting.
    """
    for i in range(0, len(paths), chunk):
        rc, _, err = git_mutate(['add', '-A', '-f', '--', *paths[i:i + chunk]])
        if rc != 0:
            return f'git add failed: {err[:200]}'
    return None

def do_sync(reason='', dry_run=False, do_push=True, verbose=True):
    """Returns a result dict; never raises."""
    res = {'committed': False, 'pushed': False, 'commit': None, 'staged': [],
           'skipped': [], 'note': None, 'error': None}

    rc, _, _ = LS.git(['rev-parse', '--git-dir'], timeout=10)
    if rc != 0:
        res['error'] = 'not a git repo'
        return res
    if not LS.on_main():
        res['note'] = 'not on main; left uncommitted'
        return res
    if mid_operation():
        res['note'] = 'rebase/merge in progress; left uncommitted'
        return res

    entries, err = porcelain()
    if err:
        res['error'] = err
        return res

    staged, skipped = classify(entries or [])
    res['staged'], res['skipped'] = staged, skipped
    res['duplicate_sections'] = duplicate_day_sections(staged)

    if not staged:
        res['note'] = 'nothing to commit'
        # A previous turn may have committed without reaching origin.
        if do_push and not dry_run and not LS.offline() and LS.fetch(max_age=0.0):
            if LS.ahead_count() > 0:
                res.update(LS._push())
        return res

    if dry_run:
        res['note'] = f'dry run: {len(staged)} path(s) would be committed'
        return res

    err = add_paths(staged)
    if err:
        res['error'] = err
        return res

    # -z again: without it git quotes and escapes any path holding a space, and
    # the commit body would then list a mangled filename.
    rc, out, _ = git_raw(['diff', '--cached', '--name-only', '-z'])
    actually = [p for p in out.split('\0') if p] if rc == 0 else []
    if not actually:
        res['note'] = 'nothing staged'
        return res

    tail = reason.strip() if reason else f'from {areas(actually)}'
    subject = f'chore(worktree): sync {len(actually)} file(s) {tail}'[:100]
    listing = '\n'.join(f'  {p}' for p in actually[:40])
    if len(actually) > 40:
        listing += f'\n  ... and {len(actually) - 40} more'
    body = (
        f'End-of-turn working-tree sync at {now_wib()}.\n\n'
        f'{listing}\n\n'
        'Auto-committed by .agent/scripts/worktree_sync.py. origin/main is the\n'
        'source of truth: work that stays in one checkout is invisible to every\n'
        'other session, to cron, and to the other machine.'
    )
    if skipped:
        body += '\n\nLeft uncommitted on purpose:\n' + '\n'.join(
            f'  {p} -- {why}' for p, why in skipped[:20])

    rc, _, err = git_mutate(['commit', '--no-verify', '-m', subject, '-m', body])
    if rc != 0:
        res['error'] = f'git commit failed: {err[:200]}'
        return res
    res['committed'] = True
    rc, out, _ = LS.git(['rev-parse', '--short', 'HEAD'], timeout=10)
    res['commit'] = out.strip() if rc == 0 else None

    if not do_push or LS.offline():
        res['note'] = 'push skipped'
        return res
    res.update(LS._push())
    return res

def sync(reason='', dry_run=False, do_push=True):
    """do_sync under the shared git lock. A timeout is not an error: another
    session holds the lock and is committing the same tree."""
    if disabled():
        return {'committed': False, 'pushed': False, 'staged': [], 'skipped': [],
                'note': 'WORKTREE_SYNC_DISABLE set', 'error': None, 'commit': None}
    try:
        with ledger_lock(LOCK_NAME, timeout=LOCK_TIMEOUT):
            return do_sync(reason=reason, dry_run=dry_run, do_push=do_push)
    except TimeoutError:
        return {'committed': False, 'pushed': False, 'staged': [], 'skipped': [],
                'note': 'another session holds the git lock; it will carry this tree',
                'error': None, 'commit': None}

def render(res, verbose=False):
    lines = []
    if res.get('error'):
        lines.append(f'worktree_sync: ERROR {res["error"]}')
    if res.get('committed'):
        where = 'pushed' if res.get('pushed') else 'local only'
        lines.append(f'worktree_sync: committed {res["commit"]} '
                     f'({len(res["staged"])} path(s), {where})')
    elif res.get('note'):
        lines.append(f'worktree_sync: {res["note"]}')
    if res.get('pushed') and not res.get('committed'):
        lines.append('worktree_sync: pushed an earlier local commit')

    for path, section, n in res.get('duplicate_sections', []):
        lines.append(f'worktree_sync: {path} has {n} sections for {section}. '
                     'Two writers appended instead of one replacing; merge them '
                     'by hand, then re-run.')

    secrets = [(p, w) for p, w in res.get('skipped', []) if w.startswith('SECRET')]
    if secrets:
        lines.append(f'worktree_sync: {len(secrets)} path(s) QUARANTINED, review by hand:')
        lines += [f'  {p} -- {w}' for p, w in secrets]
    other = [(p, w) for p, w in res.get('skipped', []) if not w.startswith('SECRET')]
    if other and verbose:
        lines.append(f'worktree_sync: {len(other)} path(s) skipped as noise/oversize:')
        lines += [f'  {p} -- {w}' for p, w in other]
    return '\n'.join(lines)

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd')

    p_status = sub.add_parser('status', help='report what would be committed')
    p_status.add_argument('-v', '--verbose', action='store_true')

    p_sync = sub.add_parser('sync', help='commit and push the working tree')
    p_sync.add_argument('--reason', default='', help='commit subject tail')
    p_sync.add_argument('--dry-run', action='store_true')
    p_sync.add_argument('--no-push', action='store_true')
    p_sync.add_argument('-v', '--verbose', action='store_true')

    args = ap.parse_args()
    if args.cmd == 'status' or args.cmd is None:
        res = sync(dry_run=True)
        verbose = getattr(args, 'verbose', True) or True
        out = render(res, verbose=verbose)
        print(out or 'worktree_sync: clean')
        if res.get('staged'):
            print(f'\nWould commit {len(res["staged"])} path(s):')
            for p in res['staged'][:60]:
                print(f'  {p}')
            if len(res['staged']) > 60:
                print(f'  ... and {len(res["staged"]) - 60} more')
        return 0

    res = sync(reason=args.reason, dry_run=args.dry_run, do_push=not args.no_push)
    out = render(res, verbose=args.verbose)
    print(out or 'worktree_sync: clean')
    return 1 if res.get('error') else 0

if __name__ == '__main__':
    sys.exit(main())
