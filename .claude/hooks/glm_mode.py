#!/usr/bin/env python3
"""SessionStart hook: surface the offload-mode toggle so the Router knows whether to
offload heavy generation/research/draft to agy-bridge (flat-rate subscription, zero Claude
quota). Backend is Gemini via the agy CLI since z.ai/GLM was retired 2026-07-27; the flag
file keeps its historical name.

Cross-platform replacement for glm_mode.sh.

Contract: always exit 0; never block a session.
"""
import os
import sys
from pathlib import Path

def project_dir():
    """CLAUDE_PROJECT_DIR when it is set and real, otherwise derived from this
    file's own location (two levels up from .claude/hooks/). The hardcoded WSL
    default other hooks use silently disables them on the macOS checkout, which
    is exactly where the offload-mode flag would silently read as off."""
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    return str(Path(__file__).resolve().parent.parent.parent)

def main():
    flag_path = Path(project_dir()) / ".agent" / "glm_mode.flag"

    state = "off"
    if flag_path.is_file():
        try:
            raw = flag_path.read_text(encoding="utf-8")
        except Exception:
            raw = ""
        state = "".join(raw.split()).lower()

    if state == "on":
        print("=== OFFLOAD MODE: ON (Gemini via agy-bridge) ===")
        print("Offload heavy generation/research/draft sub-tasks to agy-bridge")
        print("(python3 .agent/skills/agy-bridge/run.py --task draft|research|harvest ...). Claude")
        print("orchestrates + reviews + applies; do NOT burn Claude tokens on bulk generation.")
        print("Backend: Gemini via agy. z.ai/GLM retired 2026-07-27, do not pin --backend zai.")
        print("Turn it off by asking: offload off")
    else:
        print("=== OFFLOAD MODE: OFF (normal routing) ===")
        print('Default harness routing. Ask "offload on" to route heavy work to Gemini via agy-bridge.')

    sys.exit(0)

if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
