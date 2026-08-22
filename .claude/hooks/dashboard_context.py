#!/usr/bin/env python3
"""SessionStart hook: inject a trimmed Dashboard.md instead of the full file.

The full Dashboard.md is ~64KB (~16k tokens) and was injected on every session
start. This loader keeps the high-value top (header + latest daily briefing)
and cuts the historic bulk, capping at MAX_CHARS. The model reads the full
file on demand when older context is needed.

The rendered payload is cached keyed on Dashboard.md (mtime, size) in
dashboard-data/.dashboard_context_cache.json, so unchanged dashboards skip
the re-read/re-trim on every session start.

Contract: never break a session - on any error print '{}' and exit 0.
"""
import json
import os
import pathlib
import sys

MAX_CHARS = 8000
# Historic sections start here; everything below this heading is bulk.
CUT_MARKERS = ["## 📊 Daily Change Summary"]

def project_dir():
    """CLAUDE_PROJECT_DIR when it is set and real, otherwise derived from this
    file's own location (two levels up from .claude/hooks/). The hardcoded WSL
    default other hooks use silently disables them on the macOS checkout, which
    is exactly where a missing Dashboard payload does the most damage."""
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    return str(pathlib.Path(__file__).resolve().parent.parent.parent)

def render(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()

    total = len(text)
    for marker in CUT_MARKERS:
        idx = text.find(marker)
        if idx > 0:
            text = text[:idx]
            break

    if len(text) > MAX_CHARS:
        cut = text.rfind("\n", 0, MAX_CHARS)
        text = text[: cut if cut > 0 else MAX_CHARS]

    text += (
        "\n\n[... Dashboard trimmed for session start "
        f"({len(text)} of {total} chars shown). Historic daily summaries, meeting links, "
        "and older sections were cut - Read Dashboard.md on demand when you need them.]"
    )

    return json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "=== Dashboard.md (auto-loaded context, trimmed) ===\n" + text,
        }
    })

def main():
    project = project_dir()
    path = os.path.join(project, "Dashboard.md")
    if not os.path.isfile(path):
        print("{}")
        return

    st = os.stat(path)
    key = [st.st_mtime, st.st_size]
    cache_path = os.path.join(project, "dashboard-data", ".dashboard_context_cache.json")

    try:  # cache hit: identical Dashboard.md -> replay cached payload
        with open(cache_path, encoding="utf-8") as f:
            cached = json.load(f)
        if cached.get("key") == key and cached.get("payload"):
            print(cached["payload"])
            return
    except Exception:
        pass

    payload = render(path)

    try:  # best-effort cache write; failure never blocks the hook
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"key": key, "payload": payload}, f)
    except Exception:
        pass

    print(payload)

if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("{}")
    sys.exit(0)
