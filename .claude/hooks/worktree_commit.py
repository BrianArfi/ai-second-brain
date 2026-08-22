#!/usr/bin/env python3
"""Stop hook: no turn ends with its work sitting in one checkout only.

`ledger_guard.py` already syncs journal/state and the generated tracker at end of
turn. This covers the rest of the tree -- Dashboard.md, the Clients/ documents,
journal notes, journal/memory, the harness itself -- which until now waited for
somebody to remember to commit. On 11 Aug 2026 nobody did, and seven parallel
sessions left 49 uncommitted paths behind while every ledger reported healthy.

Never blocks. A commit failing is not a reason to hold up the turn, so this
reports and exits 0 whatever happens. Credentials are screened out by
worktree_sync.py and named in the output instead of being pushed.

Off switch: WORKTREE_SYNC_DISABLE=1 in the environment, or remove the hook entry
from .claude/settings.json.
"""
import os
import pathlib
import subprocess
import sys

def project_dir():
    """CLAUDE_PROJECT_DIR when it is real, else derived from this file's location.
    Matching ledger_guard.py: a hardcoded WSL default silently disables the hook
    on the macOS checkout, which is exactly where uncommitted work piles up."""
    env = os.environ.get('CLAUDE_PROJECT_DIR')
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    return str(pathlib.Path(__file__).resolve().parent.parent.parent)

def main():
    root = project_dir()
    script = os.path.join(root, '.agent', 'scripts', 'worktree_sync.py')
    if not os.path.isfile(script):
        return 0
    try:
        p = subprocess.run(
            ['python3', script, 'sync', '--reason', 'end of session turn'],
            cwd=root, capture_output=True, text=True, timeout=110)
    except (subprocess.TimeoutExpired, OSError) as exc:
        print(f'worktree_commit: skipped ({exc})')
        return 0

    out = (p.stdout or '').strip()
    err = (p.stderr or '').strip()
    # "nothing to commit" is the normal case on a read-only turn and should not
    # add a line to every single turn's output.
    if out and 'nothing to commit' not in out:
        print(out)
    elif err:
        print(f'worktree_commit: {err.splitlines()[-1][:200]}')
    return 0

if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:                       # never break the turn
        print(f'worktree_commit: internal error, ignored ({exc})')
        sys.exit(0)
