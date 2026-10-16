#!/usr/bin/env python3
"""Refer one Claude Code session from another.

Reads the local session transcripts (~/.claude/projects/<slug>/*.jsonl) and
prints a compact brief of a chosen session, so the current session can pick
up what that one did without the user re-explaining it.

  session_ref.py here                      # this session's shareable reference
  session_ref.py list [--days 3] [--grep KEYWORD]
  session_ref.py brief <ref> [<ref> ...] [--max-chars 12000]

A reference looks like `/session f245e1e3`: paste it into any other session.
<ref> is a session id or id prefix (`f245e1e3`, `session:f245e1e3` also work),
"last" / "last-2" for the Nth previous session, or any keyword: the most recent
session whose text contains it wins. The current session (CLAUDE_CODE_SESSION_ID,
else the newest transcript) is skipped unless --include-current is passed.

Session cards (session_cards.py, journal/sessions/ in git) are read first: they cross
machines, so a Windows session sees a WSL or macOS session through its card. A transcript
is opened only when the card is missing or the brief needs the detail, and transcripts are
per machine and not in git. Stdlib only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

LINK_RE = re.compile(
    r"https://(?:work-artifacts\.pages\.dev/[\w\-/]+"
    r"|docs\.google\.com/[\w\-/?=.&#]+"
    r"|drive\.google\.com/[\w\-/?=.&#]+"
    r"|[\w-]+\.slack\.com/archives/[\w/?=.&\-]+"
    r"|linear\.app/[\w\-/]+"
    r"|[\w-]+\.atlassian\.net/browse/[\w-]+)"
)
LEDGER_RE = re.compile(r"\b(?:COM|WAIT|DEC|CHASE)-\d{3,5}\b")

def projects_roots() -> list[Path]:
    roots = [Path.home() / ".claude" / "projects"]
    # A WSL shell driving the Windows-side CLI writes transcripts under /mnt/c/Users/<u>/.claude.
    for users in (Path("/mnt/c/Users"),):
        if users.is_dir():
            roots += [u / ".claude" / "projects" for u in users.iterdir() if (u / ".claude" / "projects").is_dir()]
    return [r for r in roots if r.is_dir()]

def workspace_root() -> Path:
    for var in ("ASB_WORKSPACE", "CLAUDE_PROJECT_DIR"):
        if os.environ.get(var):
            return Path(os.environ[var])
    return Path.cwd()

def project_dirs(all_projects: bool) -> list[Path]:
    roots = projects_roots()
    if not roots:
        sys.exit("No Claude transcripts folder found on this machine.")
    every = [d for r in roots for d in r.iterdir() if d.is_dir()]
    if all_projects:
        return every
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(workspace_root())).lower()
    mine = [d for d in every if d.name.lower() == slug]
    # No folder for this workspace (renamed path, other shell): search everything rather than fail.
    return mine or every

def transcripts(all_projects: bool) -> list[Path]:
    files = [f for d in project_dirs(all_projects) for f in d.glob("*.jsonl")]
    return sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)

def _text_blocks(content) -> list[str]:
    if isinstance(content, str):
        return [content]
    out = []
    for b in content or []:
        if isinstance(b, dict) and b.get("type") == "text":
            out.append(b.get("text", ""))
    return out

def _is_noise(text: str) -> bool:
    t = text.strip()
    return not t or t.startswith(("<system-reminder", "<local-command", "<command-", "Caveat:", "[Request interrupted",
                                    "<task-notification", "Stop hook feedback", "This session is being continued"))

def parse(path: Path) -> dict:
    s = {"id": path.stem, "path": path, "prompts": [], "replies": [], "files": [],
         "links": [], "ledger": [], "start": None, "end": None, "title": None}
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            ts = e.get("timestamp")
            if ts:
                s["start"] = s["start"] or ts
                s["end"] = ts
            kind = e.get("type")
            if kind in ("summary", "ai-title", "custom-title"):
                s["title"] = e.get("summary") or e.get("title") or e.get("customTitle") or s["title"]
            if e.get("isSidechain"):
                continue
            msg = e.get("message") or {}
            if kind == "user":
                for t in _text_blocks(msg.get("content")):
                    if not _is_noise(t):
                        s["prompts"].append(t.strip())
            elif kind == "assistant":
                for b in msg.get("content") or []:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text" and b.get("text", "").strip():
                        s["replies"].append(b["text"].strip())
                    elif b.get("type") == "tool_use":
                        fp = (b.get("input") or {}).get("file_path")
                        if fp and b.get("name") in ("Write", "Edit", "NotebookEdit"):
                            s["files"].append(fp)
    blob = "\n".join(s["prompts"] + s["replies"])
    s["links"] = list(dict.fromkeys(LINK_RE.findall(blob)))
    s["ledger"] = list(dict.fromkeys(LEDGER_RE.findall(blob)))
    s["files"] = list(dict.fromkeys(s["files"]))
    return s

def local_time(ts: str | None) -> str:
    if not ts:
        return "?"
    # The machine's own timezone: the reader is the person at this machine.
    return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime("%a %d %b %H:%M")

def clip(text: str, n: int) -> str:
    text = re.sub(r"\s+", " ", text)
    return text if len(text) <= n else text[: n - 3] + "..."

def resolve(ref: str, files: list[Path]) -> Path:
    ref = ref.strip().removeprefix("session:").removeprefix("/session").strip()
    m = re.fullmatch(r"last(?:-(\d+))?", ref)
    if m:
        idx = int(m.group(1) or 1) - 1
        if idx >= len(files):
            sys.exit("Not that many previous sessions.")
        return files[idx]
    hits = [f for f in files if f.stem.startswith(ref)]
    if hits:
        return hits[0]
    needle = ref.lower()
    for f in files:  # newest first
        if needle in f.read_text(encoding="utf-8", errors="replace").lower():
            return f
    sys.exit(f"No session matches '{ref}'.")

def cmd_here(a, _files):
    cur = os.environ.get("CLAUDE_CODE_SESSION_ID")
    files = transcripts(a.all_projects)
    path = next((f for f in files if f.stem == cur), None) if cur else (files[0] if files else None)
    if not path:
        sys.exit("Cannot tell which session this is.")
    s = parse(path)
    print(f"/session {s['id'][:8]}")
    print(f"Title: {s['title'] or (clip(s['prompts'][0], 80) if s['prompts'] else '?')}")
    print(f"Full id: {s['id']}")
    print(f"Transcript: {s['path'].as_posix()}")

def _cards():
    """Session cards for this workspace, or [] when the card module or folder is missing."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import session_cards
        return session_cards, session_cards.load_cards()
    except Exception:
        return None, []

def cmd_list(a, files):
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=a.days)
    mod, cards = _cards()
    cur = os.environ.get("CLAUDE_CODE_SESSION_ID")
    covered = set()
    for c in cards:
        covered.add(c["session_id"])
        last = mod.parse_ts(c.get("last_active_at"))
        if (c["session_id"] == cur and not a.include_current) or not last or last < cutoff:
            continue
        if a.grep and not mod.matches(c, a.grep):
            continue
        print(f"{c['short_id']}  {local_time(c.get('created_at'))} -> {local_time(c.get('last_active_at'))}  "
              f"{c.get('status', '?'):<15} [{c.get('machine', '?')}] {c.get('spoken_name')} - {clip(c.get('purpose', ''), 80)}")
    for f in files:
        if dt.datetime.fromtimestamp(f.stat().st_mtime, dt.timezone.utc) < cutoff:
            break
        if f.stem in covered:
            continue
        if a.grep and a.grep.lower() not in f.read_text(encoding="utf-8", errors="replace").lower():
            continue
        s = parse(f)
        if not s["prompts"]:
            continue
        label = s["title"] or s["prompts"][0]
        print(f"{s['id'][:8]}  {local_time(s['start'])} -> {local_time(s['end'])}  {len(s['prompts']):>3} asks  {clip(label, 90)}")

def brief(s: dict, max_chars: int) -> str:
    out = [f"# Session {s['id'][:8]} ({local_time(s['start'])} -> {local_time(s['end'])})"]
    if s["title"]:
        out.append(f"Title: {s['title']}")
    out.append(f"Transcript: {s['path'].as_posix()}")
    out.append("\n## What was asked")
    out += [f"{i}. {clip(p, 400)}" for i, p in enumerate(s["prompts"], 1)]
    if s["files"]:
        out.append("\n## Files written or edited")
        out += [f"- {p}" for p in s["files"][:40]]
    if s["links"]:
        out.append("\n## Links produced or cited")
        out += [f"- {u}" for u in s["links"][:30]]
    if s["ledger"]:
        out.append("\n## Ledger ids touched\n" + ", ".join(s["ledger"][:40]))
    out.append("\n## Where it ended (last assistant replies)")
    out += [clip(r, 1500) for r in s["replies"][-3:]]
    text = "\n".join(out)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[truncated; read the transcript for the rest]"
    return text

def _brief_for(ref: str, files: list[Path], per: int, mod, cards: list[dict]) -> str:
    """Card first. With a local transcript the card header sits on top of the full brief;
    without one (another machine) the card alone is the brief."""
    r = ref.strip().removeprefix("session:").removeprefix("/session").strip()
    cur = os.environ.get("CLAUDE_CODE_SESSION_ID")
    pool = [c for c in cards if c["session_id"] != cur] if cur else cards
    card = None if re.fullmatch(r"last(?:-\d+)?", r) else (mod.find_card(r, pool) if mod else None)
    if card:
        local = next((f for f in files if f.stem == card["session_id"]), None)
        head = mod.card_brief(card)
        if local:
            return head + "\n\n" + brief(parse(local), per)
        return head
    return brief(parse(resolve(ref, files)), per)

def cmd_brief(a, files):
    per = max(2000, a.max_chars // len(a.refs))
    mod, cards = _cards()
    print("\n\n".join(_brief_for(r, files, per, mod, cards) for r in a.refs))

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--all-projects", action="store_true", help="search every project folder, not only this workspace")
    p.add_argument("--include-current", action="store_true", help="do not skip the current session")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("here")
    l = sub.add_parser("list")
    l.add_argument("--days", type=int, default=3)
    l.add_argument("--grep")
    b = sub.add_parser("brief")
    b.add_argument("refs", nargs="+")
    b.add_argument("--max-chars", type=int, default=12000)
    a = p.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if a.cmd == "here":
        return cmd_here(a, None)
    files = transcripts(a.all_projects)
    if not a.include_current:
        cur = os.environ.get("CLAUDE_CODE_SESSION_ID")
        files = [f for f in files if f.stem != cur] if cur else files[1:]
    (cmd_list if a.cmd == "list" else cmd_brief)(a, files)

if __name__ == "__main__":
    main()
