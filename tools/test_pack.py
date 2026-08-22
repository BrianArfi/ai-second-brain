#!/usr/bin/env python3
"""Tests for the capability pack installer.

Each one is a way the installer could do real damage, rather than a check that
the happy path works. It writes into a temporary copy of nothing: every test
builds its own workspace, so a failure cannot leave files in this repo.

Usage:  python3 tools/test_pack.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TOOL = Path(__file__).resolve()
failures: list[str] = []


def check(name, fn):
    try:
        detail = fn()
        print(f"  ok    {name}" + (f" — {detail}" if detail else ""))
    except AssertionError as e:
        failures.append(f"{name}: {e}")
        print(f"  FAIL  {name} — {e}")


def make_workspace(tmp: Path) -> Path:
    """The smallest thing pack.py treats as a workspace."""
    ws = tmp / "ws"
    (ws / "tools").mkdir(parents=True)
    shutil.copy2(TOOL.parent / "pack.py", ws / "tools" / "pack.py")
    (ws / "CHANGELOG.md").write_text("# Changelog\n\n## v1.2.3 - 2026-01-01\n")
    return ws


def make_pack(tmp: Path, name="demo", contents=None, requires=None, extra_files=True) -> Path:
    pack = tmp / name
    (pack / ".agent/skills/demo").mkdir(parents=True)
    if extra_files:
        (pack / ".agent/skills/demo/SKILL.md").write_text("---\nname: demo\ndescription: d\n---\n")
    manifest = {
        "name": name,
        "apiVersion": "0.1",
        "version": "1.0.0",
        "description": "A demo pack.",
        "contents": contents or {"skills": [".agent/skills/demo/SKILL.md"]},
    }
    if requires:
        manifest["requires"] = requires
    (pack / "capability-pack.json").write_text(json.dumps(manifest, indent=2))
    return pack


def run(ws: Path, *args):
    return subprocess.run(
        [sys.executable, "tools/pack.py", *args],
        cwd=ws, capture_output=True, text=True,
    )


with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)

    def t_installs():
        ws = make_workspace(tmp / "a")
        pack = make_pack(tmp / "a", "demo")
        r = run(ws, "install", str(pack))
        assert r.returncode == 0, r.stderr
        assert (ws / ".agent/skills/demo/SKILL.md").is_file(), "the file did not land"
        return "1 file"

    def t_refuses_conflict():
        ws = make_workspace(tmp / "b")
        pack = make_pack(tmp / "b", "demo")
        assert run(ws, "install", str(pack)).returncode == 0
        (ws / ".agent/skills/demo/SKILL.md").write_text("MINE\n")
        r = run(ws, "install", str(pack))
        assert r.returncode == 1, "it overwrote without being asked"
        assert (ws / ".agent/skills/demo/SKILL.md").read_text() == "MINE\n", "the user's file was destroyed"
        return "user's file survived"

    def t_nothing_written_on_refusal():
        # The rule that matters: a refusal writes NOTHING, not "everything up to
        # the conflict". Half a pack looks installed and is not.
        ws = make_workspace(tmp / "c")
        pack = make_pack(tmp / "c", "demo", contents={
            "skills": [".agent/skills/demo/SKILL.md"],
            "commands": [".claude/commands/demo.md"],
        })
        (pack / ".claude/commands").mkdir(parents=True)
        (pack / ".claude/commands/demo.md").write_text("---\ndescription: d\n---\n")
        (ws / ".claude/commands").mkdir(parents=True)
        (ws / ".claude/commands/demo.md").write_text("MINE\n")
        r = run(ws, "install", str(pack))
        assert r.returncode == 1
        assert not (ws / ".agent/skills/demo/SKILL.md").exists(), "it wrote the non-conflicting file anyway"
        return "no partial install"

    def t_escape_refused():
        ws = make_workspace(tmp / "d")
        pack = make_pack(tmp / "d", "demo", contents={"skills": ["../../../etc/evil.md"]})
        r = run(ws, "install", str(pack))
        assert r.returncode == 1, "a path leaving the workspace was accepted"
        assert "leaves the pack" in (r.stdout + r.stderr), (r.stdout + r.stderr)
        return "refused"

    def t_requirement_refused():
        ws = make_workspace(tmp / "e")
        pack = make_pack(tmp / "e", "demo", requires={"templateVersion": ">=99.0.0"})
        r = run(ws, "install", str(pack))
        assert r.returncode == 1
        assert "99.0.0" in (r.stdout + r.stderr)
        assert not (ws / ".agent/skills/demo/SKILL.md").exists()
        return "refused before writing"

    def t_partial_version_requirement():
        # ">=3.9" is how a person writes a Python floor, and demanding three
        # parts made that requirement unsatisfiable on every machine.
        ws = make_workspace(tmp / "f")
        pack = make_pack(tmp / "f", "demo", requires={"python": ">=3.9", "templateVersion": ">=1.0"})
        r = run(ws, "install", str(pack))
        assert r.returncode == 0, r.stdout + r.stderr
        return ">=3.9 is satisfiable"

    def t_post_install_is_printed_not_run():
        ws = make_workspace(tmp / "g")
        pack = make_pack(tmp / "g", "demo")
        manifest = json.loads((pack / "capability-pack.json").read_text())
        manifest["postInstall"] = ["touch /tmp/asb-pack-should-not-exist"]
        (pack / "capability-pack.json").write_text(json.dumps(manifest))
        r = run(ws, "install", str(pack))
        assert r.returncode == 0
        assert "touch /tmp/asb-pack-should-not-exist" in r.stdout, "the instruction was not shown"
        assert not Path("/tmp/asb-pack-should-not-exist").exists(), "postInstall was EXECUTED"
        return "printed only"

    def t_remove_takes_back_what_it_put():
        ws = make_workspace(tmp / "h")
        pack = make_pack(tmp / "h", "demo")
        assert run(ws, "install", str(pack)).returncode == 0
        r = run(ws, "remove", "demo")
        assert r.returncode == 0, r.stderr
        assert not (ws / ".agent/skills/demo/SKILL.md").exists()
        assert not (ws / ".agent/skills/demo").exists(), "an empty folder was left behind"
        return "clean"

    def t_remove_leaves_a_folder_that_holds_something_else():
        ws = make_workspace(tmp / "i")
        pack = make_pack(tmp / "i", "demo")
        assert run(ws, "install", str(pack)).returncode == 0
        (ws / ".agent/skills/demo/notes.txt").write_text("mine")
        assert run(ws, "remove", "demo").returncode == 0
        assert (ws / ".agent/skills/demo/notes.txt").is_file(), "the user's file was removed with the pack"
        return "user's file survived"

    print("installing")
    check("a valid pack installs", t_installs)
    check("it refuses to overwrite a file that is already there", t_refuses_conflict)
    check("a refusal writes nothing at all, not everything up to the conflict", t_nothing_written_on_refusal)
    check("a path that leaves the pack is refused", t_escape_refused)
    check("an unmet requirement stops it before it writes", t_requirement_refused)
    check("a partial version requirement such as >=3.9 is satisfiable", t_partial_version_requirement)
    print("\ntrust")
    check("postInstall is printed for the human, never executed", t_post_install_is_printed_not_run)
    print("\nremoving")
    check("remove takes back exactly what it put down", t_remove_takes_back_what_it_put)
    check("remove leaves a folder that holds something else", t_remove_leaves_a_folder_that_holds_something_else)

print("")
if failures:
    print(f"{len(failures)} failure(s):")
    for f in failures:
        print(f"  - {f}")
else:
    print("all pack installer checks passed")
sys.exit(len(failures))
