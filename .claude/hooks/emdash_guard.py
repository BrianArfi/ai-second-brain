#!/usr/bin/env python3
"""PostToolUse hook on Write|Edit: warn when an em-dash/en-dash lands in a repo .md/.txt file.

Cross-platform replacement for emdash_guard.sh (which used a `grep` fast-path
before spawning python3; here the fast-path is a plain substring check).

the owner's #1 style rule (no-emdash skill) - enforced deterministically, warning-only
(em-dashes are legitimate when quoting transcripts, so never block).

Contract: always exit 0.
"""
import json
import os
import sys
from pathlib import Path

SKIP_DIRS = ("/_archive/", "/.agent/", "/node_modules/", "/scratch/", "/_temp/")

def project_dir():
    """CLAUDE_PROJECT_DIR when it is set and real, otherwise derived from this
    file's own location (two levels up from .claude/hooks/). The hardcoded WSL
    default other hooks use silently disables them on the macOS checkout, which
    is exactly where an em-dash would silently slip past this guard."""
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    return str(Path(__file__).resolve().parent.parent.parent)

def main():
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    except Exception:
        sys.exit(0)

    if not raw:
        sys.exit(0)

    # Fast-path: no em-dash/en-dash anywhere in the payload -> done.
    if "—" not in raw and "–" not in raw:
        sys.exit(0)

    try:
        d = json.loads(raw)
        ti = d.get("tool_input") or {}
        path = str(ti.get("file_path") or "")
        if not path.endswith((".md", ".txt")):
            sys.exit(0)

        project = project_dir()
        norm = os.path.abspath(path)
        project_abs = os.path.abspath(project)
        if not norm.startswith(project_abs):
            sys.exit(0)
        if any(s in norm for s in SKIP_DIRS):
            sys.exit(0)

        content = str(ti.get("content") or "") + str(ti.get("new_string") or "")
        if "—" not in content and "–" not in content:
            sys.exit(0)

        rel = os.path.relpath(norm, project_abs)
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": (
                    f"Em-dash detected in {rel}. Repo rule (no-emdash skill): replace "
                    "em-dash/en-dash characters with - or -- before delivering this document."
                ),
            }
        }
        print(json.dumps(payload))
    except Exception:
        pass
    sys.exit(0)

if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
