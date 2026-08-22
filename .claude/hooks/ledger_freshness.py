#!/usr/bin/env python3
"""PreToolUse hook on Bash: never read a ledger that origin/main has already moved past.

Why this exists: several Claude sessions and 19 cron jobs read the same four
JSON ledgers out of the same repo. The SessionStart git sync pulls once, at
session start. A session that has been open for two hours is reading two hours
of other people's writes late, and nothing says so - the file is there, it
parses, it just holds the previous values. That is how a commitment someone
closed this morning gets chased again in the afternoon.

So: before any command that reads or writes a ledger, make sure this checkout is
current. The fetch is throttled (once per 45s at most) so a burst of ledger
commands costs one network round trip, not twenty.

Warning-only when pulling would be unsafe (uncommitted ledger changes in this
tree); it says exactly what to run instead. Never blocks the command.

Contract: always exit 0.
"""
import json
import os
import pathlib
import subprocess
import sys

# Commands worth checking freshness for: anything that touches a ledger, the
# generated tracker, or the state directory.
TRIGGERS = (
    "commitment_ledger.py",
    "waiting_watchdog.py",
    "decision_log.py",
    "chase_queue.py",
    "render_followup_tracker.py",
    "state_index.py",
    "journal/state/",
    "master_followup_tracker",
)

# ledger_sync handles its own freshness; re-entering here would fetch twice.
SKIP = ("ledger_sync.py",)

def project_dir():
    """CLAUDE_PROJECT_DIR when it is set and real, otherwise derived from this
    file's own location (two levels up from .claude/hooks/). The hardcoded WSL
    default other hooks use silently disables them on the macOS checkout, which
    is exactly where a stale ledger does the most damage."""
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    return str(pathlib.Path(__file__).resolve().parent.parent.parent)

def main():
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    except Exception:
        sys.exit(0)
    if not raw:
        sys.exit(0)

    try:
        d = json.loads(raw)
    except Exception:
        sys.exit(0)

    cmd = str((d.get("tool_input") or {}).get("command") or "")
    if not cmd:
        sys.exit(0)
    if any(s in cmd for s in SKIP):
        sys.exit(0)
    if not any(t in cmd for t in TRIGGERS):
        sys.exit(0)

    project = project_dir()
    script = os.path.join(project, ".agent", "scripts", "ledger_sync.py")
    if not os.path.exists(script):
        sys.exit(0)

    try:
        p = subprocess.run(
            ["python3", script, "refresh"],
            cwd=project, capture_output=True, text=True, timeout=40,
        )
    except Exception:
        sys.exit(0)

    out = (p.stdout or "").strip()
    if not out:
        sys.exit(0)

    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": out,
            "additionalContext": (
                "=== Ledger freshness ===\n" + out
                + ("\n\nThis checkout was behind. Re-read any ledger output you "
                   "already collected this turn before acting on it."
                   if p.returncode != 0 or "pulled" in out else "")
            ),
        }
    }
    print(json.dumps(payload, ensure_ascii=False))
    sys.exit(0)

if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
