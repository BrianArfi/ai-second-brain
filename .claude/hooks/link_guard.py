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

def git_root_of(path):
    """Nearest ancestor directory holding a .git entry, or None."""
    p = Path(path).resolve()
    for cand in [p] + list(p.parents):
        try:
            if (cand / ".git").exists():
                return str(cand)
        except OSError:
            return None
    return None

def is_external(target):
    t = target.lower()
    return t.startswith(("http://", "https://", "mailto:", "tel:", "ftp://", "slack://"))

# One repo, several checkouts, and a link the owner clicks is opened by the app on
# the machine he is sitting at. A path that is absolute on ANOTHER machine is
# the largest class of dead link in this repo, and it never looks wrong: the
# viewer resolves a leading "/" against the session workspace root, so
# ~/... is looked up at C:~/... and simply fails.
FOREIGN_ROOTS = (
    ".",   # WSL automation host
    ".",                    # macOS
    "C:/Users/you/.gemini/antigravity/scratch/product-second-brain",  # Windows session
    "//wsl.localhost/Ubuntu.",
)

def foreign_machine_link(path_part, project):
    """If the target is a repo path written for a different machine, return the
    path it should carry on THIS machine. Otherwise return None."""
    p = path_part.replace("\\", "/")
    if p.lower().startswith("file:///"):
        p = "/" + p[8:]
    for root in FOREIGN_ROOTS:
        if p.lower().startswith(root.lower() + "/"):
            tail = p[len(root) + 1:]
            here = os.path.normpath(os.path.join(project, tail.replace("/", os.sep)))
            here = here.replace("\\", "/")
            mine = os.path.normpath(p).replace("\\", "/")
            if here.lower() == mine.lower():
                return None  # this machine's own root, so the link is correct
            return here
    return None

ABS_RE = re.compile(r"^(?:/|[A-Za-z]:[\\/])")

def is_absolute_link(path_part):
    return bool(ABS_RE.match(path_part))

def app_facing(doc_path, project):
    """True for documents the owner opens in the app rather than on GitHub.

    The app resolves a relative link from the workspace root, not from the
    folder the document sits in, so a relative link inside one of these is dead
    on arrival even though the file it names exists. Repo prose (CLAUDE.md,
    docs/, .agent/) is read in an editor or on GitHub, where relative is
    correct, so it is left alone.
    """
    try:
        rel = os.path.relpath(os.path.abspath(doc_path), project).replace("\\", "/")
    except ValueError:
        return False
    return rel.split("/")[0] in ("journal", "Clients", "inbox", "_temp")

def check_text(text, doc_path, project):
    """Return (broken, unlinked, fragile) for one document's text.

    broken:   [(label, target, hint)] the reader cannot open this
    unlinked: [(name, repo-relative path)] named in prose but never linked
    fragile:  [(label, target, absolute form)] opens for me, dies for the owner
    """
    doc_dir = os.path.dirname(os.path.abspath(doc_path)) or project

    broken = []
    fragile = []
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
        here = foreign_machine_link(path_part, project)
        if here is not None:
            hint = "written for another machine, on this one it is " + here
            if not os.path.exists(here):
                hint += " (which does not exist either)"
            broken.append((label or target, target, hint))
            continue
        hit = next((c for c in candidates if os.path.exists(c)), None)
        if hit is None:
            broken.append((label or target, target, ""))
            continue

        # A folder is not openable. The app says "is not a file" and the reader
        # is stuck, even though the path is perfectly correct. Link a file
        # inside it, or the index that lists its contents.
        if os.path.isdir(hit):
            index = next((os.path.join(hit, n) for n in ("README.md", "index.md")
                          if os.path.exists(os.path.join(hit, n))), None)
            hint = ("points at a folder, and the app can only open files"
                    + (f"; link {os.path.relpath(index, project)} instead" if index
                       else "; link one file inside it instead"))
            broken.append((label or target, target, hint))
            continue

        # Resolves here, dies for the owner: the app resolves a relative link from
        # the workspace root, not from this file's folder.
        if app_facing(doc_path, project) and not is_absolute_link(path_part):
            fragile.append((label or target, target, hit.replace("\\", "/")))

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
    return broken, unlinked, fragile

_INDEX = {}

def find_in_repo(name, project):
    """First file with this basename in the repo, or None. Indexed once per run."""
    if not _INDEX:
        for root, dirs, files in os.walk(project):
            dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "_archive", "_temp", "scratch", ".venv")]
            for f in files:
                _INDEX.setdefault(f, os.path.join(root, f))
    return _INDEX.get(name)

def report(doc_rel, broken, unlinked, fragile=()):
    lines = []
    if broken:
        lines.append(f"{doc_rel}: {len(broken)} link(s) the reader cannot open:")
        for entry in broken:
            label, target = entry[0], entry[1]
            hint = entry[2] if len(entry) > 2 else "no file at this path"
            lines.append(f"  - [{label}]({target})  <- {hint}")
    if fragile:
        lines.append(f"{doc_rel}: {len(fragile)} relative link(s) that resolve here and die "
                     f"for the owner (the app resolves from the workspace root):")
        for label, target, absolute in fragile:
            lines.append(f"  - [{label}]({target})  <- write it as {absolute}")
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
            # The write may have gone to another checkout of this same repo,
            # by UNC path from Windows into WSL for instance. That is exactly
            # when a machine-specific link gets written, so check it there
            # rather than skipping, using that checkout as the root.
            owner = git_root_of(norm)
            if not owner:
                sys.exit(0)
            project = owner
        if any(s in norm for s in SKIP_DIRS):
            sys.exit(0)
        if not os.path.exists(norm):
            sys.exit(0)

        with open(norm, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()

        broken, unlinked, fragile = check_text(text, norm, project)
        if not broken and not unlinked and not fragile:
            sys.exit(0)

        rel = os.path.relpath(norm, project)
        lines = report(rel, broken, unlinked, fragile)
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

def as_link_target(abs_path):
    """An absolute path safe to put inside a markdown link target."""
    return abs_path.replace("\\", "/").replace(" ", "%20")

def fix_text(text, doc_path, project):
    """Rewrite every link that resolves on this machine but not for the reader.

    Covers the two classes the guard can repair without guessing: a relative
    link inside an app-facing document, and a link written for another
    machine's checkout. A link whose target does not exist at all is left
    alone, because there is nothing to point it at.
    """
    broken, _unlinked, fragile = check_text(text, doc_path, project)
    doc_dir = os.path.dirname(os.path.abspath(doc_path)) or project
    swaps = {}

    for label, target, absolute in fragile:
        swaps[target] = as_link_target(absolute)

    for entry in broken:
        target = entry[1]
        hint = entry[2] if len(entry) > 2 else ""
        if not hint.startswith("written for another machine"):
            continue
        path_part = unquote(target.split("#")[0])
        here = foreign_machine_link(path_part, project)
        if here and os.path.isfile(here):
            swaps[target] = as_link_target(here)

    out, n = text, 0
    for old, new in swaps.items():
        if old == new:
            continue
        needle = "](" + old + ")"
        if needle in out:
            out = out.replace(needle, "](" + new + ")")
            n += 1
    return out, n

def run_cli(targets, fix=False):
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
    fixed_docs = fixed_links = 0
    for doc in sorted(docs):
        try:
            with open(doc, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except Exception:
            continue
        if fix:
            new, n = fix_text(text, doc, project)
            if n:
                with open(doc, "w", encoding="utf-8") as fh:
                    fh.write(new)
                text = new
                fixed_docs += 1
                fixed_links += n
                print(f"fixed {n} link(s) in {os.path.relpath(doc, project)}")
        broken, unlinked, fragile = check_text(text, doc, project)
        if broken or unlinked or fragile:
            bad += 1
            print("\n".join(report(os.path.relpath(doc, project), broken, unlinked, fragile)))
    if fixed_links:
        print(f"\nrewrote {fixed_links} link(s) across {fixed_docs} document(s).")
    if bad:
        print(f"\n{bad} document(s) with link problems.")
        return 1
    print(f"{len(docs)} document(s) checked, every link opens.")
    return 0

if __name__ == "__main__":
    try:
        argv = sys.argv[1:]
        fix = "--fix" in argv
        argv = [a for a in argv if a != "--fix"]
        if argv:
            sys.exit(run_cli(argv, fix=fix))
        run_hook()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
