#!/usr/bin/env python3
"""Stop hook: write or update this session's card at the end of every turn.

A card is one small JSON record (id, spoken name, purpose, status, outputs, ledger ids,
open items) so other sessions, on any machine, can see what this one is doing. It lives in
journal/sessions/ (git tracked) and is built from the transcript with plain rules: no model
call, stdlib only, well under a second. Library and CLI: .agent/scripts/session_cards.py.

Runs before worktree_commit.py so the card is committed with the rest of the turn.
Off switch: SESSION_CARD_DISABLE=1. Contract: never block, never fail the turn, exit 0 always.
"""
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

def main():
    if os.environ.get("SESSION_CARD_DISABLE") == "1":
        return
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        payload = {}
    sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
    import session_cards as sc

    cwd = payload.get("cwd")
    ws = os.environ.get("CLAUDE_PROJECT_DIR") or (cwd if cwd and os.path.isdir(cwd) else None) or str(ROOT)
    found = sc.current_transcript(payload.get("session_id"), payload.get("transcript_path"))
    if not found:
        return
    sid, transcript = found
    sc.update_from_transcript(transcript, ws, sid)

if __name__ == "__main__":
    try:
        main()
    except BaseException:  # a card must never cost a turn
        pass
    sys.exit(0)
