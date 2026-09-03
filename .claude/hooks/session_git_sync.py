#!/usr/bin/env python3
"""SessionStart hook: sync repo with GitHub (fetch + rebase pull when safe).

Cross-platform replacement for session_git_sync.sh (which required bash + POSIX
`timeout`). Calls git directly via subprocess with argument lists (no shell=True),
each call bounded by its own timeout, and degrades silently if git is missing.

Contract: always exits 0 and emits hook JSON; never blocks the session.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

GIT_TIMEOUT = 15

def repo_dir():
    # Matches session_git_sync.sh: REPO_DIR is derived from the script's own
    # location (two levels up from .claude/hooks/), NOT from CLAUDE_PROJECT_DIR
    # -- the bash version never reads that env var.
    return Path(__file__).resolve().parent.parent.parent

def run_git(args, cwd, timeout=GIT_TIMEOUT):
    """Run git with an arg list. Returns (returncode, stdout, stderr).
    On FileNotFoundError/timeout, returns (None, "", "")."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None, "", ""

def emit(ctx, sysmsg=None):
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": f"=== Git sync (origin/main) ===\n{ctx}",
        }
    }
    if sysmsg:
        payload["systemMessage"] = sysmsg
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    sys.exit(0)

def main():
    repo = repo_dir()

    # git present at all?
    rc, _, _ = run_git(["--version"], repo, timeout=5)
    if rc is None:
        emit("git not found on this machine — skipped.")

    rc, _, _ = run_git(["rev-parse", "--git-dir"], repo, timeout=5)
    if rc is None or rc != 0:
        emit(f"Not a git repo at {repo} — skipped.")

    rc, out, _ = run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo, timeout=5)
    branch = out.strip() if rc == 0 else ""
    if branch != "main":
        emit(f"On branch '{branch}' (not main) — auto-sync skipped.")

    # Rebase/merge already in progress? Don't touch anything.
    rc, out, _ = run_git(["rev-parse", "--git-dir"], repo, timeout=5)
    gitdir_str = out.strip() if rc == 0 else ".git"
    gitdir = Path(gitdir_str)
    if not gitdir.is_absolute():
        gitdir = repo / gitdir
    if (gitdir / "rebase-merge").is_dir() or (gitdir / "rebase-apply").is_dir() or (gitdir / "MERGE_HEAD").is_file():
        # A rebase state dir older than 5 minutes belongs to a process that is
        # gone (2 Sep 2026: a hook was killed mid `rebase --autostash` and every
        # sync for the next hour declined with "not on main"). Abort it and
        # carry on; a LIVE rebase is younger than that and still gets the skip.
        import time as _time
        stale = False
        for d in (gitdir / "rebase-merge", gitdir / "rebase-apply"):
            try:
                if d.is_dir() and _time.time() - d.stat().st_mtime > 300:
                    stale = True
            except OSError:
                pass
        if stale and not (gitdir / "MERGE_HEAD").is_file():
            rc, _, _ = run_git(["rebase", "--abort"], repo, timeout=15)
            if rc == 0:
                print("Cleared a stuck rebase left by an earlier run; continuing sync.")
            else:
                emit(
                    "⚠ A stuck rebase could not be aborted — auto-sync skipped. Run: git rebase --abort",
                    "Git sync: stuck rebase, not synced",
                )
        else:
            emit(
                "⚠ A rebase/merge is in progress — auto-sync skipped. Resolve it first.",
                "Git sync: rebase/merge in progress, not synced",
            )

    rc, _, _ = run_git(["fetch", "origin", "main", "--quiet"], repo, timeout=20)
    if rc is None or rc != 0:
        emit(
            "⚠ git fetch failed (offline or GitHub unreachable) — repo may be stale.",
            "Git sync: fetch failed (offline?)",
        )

    rc, out, _ = run_git(["rev-list", "--count", "HEAD..origin/main"], repo, timeout=10)
    try:
        behind = int(out.strip()) if rc == 0 else 0
    except ValueError:
        behind = 0

    rc, out, _ = run_git(["rev-list", "--count", "origin/main..HEAD"], repo, timeout=10)
    try:
        ahead = int(out.strip()) if rc == 0 else 0
    except ValueError:
        ahead = 0

    rc, out, _ = run_git(["status", "--porcelain", "--untracked-files=no"], repo, timeout=10)
    # rstrip only (not strip): porcelain status lines carry meaningful leading
    # spaces (e.g. " M file.txt"), matching bash's command substitution which
    # only trims trailing newlines, never leading whitespace.
    dirty = out.rstrip("\n") if rc == 0 else ""

    if behind == 0:
        if ahead > 0:
            emit(
                f"✓ Up to date with origin/main, but {ahead} local commit(s) not pushed. Consider pushing.",
                f"Git sync: {ahead} unpushed commit(s) on main",
            )
        emit("✓ Up to date with origin/main.")

    if dirty:
        emit(
            f"⚠ origin/main is {behind} commit(s) ahead, but the working tree has uncommitted changes — "
            f"NOT auto-pulled.\nSync manually: commit or stash, then 'git pull --rebase origin main'.\n"
            f"Dirty files:\n{dirty}",
            f"Git sync: {behind} commit(s) behind origin/main — manual sync needed (uncommitted changes)",
        )

    rc, _, _ = run_git(["pull", "--rebase", "origin", "main", "--quiet"], repo, timeout=30)
    if rc == 0:
        rc2, out, _ = run_git(["log", "--oneline", f"-{behind}"], repo, timeout=10)
        pulled = out.strip() if rc2 == 0 else ""
        emit(
            f"✓ Pulled {behind} commit(s) from origin/main (rebase):\n{pulled}",
            f"Git sync: pulled {behind} commit(s) from origin/main",
        )
    else:
        run_git(["rebase", "--abort"], repo, timeout=10)
        emit(
            f"⚠ Pull --rebase hit conflicts ({behind} commit(s) behind, {ahead} ahead) — rebase aborted, "
            f"repo left untouched.\nResolve manually: 'git pull --rebase origin main' and fix conflicts.",
            "Git sync: rebase conflict — manual sync needed",
        )

if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
