#!/usr/bin/env python3
"""UserPromptSubmit hook: inject the trimmed Dashboard once per session, for PM work.

dashboard_context.py (SessionStart) now injects only a 2-line pointer. This
companion injects the full trimmed Dashboard block the first time a prompt in
the session looks like Work/PM work (keywords below), and never again in that
session. A marker file in the OS temp dir (keyed by session_id) remembers it, so
nothing is written into the git-tracked repo.

Does nothing when the SessionStart hook already injected the full block
(DASHBOARD_CONTEXT_FULL=1, cron / daily-update runs) or when Dashboard.md is
missing. Stdlib only, exit 0 always.
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

KEYWORDS = re.compile(
    r"\b(work|exampleprogram|example program|exampleco|prd|mom|jira|linear|slack|YourManager|sweep|daily|weekly|ticket|chase)\b",
    re.I)

def looks_like_pm_work(prompt):
    return bool(KEYWORDS.search(prompt or ""))

def marker_path(session_id):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "nosession")
    return Path(tempfile.gettempdir()) / "psb_dashboard_inject" / safe

def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return
    import dashboard_context as D

    if D.wants_full():  # SessionStart already loaded the full block
        return
    if not looks_like_pm_work(str(payload.get("prompt") or "")):
        return
    marker = marker_path(str(payload.get("session_id") or ""))
    if marker.exists():
        return
    text = D.block_text(D.project_dir())
    if not text:
        return
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("1", encoding="utf-8")
    except Exception:
        pass  # worst case the block repeats on a later PM prompt
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(text)

if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
