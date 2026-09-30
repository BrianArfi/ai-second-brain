"""Parse CHANGELOG.md into sections for the dashboard's What's new tab.

Pure standard library and no I/O beyond the one read in `load()`, so
`dashboard/test_changelog.py` can test it without a running server.

Safety: the browser renders each section body with `U.mdToHtml`, which escapes
all HTML before it formats anything. This module does three more things
before the text leaves the server:

- drops HTML comments (the `<!-- source: ... -->` lines), which would
  otherwise show up as escaped literal text;
- turns links to repository files into plain text. The dashboard drawer can
  only open files under a few allowed folders, so a link to
  `docs/VERSIONING.md` would open an "Access denied" drawer. Web links
  (http, https) and in-page links (#...) are kept;
- folds hard-wrapped list items back onto one line, because the renderer
  treats every line on its own.
"""
import re
from pathlib import Path

# "## [0.7.0] - 2026-09-28", "## v0.1.0 - 2026-08-23", "## [Unreleased]",
# "## Unreleased", or a plain dated heading "## 2026-08-14".
_HEADING = re.compile(r"^##\s+(?!#)(.+?)\s*$")
_VERSION = re.compile(r"^\[?v?(\d+\.\d+\.\d+)\]?(?:\s*-\s*(\d{4}-\d{2}-\d{2}))?")
_UNRELEASED = re.compile(r"^\[?unreleased\]?$", re.I)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")

def _flatten_repo_links(text: str) -> str:
    def repl(m):
        url = m.group(2)
        if re.match(r"^(https?:|#)", url, re.I):
            return m.group(0)
        return m.group(1)
    return _LINK.sub(repl, text)

_ITEM = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+")

def _join_wrapped_items(text: str) -> str:
    """Fold a hard-wrapped list item back onto one line.

    The changelog wraps bullets at ~85 columns with an indented continuation.
    The dashboard's markdown renderer reads each line on its own, so without
    this every continuation shows as a separate paragraph under its bullet.
    """
    out, in_fence, in_item = [], False, False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            in_item = False
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue
        if _ITEM.match(line):
            in_item = True
            out.append(line)
        elif in_item and line.startswith("  ") and line.strip():
            out[-1] = out[-1].rstrip() + " " + line.strip()
        else:
            in_item = False
            out.append(line)
    return "\n".join(out)

def clean(text: str) -> str:
    """Strip HTML comments, flatten repository links, unwrap list items."""
    text = _COMMENT.sub("", text)
    text = _flatten_repo_links(text)
    text = _join_wrapped_items(text)
    # Comments often sit on their own line; collapse the blank run they leave.
    return re.sub(r"\n{3,}", "\n\n", text).strip()

def parse(text: str) -> dict:
    """Split a changelog into its preamble and its `## ` sections.

    Returns {"intro": str, "sections": [{"title", "version", "date",
    "unreleased", "body"}, ...]} in file order (newest first by convention).
    `version` is "x.y.z" or None; `date` is "YYYY-MM-DD" or None.
    Headings inside fenced code blocks are not treated as sections.
    """
    intro_lines, sections, cur = [], [], None
    in_fence = False
    for line in (text or "").splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        m = None if in_fence else _HEADING.match(line)
        if m:
            title = m.group(1).strip()
            vm = _VERSION.match(title)
            date = vm.group(2) if vm else None
            if not date:
                dm = re.match(r"^(\d{4}-\d{2}-\d{2})", title)
                date = dm.group(1) if dm else None
            cur = {
                "title": title,
                "version": vm.group(1) if vm else None,
                "date": date,
                "unreleased": bool(_UNRELEASED.match(title)),
                "_lines": [],
            }
            sections.append(cur)
            continue
        if cur is None:
            intro_lines.append(line)
        else:
            cur["_lines"].append(line)

    out = []
    for s in sections:
        body = clean("\n".join(s.pop("_lines")))
        s["body"] = body
        out.append(s)
    intro = "\n".join(l for l in intro_lines if not l.startswith("# "))
    return {"intro": clean(intro), "sections": out}

def latest_release(parsed: dict):
    """The first section that carries a version number, or None."""
    for s in parsed.get("sections", []):
        if s.get("version"):
            return s
    return None

def load(path: Path) -> dict:
    """Read and parse a changelog file. Missing file -> empty result."""
    path = Path(path)
    if not path.is_file():
        return {"intro": "", "sections": [], "missing": True}
    parsed = parse(path.read_text(encoding="utf-8"))
    rel = latest_release(parsed)
    parsed["latest"] = rel["version"] if rel else None
    return parsed
