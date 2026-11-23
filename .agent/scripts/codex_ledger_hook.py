#!/usr/bin/env python3
"""Run the harness ledger checks without automatic Git propagation."""

import json
from pathlib import Path
import sys
from io import StringIO

HOOKS = Path(__file__).resolve().parents[2] / ".claude" / "hooks"
sys.path.insert(0, str(HOOKS))

import ledger_guard
import ledger_watch

def main():
    raw = sys.stdin.read()
    event = json.loads(raw)
    if not event.get("session_id"):
        return
    sys.stdin = StringIO(raw)
    if event.get("hook_event_name") == "PostToolUse":
        ledger_watch.main(codex=True)
    elif event.get("hook_event_name") == "Stop":
        ledger_guard.main(propagate=False)

if __name__ == "__main__":
    main()
