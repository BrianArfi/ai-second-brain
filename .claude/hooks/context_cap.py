#!/usr/bin/env python3
"""Context cap: hand off only when continuing is expensive.

Runs on UserPromptSubmit and on PreToolUse (Bash, Agent). Reads the session
transcript named in the hook payload, takes the last main-chain assistant
`usage` block, and computes the context the next request will carry (input +
cache write + cache read). Never blocks a tool call.

Rule (the owner, 26 Oct 2026, replaces the flat 120k/150k cap of 23 Oct):
  While the prompt cache is warm, continuing is cheap (cache reads cost a tenth),
  so a session continues up to the smart-zone limit. A new session only pays off
  once the cache has expired, because the next request rewrites the whole
  context at full price anyway.

  COLD 150k: context >= 150k AND the cache has expired -> hand off.
  SMART 500k: context >= 500k, warm or not -> hand off (quality drops).
  Below that: silent. No early warning.

Cache warmth: the timestamp of the last main-chain request plus its TTL. TTL is
1 hour when the latest cache write used `ephemeral_1h_input_tokens`, else 5
minutes. Mid-turn (PreToolUse) the cache is always warm, so only SMART applies.

Override with CONTEXT_CAP_HARD (cold threshold) and CONTEXT_CAP_SMART, tokens.
The handoff instruction repeats at most once per 10 minutes. Stdlib only, exit 0.
"""
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime

HARD = int(os.environ.get("CONTEXT_CAP_HARD", "150000"))
SMART = int(os.environ.get("CONTEXT_CAP_SMART", "500000"))
REPEAT_SECONDS = 600
TTL_1H = 3600
TTL_5M = 300

def parse_ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None

def last_request(transcript_path):
    """(context_tokens, last_request_epoch, ttl_seconds) of the last main-chain request, or None."""
    try:
        size = os.path.getsize(transcript_path)
        with open(transcript_path, "rb") as f:
            f.seek(max(0, size - 2_000_000))  # tail only; usage lines are near the end
            tail = f.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    ctx = ts = ttl = None
    for line in reversed(tail.splitlines()):
        if '"usage"' not in line or '"assistant"' not in line:
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("isSidechain"):
            continue
        u = (e.get("message") or {}).get("usage") or {}
        if not u:
            continue
        if ctx is None:
            ctx = (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
            ts = parse_ts(e.get("timestamp"))
        cc = u.get("cache_creation") or {}
        if cc.get("ephemeral_1h_input_tokens"):
            ttl = TTL_1H
        elif cc.get("ephemeral_5m_input_tokens"):
            ttl = TTL_5M
        if ttl:
            break
    if ctx is None:
        return None
    return ctx, ts, ttl or TTL_5M

def marker(session_id, level):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "nosession")
    d = os.path.join(tempfile.gettempdir(), "psb_context_cap")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{safe}.{level}")

def should_emit(session_id, level):
    p = marker(session_id, level)
    try:
        if os.path.exists(p) and time.time() - os.path.getmtime(p) < REPEAT_SECONDS:
            return False
        with open(p, "w") as f:
            f.write(str(time.time()))
    except OSError:
        pass
    return True

def main():
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8-sig") or "{}")
    except Exception:
        return
    tp = data.get("transcript_path")
    if not tp or not os.path.exists(tp):
        return
    got = last_request(tp)
    if got is None:
        return
    ctx, ts, ttl = got
    sid = str(data.get("session_id") or "")
    short = sid[:8]
    event = data.get("hook_event_name") or ("PreToolUse" if data.get("tool_name") else "UserPromptSubmit")
    k = ctx // 1000

    # Mid-turn the last request was seconds ago, so the cache is warm by definition.
    idle = (time.time() - ts) if (ts and event == "UserPromptSubmit") else 0
    cold = idle > ttl

    if ctx >= SMART:
        level = "smart"
        why = f"above the {SMART // 1000}k smart-zone limit, where quality drops"
        user = f"Context {k}k tokens, past {SMART // 1000}k. Finishing the current step, then continuing in a new session (/session {short})."
    elif ctx >= HARD and cold:
        level = "cold"
        mins = int(idle // 60)
        why = (f"and the prompt cache has expired (last request {mins} min ago, cache lifetime {ttl // 60} min), so the next "
               f"request rewrites all {k}k tokens at full price; a fresh session is cheaper")
        user = f"Context {k}k tokens and the cache has expired. Continuing in a new session (/session {short})."
    else:
        return
    if not should_emit(sid, level):
        return

    msg = (
        f"CONTEXT CAP: this session carries {k}k tokens of context, {why} (the owner's rule, 26 Oct 2026). "
        f"Finish only the step in progress. Then write the handoff: what was asked, what is done, what is open, "
        f"the files touched, the next step, in 10 lines or fewer, and tell the owner in one plain sentence to continue "
        f"in a new session with `/session {short}`. Do not start new work here."
    )

    if event == "UserPromptSubmit":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
        print(msg)
        return
    print(json.dumps({
        "systemMessage": user,
        "hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": msg},
    }))

if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
