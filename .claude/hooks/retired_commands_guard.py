#!/usr/bin/env python3
"""SessionStart: remove any retired slash command the app re-seeded overnight.

The bundled workspace template in ASB 0.13.8 and earlier copies every command a
workspace is missing, on every launch, so a deliberate deletion comes back and the
end-of-turn worktree sync commits it. That is how nine retired commands returned on
6 Sep 2026 and /inbox-sweep shadowed the tuned /sweep for a day.

The app-side fix is ed0af71 in ai-second-brain-app (offer each command once per
workspace). This hook holds the line until that build is installed, and is harmless
afterwards. Never blocks: it prints one line and exits 0.
"""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHECK = ROOT / ".agent" / "scripts" / "check_retired_commands.py"

def main() -> int:
    if not CHECK.is_file():
        return 0
    try:
        r = subprocess.run([sys.executable, str(CHECK), "--fix"],
                           capture_output=True, text=True, timeout=10)
    except Exception:
        return 0
    if r.returncode != 0:
        names = [ln.strip() for ln in r.stdout.splitlines() if ln.startswith("  ")]
        print("=== RETIRED COMMANDS ===")
        print(f"The app re-seeded {len(names)} retired command(s); removed again: "
              + ", ".join(n.removesuffix('.md') for n in names))
        print("Install ASB 0.13.9 or later to stop this at the source (fix: ed0af71).")
    return 0

if __name__ == "__main__":
    sys.exit(main())
