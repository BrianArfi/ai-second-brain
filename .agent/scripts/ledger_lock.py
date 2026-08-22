"""
Cross-process lock for the JSON state ledgers.

Why this exists
---------------
On 3 Aug 2026 three waiting-on records and four commitments vanished. The
ledger writers were not corrupting files: they already write to a temp file
and os.replace() it, which is atomic. The loss came from a plain
read-modify-write race between separate processes:

    harvest  reads 420 items
    sweep    reads 420 items
    harvest  writes 421          <- its new record lands
    sweep    writes its own 421  <- harvest's record is now gone

Atomic replace guarantees the file is never half written. It guarantees
nothing about a second process having read the old contents first. With 19
cron jobs on this repo, 7 of which write ledgers, overlap is a matter of
when rather than whether, and the failure is silent: no error, no corrupt
file, just a record that quietly stops existing.

Usage
-----
Acquire once at process start and hold for the whole run. These are short
CLI invocations, so serialising entire runs is simpler and safer than
trying to bracket each read-modify-write pair:

    from ledger_lock import hold_ledger_lock
    hold_ledger_lock('commitments')     # exits with a clear message on timeout

or as a context manager around a narrower section:

    with ledger_lock('commitments'):
        state = load_state()
        ...
        save_state(state)

The lock is advisory (fcntl.flock), so it only protects processes that ask
for it. Any new writer to journal/state/*.json must call this too.
"""
import contextlib
import errno
import fcntl
import os
import sys
import time

LOCK_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'journal', 'state', '.locks'
)
DEFAULT_TIMEOUT = 60.0
POLL = 0.15

def _lock_path(name):
    os.makedirs(LOCK_DIR, exist_ok=True)
    return os.path.join(LOCK_DIR, f'{name}.lock')

# Locks this PROCESS already holds.
#
# `flock` is per open-file-description, not per process, so opening the same lock
# file a second time in one process and locking it blocks against the first —
# the process deadlocks against itself. That is not hypothetical here: every
# ledger CLI takes its own lock at startup and then calls `ledger_sync`, which
# needs all four to run git safely. Without this set the second acquisition would
# hang until the timeout and then report that "another process" was holding it,
# naming the caller's own pid.
_HELD = set()
# name -> open file handle, for locks taken by `hold_ledger_lock`. The handle has
# to outlive the call: closing it releases the lock.
_HELD_FILES = {}

def held_locks():
    """Names this process currently holds. Read by `ledger_sync` so it can take
    the locks it is missing without re-taking the one its caller already has."""
    return set(_HELD)

@contextlib.contextmanager
def ledger_lock(name, timeout=DEFAULT_TIMEOUT, verbose=False):
    """Exclusive advisory lock named `name`, released on exit.

    Re-entrant within one process: asking for a lock this process already holds
    yields immediately and does NOT release it on exit, because the outer holder
    is still using it.
    """
    if name in _HELD:
        yield
        return
    path = _lock_path(name)
    fh = open(path, 'w')
    start = time.time()
    waited = False
    while True:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError as exc:
            if exc.errno not in (errno.EAGAIN, errno.EACCES):
                fh.close()
                raise
            if time.time() - start > timeout:
                fh.close()
                raise TimeoutError(
                    f"could not acquire the '{name}' ledger lock within "
                    f"{timeout:.0f}s. Another process is still holding it. "
                    f"Lock file: {path}"
                )
            waited = True
            time.sleep(POLL)

    if waited and verbose:
        sys.stderr.write(
            f"[ledger_lock] waited {time.time() - start:.1f}s for '{name}'\n"
        )
    try:
        fh.write(f'{os.getpid()}\n')
        fh.flush()
        _HELD.add(name)
        yield
    finally:
        _HELD.discard(name)
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()

def hold_ledger_lock(name, timeout=DEFAULT_TIMEOUT):
    """
    Acquire for the lifetime of the process. Returns the file handle, which
    the caller should keep referenced; the OS releases the lock on exit.

    Exits with status 75 (EX_TEMPFAIL) on timeout, so a cron wrapper can tell
    "someone else was writing, try later" apart from a real failure.
    """
    if name in _HELD:
        # Already ours. Handing back the live handle keeps the lock held exactly
        # once; re-locking a second fd on the same file would block on ourselves.
        return _HELD_FILES[name]
    path = _lock_path(name)
    fh = open(path, 'w')
    start = time.time()
    while True:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError as exc:
            if exc.errno not in (errno.EAGAIN, errno.EACCES):
                raise
            if time.time() - start > timeout:
                sys.stderr.write(
                    f"[ledger_lock] '{name}' held by another process for more "
                    f"than {timeout:.0f}s, giving up rather than racing it.\n"
                )
                sys.exit(75)
            time.sleep(POLL)
    fh.write(f'{os.getpid()}\n')
    fh.flush()
    # Kept alive for the life of the process: closing the handle drops the lock.
    # Keyed by name so `held_locks()` can answer, and so a second call for the
    # same name returns the same handle instead of deadlocking on itself. This
    # used to be `globals().setdefault('_HELD', []).append(fh)`, an untyped list
    # sharing the name `_HELD` now used for the held-name set.
    _HELD_FILES[name] = fh
    _HELD.add(name)
    return fh
