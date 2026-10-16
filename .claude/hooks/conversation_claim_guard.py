#!/usr/bin/env python3
"""PreToolUse hook on Bash: refuse a Slack send into a conversation another session owns.

The reply-router opens one session per Slack conversation and records the owning
session id under `journal/state/reply_router_state.json`. Nothing enforced that
record, so on 20 Sep 2026 two sessions worked the same thread (Teammate, banner
out-of-stock fallback): one filed ABC-123 and a reply draft, the other drafted a
second reply and was about to file a duplicate ticket.

This guard closes that. Before any `slack_client.py --action post`, it resolves the
conversation key from --channel / --thread-ts and checks who holds the claim:

  claimed by a DIFFERENT session  -> deny, and name the session that owns it
  already closed as done          -> ask, and show how it was closed
  unclaimed, or claimed by us      -> silent pass

It is advisory, not the approval gate: `--approved` and `send_slop_guard.py` still
run. Off switch: CLAIM_GUARD_DISABLE=1.

Contract: always exits 0 and fails OPEN -- a guard that cannot read its own state
must never be the reason a real reply does not go out.
"""
import json
import os
import shlex
import sys
from pathlib import Path

# The claim logic lives in .agent/scripts/gates/claims.py so slack_client.py can run the
# same check in-process (Grok Bot lessons plan, B4). This hook keeps the command-line
# parsing and the permissionDecision output; the decision itself is the shared function.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / ".agent" / "scripts"))
from gates.claims import (  # noqa: E402
    DONE_WINDOW_SECONDS, OPEN_STATUSES, candidates, evaluate,
)

STATE = Path(__file__).resolve().parent.parent.parent / "journal" / "state" / "reply_router_state.json"

def emit(decision, reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)

def is_slack_send(command):
    """True only for an actual slack_client.py post, not a probe or a mention of one."""
    if "slack_client.py" not in command:
        return False
    return "--action post" in command or "--action=post" in command

def parse_target(command):
    """Pull (channel, thread_ts) out of the command line. Either may be None."""
    channel = thread_ts = None
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    for i, tok in enumerate(tokens):
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if tok == "--channel" and nxt:
            channel = nxt
        elif tok.startswith("--channel="):
            channel = tok.split("=", 1)[1]
        elif tok == "--thread-ts" and nxt:
            thread_ts = nxt
        elif tok.startswith("--thread-ts="):
            thread_ts = tok.split("=", 1)[1]
    return channel, thread_ts

def main():
    if os.environ.get("CLAIM_GUARD_DISABLE") == "1":
        sys.exit(0)

    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    except Exception:
        raw = ""
    if not raw:
        sys.exit(0)

    payload = json.loads(raw)
    if payload.get("tool_name") != "Bash":
        sys.exit(0)

    command = str((payload.get("tool_input") or {}).get("command") or "")
    if not is_slack_send(command):
        sys.exit(0)

    channel, thread_ts = parse_target(command)
    if not channel:
        sys.exit(0)

    me = str(payload.get("session_id") or "")

    convs = (json.loads(STATE.read_text(encoding="utf-8")) or {}).get("conversations") or {}
    result = evaluate(convs, channel, thread_ts, me)
    if result:
        emit(*result)

    sys.exit(0)

if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        # Fail open: never block a real send because the guard could not read state.
        sys.exit(0)
