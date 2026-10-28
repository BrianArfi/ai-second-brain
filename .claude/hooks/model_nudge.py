#!/usr/bin/env python3
"""UserPromptSubmit, first prompt only: nudge the session onto the right model
for the task class, per the owner's rule of 23 Oct 2026.

Measured over 21 days (redo-rate audit): Sonnet fails to fix on the first
correction 3x more often than Opus on Slack, reply, context and document work,
and matches Opus on coding, app and ops work. So:

  Slack / reply / context / docs / PM  -> Opus main loop (Sonnet subagents ok)
  coding / app / video / ops / scripts -> Sonnet main loop (Opus planner agent
                                          at the start, Opus reviewer before
                                          anything reaches the owner)

Claude Code fixes the model per session, so the hook can only suggest `/model`.
It prints one line, once per session, only when the model and the task class
disagree. Stdlib only, exit 0 always.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

PM = re.compile(r"\b(slack|reply|balas|bales|thread|dm|mention|context|konteks|sweep|follow.?ups?|chase|prd|mom|weekly|report|ticket|linear|jira|roadmap|brd|meeting|YourManager|julie|work|exampleprogram|example program|exampleco|draft|email|gmail|whatsapp|wa)\b", re.I)
CODE = re.compile(r"\b(app|asb|android|tauri|apk|bug|error|build|deploy|script|hook|skill|refactor|code|coding|compile|render|video|clip|dji|talk.?time|story.?time|ffmpeg|tts|vps|server|docker|cron)\b", re.I)

def session_model(repo_root):
    script = repo_root / ".agent" / "scripts" / "harness_config.py"
    if not script.is_file():
        return ""
    try:
        p = subprocess.run([sys.executable, str(script), "--json"], cwd=str(repo_root), capture_output=True, text=True, timeout=10)
        return (json.loads(p.stdout or "{}").get("model") or "").lower()
    except Exception:
        return ""

def main():
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return
    sid = re.sub(r"[^A-Za-z0-9_.-]", "_", str(data.get("session_id") or "nosession"))
    marker = Path(tempfile.gettempdir()) / "psb_model_nudge" / sid
    if marker.exists():
        return
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("1", encoding="utf-8")
    except OSError:
        pass
    prompt = str(data.get("prompt") or "")
    if len(prompt) < 12 or prompt.lstrip().startswith("<"):
        return
    pm, code = bool(PM.search(prompt)), bool(CODE.search(prompt))
    if pm == code:  # both or neither: no clear class, say nothing
        return
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parent.parent.parent)
    model = session_model(root)
    if not model:
        return
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if code and "opus" in model:
        print("Model nudge: this reads as coding, app or video work. the owner's rule (23 Oct 2026) runs that class on Sonnet, "
              "with an Opus planner agent at the start and an Opus reviewer before anything reaches him. "
              "Tell the owner in one plain sentence that `/model sonnet` would halve the cost of this session, then continue.")
    elif pm and "sonnet" in model:
        print("Model nudge: this reads as Slack, reply, context or document work. Measured 23 Oct 2026: Sonnet needs 3x more "
              "back-and-forth than Opus on this class. Tell the owner in one plain sentence that `/model opus` is the rule for this "
              "work, then continue; delegate bulk reading to Sonnet or Haiku subagents either way.")

if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
