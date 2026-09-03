#!/usr/bin/env python3
"""Stop hook: nothing tracked leaves this turn un-recorded or un-propagated.

Two checks, in this order.

1. PROPAGATE. If anything under `journal/state/` or the generated tracker is
   still uncommitted at the end of the turn, sync it: re-render the derived
   views, commit, push. A ledger change that only exists in this working tree is
   invisible to every other session and to all 19 cron jobs, and the next sweep
   that writes the same file can silently drop it. This runs automatically and
   does not interrupt anyone.

2. RECORD. `ledger_watch.py` logged the actions taken this turn that normally
   discharge a tracked item - a Slack message sent, a doc published, a ticket
   transitioned. If one of those happened and no ledger command followed it, the
   record still says open. That is the failure this whole mechanism exists to
   prevent, so it blocks once and names the specific action, then lets the turn
   end whatever happens next (`stop_hook_active` guards against a loop).

Contract: exit 0 always; blocking is expressed in the JSON, never by exit code.
"""
import json
import os
import pathlib
import re
import subprocess
import sys

SESSION_DIR = os.path.join(".claude", ".ledger_session")

def project_dir():
    """CLAUDE_PROJECT_DIR when it is set and real, otherwise derived from this
    file's own location (two levels up from .claude/hooks/). The hardcoded WSL
    default other hooks use silently disables them on the macOS checkout, which
    is exactly where a stale ledger does the most damage."""
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    return str(pathlib.Path(__file__).resolve().parent.parent.parent)

def session_path(project, session_id):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "unknown")[:80]
    return os.path.join(project, SESSION_DIR, f"{safe}.json")

def run(cmd, cwd, timeout=90):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except Exception:
        return None, "", ""

def dirty(project):
    rc, out, _ = run(["git", "status", "--porcelain", "--",
                      "journal/state", "journal/master_followup_tracker.md"],
                     project, timeout=20)
    if rc != 0 or not out:
        return []
    return [ln for ln in out.splitlines() if ln.strip()]

def main():
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    except Exception:
        sys.exit(0)
    try:
        d = json.loads(raw) if raw else {}
    except Exception:
        d = {}

    project = project_dir()
    sync = os.path.join(project, ".agent", "scripts", "ledger_sync.py")
    messages = []

    # --- 1. propagate anything still sitting in the working tree -----------
    if os.path.exists(sync) and dirty(project):
        # --background: the sync detaches and this returns in well under a
        # second. Run inline it cost 6.7 seconds on EVERY turn on an idle
        # machine, and far more on a slow link (renderers plus git fetch/push),
        # and nothing in the turn reads the result, so the user was waiting on
        # propagation for its own sake. The locks, the deletion guard and the
        # rebase-and-retry push all still run, inside the child. A failure
        # surfaces on the next turn instead of this one; ledger_sync.py replays
        # the last background result to say so.
        rc, out, err = run(
            ["python3", sync, "sync", "--background",
             "--reason", "end-of-turn ledger sync"],
            project, timeout=20)
        line = out or err
        if line:
            messages.append(line.splitlines()[-1])

    # --- 2. did an action happen with no record behind it? ----------------
    spath = session_path(project, str(d.get("session_id") or ""))
    try:
        with open(spath, encoding="utf-8") as fh:
            state = json.load(fh)
    except Exception:
        state = {}

    discharges = state.get("discharges") or []
    touches = state.get("ledger_touches") or []
    last_touch = max((t.get("at", 0) for t in touches), default=0)
    unrecorded = [x for x in discharges if x.get("at", 0) > last_touch]

    # Consume either way: the reminder is per-turn, not a standing backlog.
    if discharges or touches:
        state["discharges"] = []
        state["ledger_touches"] = []
        try:
            with open(spath, "w", encoding="utf-8") as fh:
                json.dump(state, fh, ensure_ascii=False, indent=1)
        except Exception:
            pass

    if unrecorded and not d.get("stop_hook_active"):
        items = "\n".join(
            f"  - {x.get('what')}: {str(x.get('cmd'))[:120]}" for x in unrecorded[:6]
        )
        reason = (
            "Tracked work happened this turn with no ledger record behind it:\n"
            f"{items}\n\n"
            "Every other session and all 19 cron jobs read the ledgers, not this "
            "conversation. Until the record changes, the item keeps reporting as "
            "open and someone chases it again.\n\n"
            "Close the loop now, then finish:\n"
            "  - the owner owed it   -> commitment_ledger.py close COM-xxxx --note \"...\"\n"
            "  - someone owes it -> waiting_watchdog.py close WAIT-xxxx\n"
            "  - a call was made -> decision_log.py decide DEC-xxxx --decision \"...\"\n"
            "  - new obligation created by this action -> ...add\n"
            "  - tick the matching journal/todo.md line\n"
            "The CLIs re-render the tracker and push automatically.\n\n"
            "If none of these map to a tracked item, say so in one line and stop."
        )
        if messages:
            reason += "\n\n" + "\n".join(messages)
        print(json.dumps({"decision": "block", "reason": reason},
                         ensure_ascii=False))
        sys.exit(0)

    if messages:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "Stop",
                "additionalContext": "=== Ledger sync ===\n" + "\n".join(messages),
            }
        }, ensure_ascii=False))
    sys.exit(0)

if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
