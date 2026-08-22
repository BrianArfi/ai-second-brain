#!/usr/bin/env python3
"""PostToolUse hook on Write|Edit: an HTML deliverable under Clients/ must be handed
over as an artifact-host URL, never as a local file path.

Why this exists: on 7 Aug 2026 the Linear explainer was delivered to the owner as a
`\\\\wsl.localhost\\Ubuntu\\...` path. The file was fine and Windows could resolve it,
but the app's link validator could not, so it rendered as "isn't there any more".
A local path is the wrong handover format for an HTML deliverable regardless -- it
cannot be opened on a phone, cannot be sent to YourManager or Teammate, and goes stale the
moment the file moves. artifact-host already solves all of that with a stable URL.

Warning-only: publishing is a Drive write and stays approval-gated, so this hook
can only remind, never act.

Contract: always exit 0.
"""
import json
import os
import pathlib
import sys

SKIP_DIRS = ("/_archive/", "/node_modules/", "/scratch/", "/_temp/", "/figma/")
STATE = ".agent/skills/artifact-host/published_state.json"

def project_dir():
    """CLAUDE_PROJECT_DIR when it is set and real, otherwise derived from this
    file's own location (two levels up from .claude/hooks/). The hardcoded WSL
    default other hooks use silently disables them on the macOS checkout, which
    is exactly where an HTML deliverable would silently ship as a local path
    instead of an artifact-host URL."""
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    return str(pathlib.Path(__file__).resolve().parent.parent.parent)

def published_sources(project_abs):
    """Return the set of source paths artifact-host already serves."""
    try:
        with open(os.path.join(project_abs, STATE), encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return set()
    return {e.get("source", "") for e in data.get("entries", [])}

def main():
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    except Exception:
        sys.exit(0)
    if not raw:
        sys.exit(0)

    try:
        d = json.loads(raw)
        path = str((d.get("tool_input") or {}).get("file_path") or "")
        if not path.endswith(".html"):
            sys.exit(0)

        project_abs = project_dir()
        norm = os.path.abspath(path)
        if not norm.startswith(project_abs):
            sys.exit(0)
        if any(s in norm for s in SKIP_DIRS):
            sys.exit(0)

        rel = os.path.relpath(norm, project_abs)
        if not rel.startswith("Clients/"):
            sys.exit(0)
        # Intermediate build inputs are not the deliverable.
        if rel.endswith((".src.html", ".tmp.html")):
            sys.exit(0)

        already = rel in published_sources(project_abs)
        verb = "republish" if already else "publish"
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": (
                    f"HTML deliverable touched: {rel}. Repo rule: hand the owner an "
                    f"artifact-host URL, never a local path. Ask approval, then "
                    f"{verb} via Cloudflare Pages:\n"
                    f"  python3 .agent/skills/artifact-host/scripts/publish_cf.py deploy\n"
                    + (
                        "This file is already in the ARTIFACTS list, so the URL is "
                        "stable and deploying only refreshes its content."
                        if already
                        else
                        f"Not yet published. First add it to the ARTIFACTS list in "
                        f"publish_cf.py (name, description, '{rel}'), then deploy. "
                        f"Its URL will be https://work-artifacts.pages.dev/<slugified-name>."
                    )
                    + "\nDo not use deploy_host.py: that Apps Script host is superseded "
                    "and mangles inline SVG that relies on CSS variables."
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
