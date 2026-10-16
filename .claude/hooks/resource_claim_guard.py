#!/usr/bin/env python3
"""PreToolUse hook on Bash: warn when another live session holds the Google Doc.

Writers that change an existing Doc/Drive file in place:
  gdoc_surgical.py <read-modify command> --id DOC     (replace, append, insert-row, ...)
  gdrive_manager.py update|rename|delete --id DOC     (work, personal, secondary connectors)
  gdocs_writer.py update --id DOC

If another live session holds `gdoc:<DOC>` (see .agent/scripts/claims.py), the hook warns
via additionalContext and does NOT block: a stale or wrong claim must never stop a real
edit. If nobody holds it, the hook claims it for this session. Claims go stale after 12h
or when the owning session's card is done or abandoned.

Off switch: RESOURCE_CLAIM_GUARD_DISABLE=1. Always exits 0 and fails open.
"""
import json
import os
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / ".agent" / "scripts"))

# script basename -> subcommands that write (None = every subcommand except READ_ONLY)
WRITERS = {
    "gdrive_manager.py": {"update", "rename", "delete"},
    "gdocs_writer.py": {"update"},
    "gdoc_surgical.py": None,
}
READ_ONLY = {"read", "list-tables"}

def parse_writer(command):
    """Return the Doc id a write command targets, or None."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    for i, tok in enumerate(tokens):
        script = os.path.basename(tok)
        if script not in WRITERS:
            continue
        rest = tokens[i + 1:]
        sub = next((t for t in rest if not t.startswith("-")), None)
        if sub is None:
            return None
        allowed = WRITERS[script]
        if allowed is None:
            if sub in READ_ONLY:
                return None
        elif sub not in allowed:
            return None
        for j, t in enumerate(rest):
            if t == "--id" and j + 1 < len(rest):
                return rest[j + 1]
            if t.startswith("--id="):
                return t.split("=", 1)[1]
        return None
    return None

def emit(message):
    sys.stderr.write(message + "\n")
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "additionalContext": message}}))

def main():
    if os.environ.get("RESOURCE_CLAIM_GUARD_DISABLE") == "1":
        return
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    if not raw:
        return
    payload = json.loads(raw)
    if payload.get("tool_name") != "Bash":
        return
    doc = parse_writer(str((payload.get("tool_input") or {}).get("command") or ""))
    me = str(payload.get("session_id") or "")
    if not doc or not me:
        return
    import claims
    import session_coord_common as C
    ok, holder = claims.claim("gdoc:" + doc, me, "auto-claimed before a Doc write")
    if not ok:
        cards = C.load_cards()
        emit("RESOURCE CLAIM: Google Doc %s is held by session %s, which is still live. "
             "Two sessions writing one Doc overwrite each other. Read the live Doc first, "
             "and post that session a note instead of writing blind: "
             "python3 .agent/scripts/session_notes.py post --to %s --text \"...\". "
             "Proceeding anyway is allowed; this is a warning, not a block."
             % (doc, claims.describe(holder, cards), str(holder.get("session_id"))[:8]))

if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
