#!/usr/bin/env python3
"""Install a capability pack into this workspace.

A pack is skills, commands, agents and hooks that belong together. This is how
somebody ships "the marketing pack" without forking the template.

Three rules this follows, and each one is a decision rather than an oversight:

1. **It never runs anything the pack supplies.** Install time is the one moment
   the user has not yet decided to trust the code. A pack's `postInstall` is
   printed for the human to read and run, not executed.

2. **It refuses rather than half-installing.** A pack whose requirements are not
   met, or whose files would land on top of yours, stops before writing
   anything. Half a pack is worse than none: the parts that did land look
   installed and the parts that did not are invisible.

3. **It never writes outside the workspace.** Every path in the manifest is
   resolved and checked, so a `..` or a symlink cannot reach out.

Usage:
    python3 tools/pack.py install <folder>      # install, refusing on conflict
    python3 tools/pack.py install <folder> --force
    python3 tools/pack.py check <folder>        # validate, write nothing
    python3 tools/pack.py list                  # what is installed here
    python3 tools/pack.py remove <name>
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_NAME = "capability-pack.json"
# Where the installer records what it put down, so remove knows what to take
# away and does not have to guess from the pack folder still being on disk.
RECEIPTS_DIR = REPO_ROOT / ".asb" / "packs"

PACK_API_MAJOR = 0

CONTENT_KINDS = {"skills", "commands", "agents", "hooks", "templates", "scripts"}


def fail(message: str) -> None:
    print(f"  {message}", file=sys.stderr)


def parse_semver(text: str):
    """Parse a version, padding a partial one with zeros.

    A requirement is written the way people write it, and ">=3.9" is how people
    write a Python floor. Demanding three parts made that requirement
    unsatisfiable on every machine, which is a rule nobody could ever meet.
    """
    m = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?", text.strip())
    if not m:
        return None
    return tuple(int(g) if g else 0 for g in m.groups())


def satisfies(version: str, spec: str) -> bool:
    """Supports >=, >, ==, and a bare version meaning >=. Enough for a manifest."""
    v = parse_semver(version)
    if not v:
        return False
    spec = spec.strip()
    for op in (">=", "<=", "==", ">", "<"):
        if spec.startswith(op):
            want = parse_semver(spec[len(op):])
            if not want:
                return False
            return {
                ">=": v >= want, "<=": v <= want, "==": v == want,
                ">": v > want, "<": v < want,
            }[op]
    want = parse_semver(spec)
    return bool(want) and v >= want


def template_version() -> str:
    """This workspace's template version, from the newest vX.Y.Z in the changelog."""
    changelog = REPO_ROOT / "CHANGELOG.md"
    if changelog.exists():
        m = re.search(r"^## v(\d+\.\d+\.\d+)", changelog.read_text(encoding="utf-8"), re.M)
        if m:
            return m.group(1)
    return "0.0.0"


def read_manifest(folder: Path) -> dict:
    path = folder / MANIFEST_NAME
    if not path.is_file():
        raise ValueError(f"{folder} has no {MANIFEST_NAME}, so it is not a pack.")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"{MANIFEST_NAME} is not valid JSON: {e}") from e


def validate(folder: Path, manifest: dict) -> list[str]:
    """Everything wrong with the pack, so the user fixes it in one pass."""
    problems = []

    for key in ("name", "apiVersion", "version", "description", "contents"):
        if key not in manifest:
            problems.append(f"the manifest has no {key}")
    if problems:
        return problems

    if not re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", manifest["name"]):
        problems.append(f'name "{manifest["name"]}" must be lowercase words joined by dashes')

    api = str(manifest["apiVersion"])
    if not re.match(r"^\d+\.\d+$", api):
        problems.append(f'apiVersion "{api}" is not MAJOR.MINOR')
    elif int(api.split(".")[0]) != PACK_API_MAJOR:
        problems.append(f"pack API {api} is not supported. This workspace implements {PACK_API_MAJOR}.x")

    if not parse_semver(str(manifest["version"])):
        problems.append(f'version "{manifest["version"]}" is not semver')

    contents = manifest.get("contents") or {}
    if not contents:
        problems.append("contents is empty, so the pack installs nothing")
    for kind, paths in contents.items():
        if kind not in CONTENT_KINDS:
            problems.append(f'contents has an unknown kind "{kind}". Use one of: {", ".join(sorted(CONTENT_KINDS))}')
            continue
        for rel in paths:
            if rel.startswith("/") or ".." in Path(rel).parts:
                problems.append(f'contents.{kind} path "{rel}" leaves the pack')
                continue
            if not (folder / rel).is_file():
                problems.append(f'contents.{kind} lists "{rel}", which does not exist in the pack')

    requires = manifest.get("requires") or {}
    if "templateVersion" in requires:
        here = template_version()
        if not satisfies(here, requires["templateVersion"]):
            problems.append(
                f'needs template {requires["templateVersion"]}, and this workspace is {here}'
            )
    if "python" in requires:
        here = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        if not satisfies(here, requires["python"]):
            problems.append(f'needs Python {requires["python"]}, and this is {here}')
    for command in requires.get("commands", []):
        if not shutil.which(command):
            problems.append(f'needs "{command}" on PATH, and it is not there')
    # Connectors are named, never checked: whether one is connected is the app's
    # fact and not the filesystem's, so the honest thing is to say what the pack
    # needs and let the user confirm it.

    return problems


def planned_writes(folder: Path, manifest: dict) -> list[tuple[Path, Path]]:
    """(source, destination) for every file, destinations inside the workspace."""
    out = []
    root = REPO_ROOT.resolve()
    for paths in (manifest.get("contents") or {}).values():
        for rel in paths:
            dest = (root / rel).resolve()
            if not str(dest).startswith(str(root)):
                raise ValueError(f'"{rel}" resolves outside the workspace')
            out.append((folder / rel, dest))
    return out


def cmd_check(folder: Path) -> int:
    manifest = read_manifest(folder)
    problems = validate(folder, manifest)
    if problems:
        print(f"{manifest.get('name', folder.name)}: {len(problems)} problem(s)")
        for p in problems:
            fail(p)
        return 1
    writes = planned_writes(folder, manifest)
    print(f"{manifest['name']} {manifest['version']}: ok, {len(writes)} file(s)")
    for _, dest in writes:
        marker = "  (would replace)" if dest.exists() else ""
        print(f"  {dest.relative_to(REPO_ROOT)}{marker}")
    return 0


def cmd_install(folder: Path, force: bool) -> int:
    manifest = read_manifest(folder)
    problems = validate(folder, manifest)
    if problems:
        print(f"Not installing {manifest.get('name', folder.name)}: {len(problems)} problem(s)")
        for p in problems:
            fail(p)
        return 1

    writes = planned_writes(folder, manifest)
    existing = [d for _, d in writes if d.exists()]
    if existing and not force:
        print(f"Not installing {manifest['name']}: {len(existing)} file(s) already exist.")
        for d in existing:
            fail(str(d.relative_to(REPO_ROOT)))
        fail("Nothing was written. Re-run with --force to overwrite, after you have looked.")
        return 1

    for src, dest in writes:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        if dest.suffix == ".py" or dest.parent.name == "scripts":
            dest.chmod(dest.stat().st_mode | 0o111)

    RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
    receipt = {
        "name": manifest["name"],
        "version": manifest["version"],
        "installedFrom": str(folder.resolve()),
        "files": [str(d.relative_to(REPO_ROOT)) for _, d in writes],
    }
    (RECEIPTS_DIR / f"{manifest['name']}.json").write_text(json.dumps(receipt, indent=2) + "\n")

    print(f"Installed {manifest['name']} {manifest['version']}: {len(writes)} file(s).")
    for line in manifest.get("postInstall", []):
        print(f"  {line}")
    return 0


def cmd_list() -> int:
    if not RECEIPTS_DIR.is_dir():
        print("No packs installed.")
        return 0
    receipts = sorted(RECEIPTS_DIR.glob("*.json"))
    if not receipts:
        print("No packs installed.")
        return 0
    for r in receipts:
        data = json.loads(r.read_text(encoding="utf-8"))
        missing = [f for f in data["files"] if not (REPO_ROOT / f).exists()]
        note = f"  ({len(missing)} file(s) missing)" if missing else ""
        print(f"{data['name']} {data['version']}  {len(data['files'])} file(s){note}")
    return 0


def cmd_remove(name: str) -> int:
    receipt_path = RECEIPTS_DIR / f"{name}.json"
    if not receipt_path.is_file():
        print(f"No pack named {name} is installed.", file=sys.stderr)
        return 1
    data = json.loads(receipt_path.read_text(encoding="utf-8"))
    removed = 0
    for rel in data["files"]:
        path = REPO_ROOT / rel
        if path.is_file():
            path.unlink()
            removed += 1
    receipt_path.unlink()

    # Take away the folders the pack created, and only while they are empty.
    # Walking upward and stopping at the first non-empty parent means a folder
    # that holds something else, including something the user added, is never
    # touched.
    for rel in data["files"]:
        folder = (REPO_ROOT / rel).parent
        while folder != REPO_ROOT and folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
            folder = folder.parent

    print(f"Removed {name}: {removed} of {len(data['files'])} file(s).")
    if removed != len(data["files"]):
        print("  The rest were already gone.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    p_install = sub.add_parser("install", help="install a pack from a folder")
    p_install.add_argument("folder", type=Path)
    p_install.add_argument("--force", action="store_true", help="overwrite files that already exist")

    p_check = sub.add_parser("check", help="validate a pack, write nothing")
    p_check.add_argument("folder", type=Path)

    sub.add_parser("list", help="what is installed here")

    p_remove = sub.add_parser("remove", help="remove an installed pack")
    p_remove.add_argument("name")

    args = ap.parse_args()
    try:
        if args.command == "install":
            return cmd_install(args.folder, args.force)
        if args.command == "check":
            return cmd_check(args.folder)
        if args.command == "list":
            return cmd_list()
        if args.command == "remove":
            return cmd_remove(args.name)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
