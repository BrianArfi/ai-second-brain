#!/usr/bin/env python3
"""The voice pack must match the workspace files it ships.

A pack folder holds copies, because `pack.py` resolves every source path inside
the pack. Copies drift: someone edits `.agent/skills/no-ai-slop/SKILL.md`, ships
it, and every workspace that installs the pack afterwards gets the old rules
while the template shows the new ones. Nothing errors, and the pack looks fine.

This is the check that catches that. Run it after editing anything the pack
ships.

Usage:  python3 tools/test_voice_pack.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACK = REPO_ROOT / "packs" / "voice"

manifest = json.loads((PACK / "capability-pack.json").read_text(encoding="utf-8"))
paths = [rel for group in manifest["contents"].values() for rel in group]

drifted: list[str] = []
missing: list[str] = []

for rel in paths:
    shipped = PACK / rel
    canonical = REPO_ROOT / rel
    if not canonical.is_file():
        missing.append(f"{rel} is in the pack but not in the workspace")
        continue
    if not shipped.is_file():
        missing.append(f"{rel} is in the manifest but not in the pack folder")
        continue
    if shipped.read_bytes() != canonical.read_bytes():
        drifted.append(rel)

for rel in drifted:
    print(f"  DRIFT   {rel}")
for m in missing:
    print(f"  MISSING {m}")

if not drifted and not missing:
    print(f"voice pack matches the workspace, {len(paths)} file(s)")
    sys.exit(0)

print("")
print("Fix by copying the workspace file over the pack copy, after checking which one is newer:")
for rel in drifted:
    print(f"  cp {rel} packs/voice/{rel}")
sys.exit(1)
