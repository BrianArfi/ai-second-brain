#!/usr/bin/env python3
"""SessionStart hook: Dashboard.md context, a 2-line pointer by default.

The full Dashboard.md is ~64KB (~16k tokens). The trimmed block (header + latest
daily briefing, capped at MAX_CHARS, ~7.5k chars) used to be injected on every
session start, which charged Work briefing text to sessions that never touch
Work work. Now:

- default: a 2-line pointer;
- full trimmed block only for cron / daily-update style runs (AGENT_MODE=1,
  PSB_ROUTINE set, or a `claude -p` ancestor on Linux) or when
  DASHBOARD_CONTEXT_FULL=1 (kill switch: always full);
- otherwise dashboard_prompt_context.py (UserPromptSubmit) injects the same
  block once, when the first prompt looks like Work/PM work.

The trimmed block is cached keyed on Dashboard.md (mtime, size) in
dashboard-data/.dashboard_context_cache.json, so unchanged dashboards skip
the re-read/re-trim.

Contract: never break a session - on any error print '{}' and exit 0.
"""
import json
import os
import pathlib
import sys

MAX_CHARS = 8000
# Historic sections start here; everything below this heading is bulk.
CUT_MARKERS = ["## 📊 Daily Change Summary"]

POINTER = (
    "Dashboard.md (Work/PM briefing) is not auto-loaded this session. For Work or PM work, "
    "Read Dashboard.md and journal/todo.md first; a prompt hook also injects the trimmed block "
    "when your first PM-looking prompt arrives.\n"
    "Set DASHBOARD_CONTEXT_FULL=1 to always load it at session start."
)

def project_dir():
    """CLAUDE_PROJECT_DIR when it is set and real, otherwise derived from this
    file's own location (two levels up from .claude/hooks/). The hardcoded WSL
    default other hooks use silently disables them on the macOS checkout, which
    is exactly where a missing Dashboard payload does the most damage."""
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    return str(pathlib.Path(__file__).resolve().parent.parent.parent)

def trimmed_text(path):
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
    return "=== Dashboard.md (auto-loaded context, trimmed) ===\n" + text

def block_text(project):
    """The full trimmed Dashboard block, cached on Dashboard.md (mtime, size).
    Returns '' when Dashboard.md is missing. Shared with dashboard_prompt_context.py."""
    path = os.path.join(project, "Dashboard.md")
    if not os.path.isfile(path):
        return ""
    st = os.stat(path)
    key = [st.st_mtime, st.st_size]
    cache_path = os.path.join(project, "dashboard-data", ".dashboard_context_cache.json")

    try:  # cache hit: identical Dashboard.md -> replay cached block
        with open(cache_path, encoding="utf-8") as f:
            cached = json.load(f)
        if cached.get("key") == key and cached.get("text"):
            return cached["text"]
    except Exception:
        pass

    text = trimmed_text(path)

    try:  # best-effort cache write; failure never blocks the hook
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"key": key, "text": text}, f)
    except Exception:
        pass
    return text

def under_headless_claude():
    """Best effort (Linux only): is an ancestor process a `claude -p` / `--print` run?"""
    try:
        pid = os.getppid()
        for _ in range(8):
            if pid <= 1:
                return False
            with open("/proc/%d/cmdline" % pid, "rb") as f:
                argv = f.read().decode("utf-8", "replace").split("\0")
            if "claude" in argv[0] and any(a in ("-p", "--print") for a in argv[1:]):
                return True
            with open("/proc/%d/stat" % pid, encoding="utf-8") as f:
                pid = int(f.read().rsplit(")", 1)[1].split()[1])
    except Exception:
        pass
    return False

def wants_full():
    """Full block at session start: kill switch, or a cron / daily-update style run."""
    if os.environ.get("DASHBOARD_CONTEXT_FULL") == "1":
        return True
    if os.environ.get("AGENT_MODE") == "1" or os.environ.get("PSB_ROUTINE"):
        return True
    return under_headless_claude()

def emit(text):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": text,
        }
    }))

def main():
    project = project_dir()
    if not os.path.isfile(os.path.join(project, "Dashboard.md")):
        print("{}")
        return
    if not wants_full():
        emit(POINTER)
        return
    text = block_text(project)
    if not text:
        print("{}")
        return
    emit(text)

if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("{}")
    sys.exit(0)
