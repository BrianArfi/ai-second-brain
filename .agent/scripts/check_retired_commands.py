#!/usr/bin/env python3
"""Report retired slash commands that have reappeared in .claude/commands/.

The app re-seeds its own command template into the workspace from time to time,
and the end-of-turn worktree sync commits the result. That is how nine commands
retired on 6 Sep 2026 were back and committed the same evening (8128c92f4).
A duplicate command is not harmless: /inbox-sweep runs a Slack search that is
structurally blind to 1:1 DMs, which is the exact failure /sweep exists to fix.

Exit 0 = clean, 1 = a retired command is live again. `--fix` removes them, which is
safe: every name reported here already has its copy in the archive folder.

The app fix landed in ai-second-brain-app ed0af71 (a per-workspace ledger, so a
command is offered once and a deletion then stands). Until that build is
installed, this script is what holds the line.

Rationale and the full list: .agent/archive/commands/README.md
"""
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / ".agent" / "archive" / "commands"
LIVE = ROOT / ".claude" / "commands"

# Retired once, then deliberately brought back. Never reported.
STILL_LIVE_ON_PURPOSE = {"connect-tools.md"}

def main() -> int:
    fix = "--fix" in sys.argv
    retired = {p.name for p in ARCHIVE.glob("*.md")} - {"README.md"}
    back = sorted((retired - STILL_LIVE_ON_PURPOSE) & {p.name for p in LIVE.glob("*.md")})
    if not back:
        print(f"clean: {len(retired)} retired commands, none live")
        return 0
    print(f"{len(back)} retired command(s) are live again in .claude/commands/:")
    for name in back:
        print(f"  {name}")
    if not fix:
        print("\nThe app re-seeded its template. Archive the newer copy over the old one,")
        print("then remove it from .claude/commands/, or re-run with --fix.")
        print("See .agent/archive/commands/README.md")
        return 1
    # The re-seeded copy is the newer template text, so it replaces the archived one
    # rather than being thrown away: the archive is the record of what the command said.
    for name in back:
        shutil.copy2(LIVE / name, ARCHIVE / name)
        (LIVE / name).unlink()
    print(f"\nremoved {len(back)}, archived the newer text over the old copy")
    return 1

if __name__ == "__main__":
    sys.exit(main())
