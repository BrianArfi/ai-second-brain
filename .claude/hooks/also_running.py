#!/usr/bin/env python3
"""SessionStart hook: tell this session which other sessions are running.

Prints at most five lines, "Also running: <short_id> <spoken_name> - <status> - <purpose>",
for session cards active in the last 24 hours on any machine (cards come through git),
this session excluded, then one hint line. Injected once at start so the prompt prefix stays
stable for the whole session. Nothing running: prints '{}'.

It also starts `session_cards.py refine --max 5` detached in the background, so cards with a raw
first-prompt purpose get a better purpose and name over time. The launch prints nothing, waits for
nothing, and is skipped when a stamp file (journal/sessions/.refine_stamp, gitignored) is younger
than 30 minutes. Off switch: SESSION_REFINE_DISABLE=1. A refine child (ASB_REFINE=1) never starts another.

Contract: never break a session. On any error print '{}' and exit 0.
"""
import json
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
HINT = "Find any session: python3 .agent/scripts/session_cards.py list | /session <id>"
REFINE_EVERY_SECONDS = 30 * 60
REFINE_MAX = "5"

def launch_refine(sc, ws, now=None):
    """Start `session_cards.py refine --max 5` in the background unless one started in the last 30 minutes.
    Returns True when a process was started. Never raises, never prints, never waits."""
    try:
        if os.environ.get("SESSION_REFINE_DISABLE") == "1" or os.environ.get("ASB_REFINE") == "1":
            return False
        cards = sc.card_dir(ws)
        if not cards.is_dir():
            return False
        stamp = cards / ".refine_stamp"
        now = time.time() if now is None else now
        try:
            if now - stamp.stat().st_mtime < REFINE_EVERY_SECONDS:
                return False
        except OSError:
            pass
        stamp.write_text(str(int(now)) + "\n", encoding="utf-8")      # stamp first: two starts cannot both launch
        cmd = [sys.executable, str(ROOT / ".agent" / "scripts" / "session_cards.py"),
               "--workspace", str(ws), "refine", "--max", REFINE_MAX]
        kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
              "cwd": str(ws)}
        if os.name == "nt":
            # NEW_PROCESS_GROUP | NO_WINDOW. No DETACHED_PROCESS: a detached child has no console, so every
            # console program it starts (claude.exe) opens a visible window. NO_WINDOW gives it a hidden one.
            kw["creationflags"] = 0x00000200 | 0x08000000
        else:
            kw["start_new_session"] = True
        subprocess.Popen(cmd, **kw)
        return True
    except BaseException:
        return False

def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        payload = {}
    sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
    import session_cards as sc

    env = os.environ.get("CLAUDE_PROJECT_DIR")
    ws = env if env and os.path.isdir(env) else str(ROOT)
    me = payload.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID")
    lines = sc.active_brief(sc.load_cards(ws), limit=5, exclude=me)
    launch_refine(sc, ws)
    if not lines:
        print("{}")
        return
    text = "\n".join(f"Also running: {ln}" for ln in lines) + "\n" + HINT
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}))

if __name__ == "__main__":
    try:
        main()
    except BaseException:
        print("{}")
    sys.exit(0)
