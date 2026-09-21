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
import time
from pathlib import Path

STATE = Path(__file__).resolve().parent.parent.parent / "journal" / "state" / "reply_router_state.json"
OPEN_STATUSES = ("open", "claimed", "active")
# A conversation closed longer ago than this is history, not a live duplicate.
DONE_WINDOW_SECONDS = 7 * 24 * 3600

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

def candidates(convs, channel, thread_ts):
    """Conversations this send could land in, most specific first."""
    found = []
    if thread_ts:
        key = "%s:t%s" % (channel, thread_ts)
        if key in convs:
            found.append((key, convs[key]))
    for key, conv in convs.items():
        if (key, conv) in found or not key.startswith(channel + ":"):
            continue
        if thread_ts:
            # A reply whose parent ts the router filed under a different key
            # (channel-level `:u<user>` conversations carry their messages here).
            msgs = conv.get("messages") or []
            if not any(len(m) > 1 and m[1] == thread_ts for m in msgs):
                continue
        elif conv.get("status") not in OPEN_STATUSES:
            # No thread-ts to disambiguate: only a live conversation is evidence.
            continue
        found.append((key, conv))
    return found

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
    for key, conv in candidates(convs, channel, thread_ts):
        owner = conv.get("session_id")
        status = conv.get("status")

        if status in OPEN_STATUSES and owner and owner != me:
            emit("deny", (
                "ANOTHER SESSION OWNS THIS CONVERSATION. The reply-router opened "
                "`%s` as session %s, and it is still open. Two sessions replying to one "
                "thread is how the same thread got two drafts and nearly two Jira tickets "
                "on 20 Sep 2026.\n"
                "Do this instead: open that session and send from there, or close it first "
                "(`reply_router.py close --conv '%s' --summary ...`). Check what it already "
                "produced before drafting anything new.\n"
                "Override for this call only: CLAIM_GUARD_DISABLE=1."
                % (key, owner, key)
            ))

        if status == "done":
            closed_at = conv.get("closed_at") or 0
            if time.time() - closed_at < DONE_WINDOW_SECONDS:
                emit("ask", (
                    "This conversation is already CLOSED in the reply-router (`%s`, reason: %s).\n"
                    "What it was closed with: %s\n"
                    "Confirm this is a genuinely new message and not a duplicate of the reply "
                    "that session already sent."
                    % (key, conv.get("closed_reason") or "?", conv.get("summary") or "(no summary)")
                ))

    sys.exit(0)

if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        # Fail open: never block a real send because the guard could not read state.
        sys.exit(0)
