#!/usr/bin/env python3
"""SessionStart hook: hand this session the notes other sessions left for it.

Prints up to 5 undelivered notes addressed to this session id or to a work-tree node
on this session's card (see .agent/scripts/session_notes.py), then marks them
delivered. Silent when there are none. Replaces the old habit of checking `git log`
and `ListAgents` before a handoff. Always exits 0.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / ".agent" / "scripts"))

MAX_NOTES = 5

def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        payload = {}
    session = str(payload.get("session_id") or "")
    import session_coord_common as C
    import session_notes as N
    cards = C.load_cards()
    card = C.find_card(session, cards) if session else None
    nodes = list((card or {}).get("nodes") or [])
    items = N.inbox(session, nodes)[:MAX_NOTES]
    if not items:
        return
    print("Notes from other sessions (%d):" % len(items))
    for _, note in items:
        print("- " + N.format_note(note, cards))
    N.mark_delivered([p for p, _ in items])

if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
