#!/usr/bin/env python3
"""PreToolUse hook on the Agent tool: log every spawn, warn on costly patterns.

Never blocks. Added 13 Oct 2026 after one quiz QA session spent 377M tokens
on 81 nested Opus subagents, one per quiz level, in under two hours.

Warns (to the model via additionalContext, to the user via systemMessage) when:
  - a spawn names opus/fable explicitly for work that reads as mechanical
    (QA, verify, lookup, harvest, format...);
  - a session passes SPAWN_WARN_AT spawns, then every SPAWN_WARN_EVERY after;
  - a subagent spawns another subagent (nesting).

Log: ~/.claude/logs/agent_spawns/<session_id>.jsonl, one line per spawn.
Pairs with CLAUDE_CODE_SUBAGENT_MODEL=sonnet (the default for a spawn with no
model) and .agent/scripts/token_usage_report.py (the weekly check).
"""
import json
import os
import re
import sys
import time

SPAWN_WARN_AT = 15
SPAWN_WARN_EVERY = 10
FLAGSHIP = {"opus", "fable"}
MECHANICAL = re.compile(
    r"\b(qa|verif\w*|check\w*|cek|review\w*|audit\w*|harvest\w*|lookup|look up|"
    r"scan\w*|grep|search\w*|find|read\w*|summar\w*|extract\w*|format\w*|"
    r"convert\w*|test\w*|translat\w*|list\w*)\b",
    re.I,
)
LOG_DIR = os.path.join(os.path.expanduser("~"), ".claude", "logs", "agent_spawns")

def main():
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
    except Exception:
        return
    if data.get("tool_name") not in ("Agent", "Task"):
        return
    ti = data.get("tool_input") or {}
    session = re.sub(r"[^\w-]", "_", str(data.get("session_id") or "unknown"))
    model = (ti.get("model") or "").lower()
    desc = ti.get("description") or ""
    nested = bool(data.get("agent_id"))

    os.makedirs(LOG_DIR, exist_ok=True)
    # The same guard can be registered twice (global ~/.claude settings and a
    # project's .claude/settings.json). Both copies get the same tool_use_id
    # and may run in parallel; whichever creates the marker first wins.
    tool_use_id = re.sub(r"[^\w-]", "_", str(data.get("tool_use_id") or ""))
    if tool_use_id:
        seen_dir = os.path.join(LOG_DIR, ".seen")
        os.makedirs(seen_dir, exist_ok=True)
        try:
            os.close(os.open(os.path.join(seen_dir, tool_use_id),
                             os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        except FileExistsError:
            return
        if tool_use_id.endswith("0"):  # prune old markers on ~1 in 16 calls
            cutoff = time.time() - 86400
            for name in os.listdir(seen_dir):
                path = os.path.join(seen_dir, name)
                try:
                    if os.path.getmtime(path) < cutoff:
                        os.remove(path)
                except OSError:
                    pass
    log = os.path.join(LOG_DIR, session + ".jsonl")
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "cwd": data.get("cwd"),
            "subagent_type": ti.get("subagent_type") or "general-purpose",
            "model": model or None,
            "description": desc[:120],
            "background": ti.get("run_in_background"),
            "parent_agent_id": data.get("agent_id"),
        }, ensure_ascii=False) + "\n")
    with open(log, encoding="utf-8") as f:
        count = sum(1 for _ in f)

    warnings = []
    shape = desc + " " + (ti.get("prompt") or "")[:300]
    if model in FLAGSHIP and MECHANICAL.search(shape):
        warnings.append(
            f"Agent '{desc}' is spawned on {model} for work that reads as mechanical "
            "(QA/verify/lookup/format). Use sonnet, or haiku for pure reading, "
            "unless this genuinely needs flagship judgment."
        )
    if count == SPAWN_WARN_AT + 1 or (
        count > SPAWN_WARN_AT and (count - SPAWN_WARN_AT - 1) % SPAWN_WARN_EVERY == 0
    ):
        warnings.append(
            f"This session has now spawned {count} subagents. Batch related items "
            "into fewer agents instead of one agent per item."
        )
    if nested:
        warnings.append(
            "A subagent is spawning another subagent. Nested fan-out multiplies cost; "
            "return to the parent and let it batch the work instead."
        )
    if warnings:
        msg = "Agent spend guard: " + " ".join(warnings)
        print(json.dumps({
            "systemMessage": msg,
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": msg,
            },
        }))

if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
