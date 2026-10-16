"""Shared paths and helpers for cross-session coordination (claims, notes, mentions).

Contract (shared with session_cards.py and the ASB app):
  cardDir   = sessionCardsDir from <workspace>/.asb/config.json when present
              (relative to the workspace), else journal/sessions when
              journal/state/work_tree.json exists (PSB), else .asb/sessions.
  notes     = <cardDir>/../notes/<target>/<created_compact>-<from_short>.json
  claims    = <cardDir>/../claims.json
Cards are read directly from <cardDir>/<session_id>.json; this module never
writes a card (session_cards.py owns that).

Test and override hook: ASB_CARD_DIR (absolute) replaces the resolved cardDir.
"""
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent.parent
STALE_SECONDS = 12 * 3600

def card_dir(workspace=None):
    env = os.environ.get("ASB_CARD_DIR")
    if env:
        return Path(env)
    ws = Path(workspace) if workspace else WORKSPACE
    cfg = ws / ".asb" / "config.json"
    try:
        rel = (json.loads(cfg.read_text(encoding="utf-8")) or {}).get("sessionCardsDir")
        if rel:
            p = Path(rel)
            return p if p.is_absolute() else ws / p
    except Exception:
        pass
    if (ws / "journal" / "state" / "work_tree.json").exists():
        return ws / "journal" / "sessions"
    return ws / ".asb" / "sessions"

def notes_dir(workspace=None):
    return card_dir(workspace).parent / "notes"

def claims_path(workspace=None):
    return card_dir(workspace).parent / "claims.json"

def now_dt():
    return datetime.now(timezone.utc)

def iso(dt=None):
    return (dt or now_dt()).strftime("%Y-%m-%dT%H:%M:%SZ")

def compact(dt=None):
    return (dt or now_dt()).strftime("%Y%m%dT%H%M%SZ")

def parse_iso(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None

def age_seconds(s, now=None):
    dt = parse_iso(s)
    if not dt:
        return None
    return ((now or now_dt()) - dt).total_seconds()

def human_age(seconds):
    if seconds is None:
        return "?"
    s = int(max(seconds, 0))
    if s < 90:
        return "now"
    if s < 3600:
        return "%dm ago" % (s // 60)
    if s < 86400:
        return "%dh ago" % (s // 3600)
    return "%dd ago" % (s // 86400)

def current_session_id():
    for var in ("ASB_SESSION_ID", "CLAUDE_SESSION_ID"):
        if os.environ.get(var):
            return os.environ[var]
    return ""

def safe_name(s):
    return re.sub(r"[^A-Za-z0-9_.:-]", "_", str(s))[:120]

def atomic_write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default

def load_cards(workspace=None):
    """All readable cards in cardDir, newest activity first."""
    d = card_dir(workspace)
    cards = []
    if d.is_dir():
        for f in d.glob("*.json"):
            c = read_json(f)
            if isinstance(c, dict) and c.get("session_id"):
                cards.append(c)
    cards.sort(key=lambda c: str(c.get("last_active_at") or ""), reverse=True)
    return cards

def find_card(ref, cards=None):
    """Resolve a full id, short id or unique prefix to a card, or None."""
    ref = (ref or "").strip()
    if not ref:
        return None
    cards = load_cards() if cards is None else cards
    for c in cards:
        if c["session_id"] == ref:
            return c
    hits = [c for c in cards if c["session_id"].startswith(ref) or c.get("short_id") == ref]
    return hits[0] if len(hits) == 1 else None

def card_is_live(card):
    return bool(card) and card.get("status") in ("active", "waiting_on_user")
