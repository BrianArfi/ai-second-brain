#!/usr/bin/env python3
"""Session cards: one small JSON record per Claude session, readable from any machine.

A card says what a session is for, where it stands and what it produced. It points to
the transcript and never copies it. In this repo cards live in journal/sessions/ (git
tracked), so Windows, WSL and macOS sessions can see each other.

  session_cards.py list [--active-hours 24] [--node X] [--grep WORD] [--all-hosts]
  session_cards.py show <ref>            # id prefix, number from `list`, or a keyword
  session_cards.py active-brief [--max 5] [--exclude ID]
  session_cards.py set-status <ref> <active|waiting_on_user|done|abandoned>
  session_cards.py refine [--max 10]     # better purpose + spoken name from a cheap model (never in the Stop hook)

Card dir (first match wins): sessionCardsDir in <workspace>/.asb/config.json (relative to
the workspace), else journal/sessions when journal/state/work_tree.json exists, else
.asb/sessions. Notes live in <cardDir>/../notes, claims in <cardDir>/../claims.json.

Stdlib only. As a library: card_dir(), load_cards(), write_card(), build_card().
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

VERSION = 1
STATUSES = ("active", "waiting_on_user", "done", "abandoned")
OPEN_STATUSES = ("active", "waiting_on_user")
HERE = Path(__file__).resolve().parent
NODE_TAG_RE = re.compile(r"\[node:([A-Za-z0-9_.\-]+)\]|--node[ =]([A-Za-z0-9_.\-]+)")
STOPWORDS = set("""a an the to of for in on at and or but with from by is are be this that it its my our your
please pls can could would you i we me us do does did make create write build run just
tolong coba bisa mau ayo yuk dong ya yang di ke dari untuk dan atau dengan ini itu aku kamu kita
buat buatkan bikin bikinin tambah tambahin lanjut lanjutin cek check""".split())
TAIL_STRIP_RE = re.compile(r"^(save this as a rule\?|branchable:)", re.I)
# A final reply that reports the tracked item closed (or says the task is finished) marks the card done.
DONE_RE = re.compile(
    r"\b(?:COM|WAIT)-\d{3,5}\b[^.\n]{0,40}?\b(?:is|are|was|now)\s+(?:closed|done)\b"
    r"|\bclosed\s+(?:COM|WAIT)-\d{3,5}\b"
    r"|\bthis (?:task|session|chat) is (?:done|finished|complete)\b"
    r"|\b(?:tugas(?:mu)?|item(?:mu)?)(?: ini)? sudah (?:ditutup|selesai)\b", re.I)

# ---------- locations ----------

def workspace_root(workspace: str | os.PathLike | None = None) -> Path:
    if workspace:
        return Path(workspace)
    for var in ("ASB_WORKSPACE", "CLAUDE_PROJECT_DIR"):
        if os.environ.get(var) and Path(os.environ[var]).is_dir():
            return Path(os.environ[var])
    return HERE.parent.parent if (HERE.parent.parent / "journal").is_dir() else Path.cwd()

def card_dir(workspace: str | os.PathLike | None = None) -> Path:
    ws = workspace_root(workspace)
    cfg = ws / ".asb" / "config.json"
    if cfg.is_file():
        try:
            rel = json.loads(cfg.read_text(encoding="utf-8")).get("sessionCardsDir")
            if rel:
                p = Path(rel)
                return p if p.is_absolute() else ws / p
        except (OSError, ValueError):
            pass
    if (ws / "journal" / "state" / "work_tree.json").is_file():
        return ws / "journal" / "sessions"
    return ws / ".asb" / "sessions"

def notes_dir(workspace=None) -> Path:
    return card_dir(workspace).parent / "notes"

def claims_path(workspace=None) -> Path:
    return card_dir(workspace).parent / "claims.json"

# ---------- time ----------

def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def parse_ts(ts: str | None) -> dt.datetime | None:
    if not ts:
        return None
    try:
        d = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)

def norm_iso(ts: str | None) -> str | None:
    d = parse_ts(ts)
    return d.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if d else None

# ---------- store ----------

def read_card(path: Path) -> dict | None:
    try:
        c = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return c if isinstance(c, dict) and c.get("session_id") else None

def load_cards(workspace=None, cards_dir: Path | None = None) -> list[dict]:
    d = cards_dir or card_dir(workspace)
    if not d.is_dir():
        return []
    cards = [c for c in (read_card(p) for p in d.glob("*.json")) if c]
    cards.sort(key=lambda c: c.get("last_active_at") or "", reverse=True)
    return cards

def write_card(card: dict, workspace=None, cards_dir: Path | None = None) -> Path:
    """Atomic write (tmp + rename). Callers merge into their own card first."""
    d = cards_dir or card_dir(workspace)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{card['session_id']}.json"
    tmp = d / f".{card['session_id']}.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(card, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    return path

def matches(c: dict, needle: str) -> bool:
    hay = " ".join([c.get("spoken_name", ""), c.get("purpose", ""), *c.get("nodes", []), *c.get("ledger_ids", []),
                    *(o.get("label", "") + " " + o.get("url", "") for o in c.get("outputs", []))])
    return needle.lower() in hay.lower()

def find_card(ref: str, cards: list[dict]) -> dict | None:
    """Id prefix first, then a keyword in name, purpose, nodes, ledger ids or outputs (newest card wins)."""
    ref = ref.strip().removeprefix("session:").removeprefix("/session").strip()
    if not ref:
        return None
    for c in cards:
        if c["session_id"].startswith(ref) or c.get("short_id") == ref:
            return c
    return next((c for c in cards if matches(c, ref)), None)

# ---------- building a card from a transcript ----------

def _session_ref():
    sys.path.insert(0, str(HERE))
    import session_ref  # noqa: E402  (lazy: session_ref imports this module lazily too)
    return session_ref

def clip(text: str, n: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[: n - 3].rstrip() + "..."

def clean_prompt(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"^\[[^\]\n]{0,120}\]\s*", "", t)          # leading "[Workflow harness ...]" tag
    t = re.sub(r"^/[\w:\-]+\s*", "", t) or t               # leading slash command
    return t.strip()

def spoken_name(prompt: str, fallback: str) -> str:
    """2 to 4 words, deterministic: the first meaningful words of the first prompt."""
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]*", clean_prompt(prompt))
    keep = [w for w in words if w.lower() not in STOPWORDS and len(w) > 1][:3]
    if len(keep) < 2:
        keep = (keep + [w for w in words if w not in keep])[:2]
    if not keep:
        return fallback
    return " ".join(w if w.isupper() or any(ch.isdigit() for ch in w) else w.capitalize() for w in keep)

def purpose_line(prompt: str) -> str:
    t = clean_prompt(prompt)
    first = re.split(r"(?<=[.?!])\s|\n", t, maxsplit=1)[0] if t else ""
    return clip(first or t, 140)

def strip_reply_tail(text: str) -> str:
    """Drop the Ringkasan block and the /learn or Branchable closing lines before judging the ending."""
    cut = re.search(r"\*\*Ringkasan\*\*|^#+\s*Ringkasan", text, re.M)
    if cut:
        text = text[: cut.start()]
    lines = [ln for ln in text.rstrip().splitlines()]
    while lines and (not lines[-1].strip() or TAIL_STRIP_RE.match(lines[-1].strip())):
        lines.pop()
    return "\n".join(lines).strip()

def ends_in_question(reply: str) -> bool:
    body = strip_reply_tail(reply)
    if not body:
        return False
    last = body.splitlines()[-1].strip().rstrip("*_`\")]> ")
    return last.endswith("?")

def reports_done(reply: str) -> bool:
    """The reply closes the session's work: a closure line, no question, no open checkbox, no branch offer."""
    if not reply or ends_in_question(reply) or re.search(r"^\s*[-*]\s*\[ \]", reply, re.M):
        return False
    if re.search(r"^\s*branchable:", reply, re.I | re.M):
        return False
    return bool(DONE_RE.search(reply))

def last_question(reply: str) -> str:
    body = strip_reply_tail(reply)
    last = body.splitlines()[-1].strip() if body else ""
    sentences = re.split(r"(?<=[.!?])\s+", last)
    return clip(sentences[-1] if sentences else last, 160)

def known_node_ids(ws: Path) -> set[str] | None:
    p = ws / "journal" / "state" / "work_tree.json"
    if not p.is_file():
        return None
    try:
        tree = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ids: set[str] = set()
    stack = list(tree.get("roots") or [])
    while stack:
        n = stack.pop()
        if isinstance(n, dict):
            if n.get("id"):
                ids.add(str(n["id"]))
            stack.extend(n.get("children") or [])
    return ids or None

def label_for(url: str) -> str:
    m = re.match(r"https://([^/]+)/?([^?#]*)", url)
    host, tail = (m.group(1), m.group(2)) if m else (url, "")
    kind = ("Google Doc" if "docs.google" in host else "Drive" if "drive.google" in host else
            "Slack" if "slack.com" in host else "Linear" if "linear.app" in host else
            "Jira" if "atlassian" in host else "Artifact" if "pages.dev" in host else host)
    last = [p for p in tail.split("/") if p][-1:] or [""]
    return clip(f"{kind} {last[0]}".strip(), 60)

def source_kind() -> str:
    forced = os.environ.get("ASB_SESSION_SOURCE")
    if forced in ("cli", "app", "talk"):
        return forced
    entry = (os.environ.get("CLAUDE_CODE_ENTRYPOINT") or "").lower()
    return "app" if ("desktop" in entry or "asb" in entry) else "cli"

def build_card(transcript: Path, workspace=None, existing: dict | None = None, session_id: str | None = None) -> dict:
    """Build or merge-update this session's card from its transcript. Existing wins on
    identity fields (name, purpose, parent, children, created_at); facts accumulate."""
    ws = workspace_root(workspace)
    sr = _session_ref()
    s = sr.parse(transcript)
    sid = session_id or s["id"]
    first = s["prompts"][0] if s["prompts"] else ""
    ex = existing or {}
    default_name = f"Session {sid[:8]}"

    derived_name = spoken_name(first, default_name)
    derived_purpose = purpose_line(first)
    title = clip(s["title"], 140) if s.get("title") else ""
    name = ex.get("spoken_name") or derived_name
    purpose = ex.get("purpose") or title or derived_purpose
    if title and ex.get("purpose") == derived_purpose:      # prompt-derived line upgraded once a real title exists
        purpose = title
        if ex.get("spoken_name") == derived_name:
            name = spoken_name(title, default_name)

    blob = "\n".join(s["prompts"] + s["replies"])
    node_ids = [a or b for a, b in NODE_TAG_RE.findall(blob)]
    known = known_node_ids(ws)
    if known is not None:
        node_ids = [n for n in node_ids if n in known]
    nodes = list(dict.fromkeys([*ex.get("nodes", []), *node_ids]))

    seen = {o.get("url") for o in ex.get("outputs", [])}
    outputs = list(ex.get("outputs", []))
    for url in s["links"]:
        if url not in seen:
            outputs.append({"label": label_for(url), "url": url})
            seen.add(url)
    for fp in s["files"]:
        if re.search(r"\.(html|pdf|mp4|pptx|docx|xlsx|png)$", fp, re.I) and fp not in seen:
            outputs.append({"label": clip(Path(fp).name, 60), "url": fp.replace("\\", "/")})
            seen.add(fp)
    outputs = outputs[-12:]

    last_reply = s["replies"][-1] if s["replies"] else ""
    waiting = bool(last_reply) and ends_in_question(last_reply)
    last_active = norm_iso(s["end"]) or now_iso()
    status = "waiting_on_user" if waiting else "done" if reports_done(last_reply) else "active"
    if ex.get("status") in ("done", "abandoned") and last_active <= (ex.get("last_active_at") or ""):
        status = ex["status"]                                 # no new activity since it was closed

    items: list[str] = []
    if waiting:
        items.append(last_question(last_reply))
    items += [clip(m, 160) for m in re.findall(r"^\s*[-*]\s*\[ \]\s*(.+)$", strip_reply_tail(last_reply), re.M)]
    open_items = list(dict.fromkeys(i for i in items if i))[:3]

    card = {
        "version": VERSION,
        "session_id": sid,
        "short_id": sid[:8],
        "spoken_name": name,
        "purpose": purpose,
        "workspace": str(ws).replace("\\", "/"),
        "machine": ex.get("machine") or socket.gethostname(),
        "source": ex.get("source") or source_kind(),
        "hand_made": ex.get("hand_made", True),
        "nodes": nodes,
        "status": status,
        "outputs": outputs,
        "ledger_ids": list(dict.fromkeys([*ex.get("ledger_ids", []), *s["ledger"]]))[-40:],
        "open_items": open_items,
        "parent_id": ex.get("parent_id"),
        "children": ex.get("children", []),
        "transcript_path": str(transcript).replace("\\", "/"),
        "created_at": ex.get("created_at") or norm_iso(s["start"]) or now_iso(),
        "last_active_at": last_active,
    }
    if ex.get("purpose_refined"):
        card["purpose_refined"] = True                        # set by `refine`; the Stop hook only carries it
    return card

def update_from_transcript(transcript: Path, workspace=None, session_id: str | None = None) -> Path:
    cards = card_dir(workspace)
    sid = session_id or transcript.stem
    ex = read_card(cards / f"{sid}.json")
    card = build_card(transcript, workspace, ex, sid)
    if card["status"] == "done" and (ex or {}).get("status") != "done":
        report_done_to_app(sid, workspace)
    return write_card(card, workspace)

def report_done_to_app(session_id: str, workspace=None) -> Path | None:
    """Tell the ASB app the session is finished, so its rail shows a check, not a hollow circle.
    The app reads .asb/branches/status/<id>.json for any session, applies it, and moves the file.
    A later turn sets the app status back to idle by itself. Skipped outside an ASB workspace."""
    status_dir = workspace_root(workspace) / ".asb" / "branches" / "status"
    if not status_dir.parent.is_dir():
        return None
    status_dir.mkdir(exist_ok=True)
    path = status_dir / f"{session_id}.json"
    fd, tmp = tempfile.mkstemp(dir=status_dir, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"status": "done"}, fh)
    os.replace(tmp, path)
    return path

def current_transcript(session_id: str | None, hinted: str | None = None) -> tuple[str, Path] | None:
    """Locate this session's transcript: the path the hook was given, else by session id, else newest."""
    if hinted and Path(hinted).is_file():
        return Path(hinted).stem, Path(hinted)
    sr = _session_ref()
    files = sr.transcripts(False)
    sid = session_id or os.environ.get("CLAUDE_CODE_SESSION_ID")
    if sid:
        hit = next((f for f in files if f.stem == sid), None)
        return (sid, hit) if hit else None
    return (files[0].stem, files[0]) if files else None

# ---------- display ----------

def brief_line(c: dict) -> str:
    return f"{c.get('short_id') or c['session_id'][:8]} {c.get('spoken_name', '?')} - {c.get('status', '?')} - {clip(c.get('purpose', ''), 90)}"

def local_time(ts: str | None) -> str:
    d = parse_ts(ts)
    return d.astimezone().strftime("%a %d %b %H:%M") if d else "?"

def filter_cards(cards: list[dict], active_hours: float = 24, node: str | None = None, grep: str | None = None,
                 exclude: str | None = None, open_only: bool = False) -> list[dict]:
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=active_hours) if active_hours else None
    out = []
    for c in cards:
        if exclude and c["session_id"] == exclude:
            continue
        if open_only and c.get("status") not in OPEN_STATUSES:
            continue
        last = parse_ts(c.get("last_active_at"))
        if cutoff and (not last or last < cutoff):
            continue
        if node and node not in c.get("nodes", []):
            continue
        if grep and not matches(c, grep):
            continue
        out.append(c)
    return out

def active_brief(cards: list[dict], limit: int = 5, exclude: str | None = None, hours: float = 24) -> list[str]:
    sel = filter_cards(cards, hours, exclude=exclude, open_only=True)
    return [brief_line(c) for c in sel[:limit]]

def card_brief(c: dict) -> str:
    out = [f"# Session {c['short_id']} - {c.get('spoken_name')} ({c.get('status')})",
           f"Purpose: {c.get('purpose')}",
           f"Last active: {local_time(c.get('last_active_at'))}  machine: {c.get('machine')}  workspace: {c.get('workspace')}"]
    if c.get("nodes"):
        out.append("Nodes: " + ", ".join(c["nodes"]))
    if c.get("outputs"):
        out.append("\n## Outputs")
        out += [f"- {o.get('label')}: {o.get('url')}" for o in c["outputs"]]
    if c.get("ledger_ids"):
        out.append("\n## Ledger ids touched\n" + ", ".join(c["ledger_ids"]))
    if c.get("open_items"):
        out.append("\n## Open items")
        out += [f"- {i}" for i in c["open_items"]]
    if c.get("parent_id") or c.get("children"):
        out.append(f"\nParent: {c.get('parent_id')}  Children: {', '.join(c.get('children', [])) or '-'}")
    tp = c.get("transcript_path")
    out.append(f"\nTranscript: {tp or 'unknown'} (on {c.get('machine')}; not in git)")
    return "\n".join(out)

# ---------- refine: a better purpose and spoken name from a cheap model ----------

REFINE_MIN_TURNS = 3
REFINE_SCAN_LIMIT = 60          # newest unrefined cards looked at per run, so a big card pile stays cheap
REFINE_TIMEOUT = 90
REFINE_PROMPT = (
    "You name a work session in a list of running sessions. Read the requests below and answer with "
    "exactly two lines and nothing else.\n"
    "PURPOSE: one plain English line under 90 characters that names the work (what is being made, "
    "fixed or decided). No filler, no quotes, no em-dash.\n"
    "NAME: a spoken name of 2 to 4 words, Title Case.\n"
    "Do not use tools.\n\nSession requests:\n"
)

def refine_context(prompts: list[str], replies: list[str]) -> str:
    """Small, bounded text for the summarizer: the first requests, the latest one, the latest reply's start."""
    first = [clip(clean_prompt(p), 240) for p in prompts[:4]]
    parts = [f"- {t}" for t in first if t]
    if len(prompts) > 4:
        parts.append(f"- (latest) {clip(clean_prompt(prompts[-1]), 240)}")
    if replies:
        parts.append(f"Assistant's latest reply starts: {clip(replies[-1], 300)}")
    return "\n".join(parts)

def parse_summary(text: str) -> tuple[str, str] | None:
    """Pull PURPOSE and NAME out of the model's answer. None when there is no usable purpose."""
    text = (text or "").translate({0x2014: "-", 0x2013: "-"})              # no em-dash or en-dash in a card
    pm = re.search(r"^\W*PURPOSE\s*:\s*(.+)$", text, re.I | re.M)
    nm = re.search(r"^\W*NAME\s*:\s*(.+)$", text, re.I | re.M)
    purpose = clip((pm.group(1) if pm else "").strip(" \"'`*"), 90)
    if not purpose:
        return None
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]*", nm.group(1) if nm else "")[:4]
    name = " ".join(w if w.isupper() or any(ch.isdigit() for ch in w) else w.capitalize() for w in words)
    return purpose, (name if len(words) >= 2 else "")

def claude_binary() -> str | None:
    forced = os.environ.get("ASB_CLAUDE_BIN")
    if forced:
        return forced
    found = shutil.which("claude")
    # In WSL, `which` can return the Windows CLI under /mnt/c, which is not logged in there.
    if found and not found.startswith("/mnt/"):
        return found
    for cand in (Path.home() / ".npm-global" / "bin" / "claude", Path.home() / ".local" / "bin" / "claude"):
        if cand.is_file():
            return str(cand)
    # The VS Code extension ships a native CLI; take the newest one.
    ext = sorted((Path.home() / ".vscode-server" / "extensions").glob("anthropic.claude-code-*/resources/native-binary/claude"))
    if ext:
        return str(ext[-1])
    return None

def summarize_with_claude(context: str) -> tuple[str, str] | None:
    """One `claude -p --model haiku` call. Runs from a temp dir with the card hook off, so the project hooks
    never load and this call cannot write a card of its own or start another refine."""
    binary = claude_binary()
    if not binary:
        raise RuntimeError("claude CLI not found")
    env = dict(os.environ, SESSION_CARD_DISABLE="1", ASB_REFINE="1", WORKTREE_SYNC_DISABLE="1")
    # Windows: refine runs detached with no console, so without CREATE_NO_WINDOW claude.exe gets a brand
    # new console, which Windows Terminal shows as a pop-up window (15 Oct 2026).
    kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
    with tempfile.TemporaryDirectory(prefix="asb-refine-") as tmp:
        proc = subprocess.run([binary, "-p", "--model", "haiku", "--no-session-persistence"],
                              input=REFINE_PROMPT + context, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=REFINE_TIMEOUT, cwd=tmp, env=env, **kw)
    if proc.returncode != 0:
        raise RuntimeError(f"claude exited {proc.returncode}")
    return parse_summary(proc.stdout)

def refine_candidates(cards: list[dict], min_turns: int = REFINE_MIN_TURNS):
    """Yield (card, parsed transcript) for cards whose purpose is still the raw first prompt and whose
    session has at least `min_turns` user turns. Cards come newest first."""
    sr = _session_ref()
    scanned = 0
    for c in cards:
        if c.get("purpose_refined"):
            continue
        tp = c.get("transcript_path")
        if not tp or not Path(tp).is_file():                  # another machine's transcript: not ours to read
            continue
        scanned += 1
        if scanned > REFINE_SCAN_LIMIT:
            return
        try:
            s = sr.parse(Path(tp))
        except OSError:
            continue
        if len(s["prompts"]) < min_turns:
            continue
        if (c.get("purpose") or "") != purpose_line(s["prompts"][0]):   # a title or an earlier edit already replaced it
            continue
        yield c, s

def refine_cards(workspace=None, max_cards: int = 10, summarizer=None, min_turns: int = REFINE_MIN_TURNS) -> list[dict]:
    """Refine up to `max_cards` cards. `summarizer(context) -> (purpose, name) | None` is injectable for tests.
    A model or CLI failure ends the run (no point asking again now); the cards stay unrefined for the next run.
    Returns the cards written."""
    summarizer = summarizer or summarize_with_claude
    cards_dir = card_dir(workspace)
    done: list[dict] = []
    for c, s in refine_candidates(load_cards(workspace, cards_dir), min_turns):
        if len(done) >= max_cards:
            break
        try:
            result = summarizer(refine_context(s["prompts"], s["replies"]))
        except (RuntimeError, OSError, subprocess.SubprocessError):
            break
        if not result:
            continue
        purpose, name = result
        fresh = read_card(cards_dir / f"{c['session_id']}.json") or c   # the Stop hook may have written meanwhile
        fresh["purpose"] = purpose
        if name:
            fresh["spoken_name"] = name
        fresh["purpose_refined"] = True
        write_card(fresh, workspace, cards_dir)
        done.append(fresh)
    return done

# ---------- CLI ----------

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--workspace")
    sub = p.add_subparsers(dest="cmd", required=True)
    l = sub.add_parser("list")
    l.add_argument("--active-hours", type=float, default=24, help="0 = no limit")
    l.add_argument("--node")
    l.add_argument("--grep")
    l.add_argument("--all-hosts", action="store_true", help="accepted for clarity: cards from every machine are always listed")
    sh = sub.add_parser("show")
    sh.add_argument("ref")
    ab = sub.add_parser("active-brief")
    ab.add_argument("--max", type=int, default=5)
    ab.add_argument("--exclude", default=os.environ.get("CLAUDE_CODE_SESSION_ID"))
    rf = sub.add_parser("refine")
    rf.add_argument("--max", type=int, default=10)
    ss = sub.add_parser("set-status")
    ss.add_argument("ref")
    ss.add_argument("status", choices=STATUSES)
    a = p.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cards = load_cards(a.workspace)
    if a.cmd == "list":
        sel = filter_cards(cards, a.active_hours, a.node, a.grep)
        if not sel:
            print("No session cards match.")
        for i, c in enumerate(sel, 1):
            print(f"{i:>2}. {c['short_id']}  {local_time(c.get('last_active_at'))}  {c.get('status'):<15} {c.get('machine', '?'):<12} "
                  f"{c.get('spoken_name')} - {clip(c.get('purpose', ''), 80)}")
    elif a.cmd == "show":
        c = find_card(a.ref, cards)
        print(card_brief(c) if c else f"No card matches '{a.ref}'.")
    elif a.cmd == "active-brief":
        for line in active_brief(cards, a.max, a.exclude):
            print(line)
    elif a.cmd == "refine":
        for c in refine_cards(a.workspace, a.max):
            print(brief_line(c))
    elif a.cmd == "set-status":
        c = find_card(a.ref, cards)
        if not c:
            sys.exit(f"No card matches '{a.ref}'.")
        c["status"] = a.status
        write_card(c, a.workspace)
        if a.status == "done":
            report_done_to_app(c["session_id"], a.workspace)
        print(f"{c['short_id']} -> {a.status}")

if __name__ == "__main__":
    main()
