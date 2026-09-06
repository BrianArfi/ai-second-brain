#!/usr/bin/env python3
"""PostToolUse hook on Write|Edit: check that every link in a repo .md file resolves,
and that a file named in prose is actually linked.

Two failures this catches, both found by the owner and not by machine on 20 Aug 2026:

  1. A markdown link whose relative target does not exist on disk. The document
     reads as complete and the link is dead at the other end.
  2. A repo file named in prose as a bare filename ("see WhatsApp_OTP_Listener_Setup.md")
     when the file exists and could have been linked. The reader has to go find it.

http(s) links are NOT fetched. A hook must not make network calls on every write,
and a 200 today is not a 200 tomorrow. Only local targets are verified.

Warning only, never blocks: a document may legitimately name a file that does not
exist yet, and Write order inside one turn is not link order.

Also runs standalone over any path, for checking a document before it goes out:

    python3 .claude/hooks/link_guard.py Clients/Work/meetings/MOM_x.md
    python3 .claude/hooks/link_guard.py journal/            # whole tree

Exit code standalone: 1 when something is wrong, so it can gate a send. As a hook:
always 0.

Contract as a hook: always exit 0.
"""
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import unquote

SKIP_DIRS = ("/_archive/", "/node_modules/", "/scratch/", "/_temp/", "/.git/")

# [label](target)
LINK_RE = re.compile(r"\[(?P<label>[^\]]*)\]\((?P<target>[^)\s]+)(?:\s+\"[^\"]*\")?\)")
# fenced blocks and inline code, stripped before the bare-filename scan
FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
# a filename sitting in prose
BARE_FILE_RE = re.compile(r"(?<![\w/([`])(?P<name>[\w][\w.-]{2,}\.(?:md|html|pdf|csv|json|py|sh))(?![\w`])")

# Names everyone recognises, or that are tooling rather than reading material.
# Naming CLAUDE.md in a sentence is not a missing link; naming
# WhatsApp_OTP_Listener_Setup.md is.
COMMON_NAMES = {
    "CLAUDE.md", "README.md", "AGENTS.md", "MEMORY.md", "SKILL.md",
    "todo.md", "Dashboard.md", "CHANGELOG.md", "eval.md", "ste.md",
    "settings.json", "package.json", "config.json",
}

def is_worth_linking(name):
    """A bare filename is worth flagging only when it identifies one specific
    document. Short or generic names produce noise, and a guard that cries wolf
    stops being read."""
    if name in COMMON_NAMES:
        return False
    if name.endswith((".py", ".sh", ".json")):
        return False  # scripts and config are named to be run, not read
    stem = name.rsplit(".", 1)[0]
    return len(stem) >= 12 and ("_" in stem or "-" in stem)

CHECKED_SUFFIXES = (".md", ".markdown")

def project_dir():
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    return str(Path(__file__).resolve().parent.parent.parent)

def is_external(target):
    t = target.lower()
    return t.startswith(("http://", "https://", "mailto:", "tel:", "ftp://", "slack://"))

def check_text(text, doc_path, project):
    """Return (broken, unlinked) for one document's text.

    broken:   [(label, target)] whose local target does not exist
    unlinked: [(name, repo-relative path)] named in prose but never linked
    """
    doc_dir = os.path.dirname(os.path.abspath(doc_path)) or project

    broken = []
    linked_targets = set()
    for m in LINK_RE.finditer(text):
        target = m.group("target").strip()
        label = m.group("label").strip()
        if is_external(target) or target.startswith("#"):
            continue
        linked_targets.add(os.path.basename(unquote(target.split("#")[0])))
        path_part = unquote(target.split("#")[0])
        if not path_part:
            continue
        # Try the link as written, relative to the document. Then fall back to
        # repo-root-relative, which a lot of this repo's older prose uses and
        # which the dashboard resolves correctly.
        candidates = [
            os.path.normpath(os.path.join(project if path_part.startswith("/") else doc_dir,
                                          path_part.lstrip("/"))),
            os.path.normpath(os.path.join(project, path_part.lstrip("/"))),
        ]
        # A real absolute filesystem path. CLAUDE.md asks for these on any link
        # the owner clicks himself, so the guard has to try the path as written too,
        # not only as a repo-root-relative one.
        if path_part.startswith("/"):
            candidates.append(os.path.normpath(path_part))
        if not any(os.path.exists(c) for c in candidates):
            broken.append((label or target, target))

    # Bare filenames in prose. Fenced blocks are excluded because they are examples,
    # but INLINE code is not: `Some_Doc.md` in a sentence is exactly the case this
    # exists to catch. A backtick is not a link and nobody can click it.
    stripped = FENCE_RE.sub(" ", text)
    stripped = stripped.replace("`", " ")
    # remove link constructs entirely so labels and targets are not rescanned
    stripped = LINK_RE.sub(" ", stripped)
    unlinked = []
    seen = set()
    for m in BARE_FILE_RE.finditer(stripped):
        name = m.group("name")
        if name in seen or name in linked_targets:
            continue
        if os.path.basename(doc_path) == name:
            continue
        if not is_worth_linking(name):
            continue
        seen.add(name)
        hit = find_in_repo(name, project)
        if hit:
            unlinked.append((name, os.path.relpath(hit, project)))
    return broken, unlinked

_INDEX = {}

def find_in_repo(name, project):
    """First file with this basename in the repo, or None. Indexed once per run."""
    if not _INDEX:
        for root, dirs, files in os.walk(project):
            dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "_archive", "_temp", "scratch", ".venv")]
            for f in files:
                _INDEX.setdefault(f, os.path.join(root, f))
    return _INDEX.get(name)

def report(doc_rel, broken, unlinked):
    lines = []
    if broken:
        lines.append(f"{doc_rel}: {len(broken)} link(s) point at a file that does not exist:")
        for label, target in broken:
            lines.append(f"  - [{label}]({target})")
    if unlinked:
        lines.append(f"{doc_rel}: file(s) named in prose but not linked:")
        for name, rel in unlinked:
            lines.append(f"  - {name} -> link it as ({rel})")
    return lines

def run_hook():
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    except Exception:
        sys.exit(0)
    if not raw:
        sys.exit(0)
    try:
        d = json.loads(raw)
        ti = d.get("tool_input") or {}
        path = str(ti.get("file_path") or "")
        if not path.endswith(CHECKED_SUFFIXES):
            sys.exit(0)
        project = project_dir()
        norm = os.path.abspath(path)
        if not norm.startswith(os.path.abspath(project)):
            sys.exit(0)
        if any(s in norm for s in SKIP_DIRS):
            sys.exit(0)
        if not os.path.exists(norm):
            sys.exit(0)

        with open(norm, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()

        broken, unlinked = check_text(text, norm, project)
        if not broken and not unlinked:
            sys.exit(0)

        rel = os.path.relpath(norm, project)
        lines = report(rel, broken, unlinked)
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": (
                    "Link check failed. Fix before this document goes to anyone.\n"
                    + "\n".join(lines)
                ),
            }
        }
        print(json.dumps(payload))
    except Exception:
        pass
    sys.exit(0)

def run_cli(targets):
    project = project_dir()
    docs = []
    for t in targets:
        p = os.path.abspath(t)
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "_archive", "_temp", "scratch", ".venv")]
                docs += [os.path.join(root, f) for f in files if f.endswith(CHECKED_SUFFIXES)]
        elif p.endswith(CHECKED_SUFFIXES):
            docs.append(p)

    bad = 0
    for doc in sorted(docs):
        try:
            with open(doc, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except Exception:
            continue
        broken, unlinked = check_text(text, doc, project)
        if broken or unlinked:
            bad += 1
            print("\n".join(report(os.path.relpath(doc, project), broken, unlinked)))
    if bad:
        print(f"\n{bad} document(s) with link problems.")
        return 1
    print(f"{len(docs)} document(s) checked, all links resolve.")
    return 0

if __name__ == "__main__":
    try:
        if len(sys.argv) > 1:
            sys.exit(run_cli(sys.argv[1:]))
        run_hook()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
