#!/usr/bin/env python3
"""Check Slack claims and Ledger reads before Codex tools run."""

from io import StringIO
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / ".claude" / "hooks"))

import conversation_claim_guard
from ledger_freshness import SKIP, TRIGGERS, project_dir
from ledger_watch import invoked_scripts

def check_ledger_freshness(command):
    if any(word in command for word in SKIP) or not any(word in command for word in TRIGGERS):
        return
    try:
        compared = subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "origin/main", "--",
             "journal/state", "journal/master_followup_tracker.md"],
            cwd=project_dir(), capture_output=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    if compared.returncode == 1:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": (
                "Local Ledger files differ from the cached origin/main reference. "
                "Reconcile the source versions before relying on current status. "
                "A read-only task must leave the checkout untouched."
            ),
        }}))

def main():
    raw = sys.stdin.read()
    event = json.loads(raw)
    tool = event.get("tool_name", "")
    if tool == "Bash":
        command = (event.get("tool_input") or {}).get("command", "")
        if "slack_client.py" in invoked_scripts(command):
            sys.stdin = StringIO(raw)
            conversation_claim_guard.main(codex=True)
        else:
            check_ledger_freshness(command)
    elif tool.startswith("mcp__"):
        _, *namespace, method = tool.lower().split("__")
        if not any("slack" in part for part in namespace):
            return
        name = method.replace("_", "")
        if name in {"post", "send"} or name.endswith(
            ("postmessage", "sendmessage", "schedulemessage", "postreply", "sendreply")
        ):
            conversation_claim_guard.emit(
                "deny", "Use the approval-gated Slack connector in .agent/protocols/slack_send.md."
            )

if __name__ == "__main__":
    main()
