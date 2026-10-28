#!/usr/bin/env python3
"""PreToolUse on Bash: warn before a command that is known to dump a large
result into the context with no cap. Never blocks.

Why: the 14-day audit of 23 Oct 2026 counted 25.8M chars of Bash results
(about 6.5M tokens) that stayed in context and were re-read on every later
turn. Slack history, Fathom transcripts, git logs, whole-file cats and Linear
saves were the usual sources.

Warns when the command matches a verbose pattern AND has no cap (head, tail,
--limit, -n <N>, --max, wc, grep -c, a redirect to a file, or `| python`).
Stdlib only, exit 0 always.
"""
import json
import re
import sys

VERBOSE = [
    (re.compile(r"\bcat\s+[^|;&>]*\.(md|json|jsonl|txt|log|csv|html)\b"), "cat of a whole file"),
    (re.compile(r"\bgit\s+(log|diff|show)\b"), "git log/diff/show"),
    (re.compile(r"slack_client\.py[^|;&]*--action\s+(history|search|thread)"), "Slack history/search"),
    (re.compile(r"fathom[^|;&]*(transcript|--full)"), "Fathom transcript"),
    (re.compile(r"\b(find|ls -R|tree)\b"), "recursive listing"),
    (re.compile(r"gmail_manager\.py[^|;&]*\b(get|list)\b"), "Gmail get/list"),
    (re.compile(r"(linear|jira)[_-]?(client|connector)?\.py[^|;&]*\b(get|list|search|save)\b"), "Linear/Jira read or save echo"),
    (re.compile(r"\bcurl\b"), "curl"),
    (re.compile(r"\bpdftotext\b(?!.*\|)"), "pdftotext"),
]
CAPPED = re.compile(r"\|\s*(head|tail|wc|grep -c|sed -n|cut|awk|jq|python3?|sort \| uniq -c)\b|--limit\b|--max\w*\b|\s-n\s*\d+|--top\b|--count\b|-maxdepth\b|>\s*\S+|--quiet\b|-q\b|--oneline|--stat\b", re.I)

def main():
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8-sig") or "{}")
    except Exception:
        return
    if data.get("tool_name") != "Bash":
        return
    cmd = str((data.get("tool_input") or {}).get("command") or "")
    if not cmd or CAPPED.search(cmd):
        return
    hits = [label for pat, label in VERBOSE if pat.search(cmd)]
    if not hits:
        return
    msg = (f"Output hygiene: {', '.join(hits)} with no cap. Every char of this result stays in context for the rest "
           f"of the session and is re-read on each turn. Add head/tail, a --limit, write the result to a file and read "
           f"a slice, or send the bulk read to a Haiku/Sonnet subagent. Proceed if the full output is really needed.")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": msg}}))

if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
