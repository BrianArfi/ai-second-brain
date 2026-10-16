#!/usr/bin/env python3
"""Resource claims across sessions: who is working on this Doc, ticket, file or branch.

Why: reply_router claims Slack threads only. Two sessions still overwrote the same
Google Doc, filed the same ticket, or edited the same PRD. A claim says "session X
has this", so a second session finds out before it writes.

Resource keys:  gdoc:<docId>  jira:<KEY>  linear:<KEY>  file:<workspace-relative path>
                branch:<repo>:<name>  slack:<conversationKey>

A claim is stale (anyone may take it) when it is older than 12h, or when its
session card says done/abandoned. Writes run under the 'session_claims' ledger lock and claims.json.lock.

  claims.py claim <key> [--session ID] [--note TEXT]   exit 3 if a live session holds it
  claims.py release <key> [--session ID]
  claims.py who <key>
  claims.py list [--all]

Slack keys also consult journal/state/reply_router_state.json, read-only.
"""
import argparse
import contextlib
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import session_coord_common as C  # noqa: E402
import ledger_lock as _LL  # noqa: E402
from ledger_lock import ledger_lock  # noqa: E402

LOCK = "session_claims"
ROUTER_STATE = C.WORKSPACE / "journal" / "state" / "reply_router_state.json"
OPEN_STATUSES = ("open", "claimed", "active")

@contextlib.contextmanager
def _claims_lock(timeout=30.0):
    """Ledger lock plus the shared <claims dir>/claims.json.lock file lock.

    The second lock is the one the session contract names, so a writer outside this
    repo (the desktop app) that locks claims.json.lock excludes this script too.
    """
    with ledger_lock(LOCK):
        path = C.claims_path().with_name("claims.json.lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(path, "a+")
        start = time.time()
        while True:
            try:
                _LL._try_lock(fh)
                break
            except OSError as exc:
                if exc.errno not in _LL._BUSY or time.time() - start > timeout:
                    fh.close()
                    raise
                time.sleep(0.1)
        try:
            yield
        finally:
            try:
                _LL._unlock(fh)
            finally:
                fh.close()

def is_stale(claim, cards=None, now=None):
    age = C.age_seconds(claim.get("claimed_at"), now)
    if age is None or age > C.STALE_SECONDS:
        return True
    card = C.find_card(claim.get("session_id"), cards)
    return bool(card) and card.get("status") in ("done", "abandoned")

def _load():
    data = C.read_json(C.claims_path(), {})
    return data if isinstance(data, dict) else {}

def _router_owner(key):
    """Read-only owner lookup for slack:<conversationKey> in the reply-router state."""
    if not key.startswith("slack:"):
        return None
    conv_key = key[len("slack:"):]
    state = C.read_json(ROUTER_STATE, {}) or {}
    conv = (state.get("conversations") or {}).get(conv_key)
    if conv and conv.get("status") in OPEN_STATUSES and conv.get("session_id"):
        return {"session_id": conv["session_id"], "claimed_at": None,
                "note": "reply_router conversation %s" % conv_key, "source": "reply_router"}
    return None

def who(key, cards=None, now=None):
    """The live holder of `key` as a dict, or None. Stale claims count as unheld."""
    claim_ = _load().get(key)
    if claim_ and not is_stale(claim_, cards, now):
        return dict(claim_, key=key)
    return _router_owner(key)

def claim(key, session, note="", cards=None, now=None):
    """Take `key`. Returns (ok, holder). ok is False when another live session holds it."""
    with _claims_lock():
        data = _load()
        current = data.get(key)
        if current and current.get("session_id") != session and not is_stale(current, cards, now):
            return False, dict(current, key=key)
        router = _router_owner(key)
        if router and router["session_id"] != session:
            return False, dict(router, key=key)
        # Drop other stale entries while the lock is held, so the file stays small.
        for k in [k for k, v in data.items() if k != key and is_stale(v, cards, now)]:
            del data[k]
        data[key] = {"session_id": session, "claimed_at": C.iso(now), "note": note or ""}
        C.atomic_write_json(C.claims_path(), data)
        return True, None

def release(key, session=None):
    with _claims_lock():
        data = _load()
        cur = data.get(key)
        if not cur:
            return False
        if session and cur.get("session_id") != session:
            return False
        del data[key]
        C.atomic_write_json(C.claims_path(), data)
        return True

def describe(holder, cards=None):
    card = C.find_card(holder.get("session_id"), cards)
    name = (card or {}).get("spoken_name") or ""
    short = (card or {}).get("short_id") or str(holder.get("session_id"))[:8]
    age = C.human_age(C.age_seconds(holder.get("claimed_at"))) if holder.get("claimed_at") else "open"
    return "%s %s (%s)" % (short, name, age) if name else "%s (%s)" % (short, age)

def main(argv=None):
    p = argparse.ArgumentParser(description="Cross-session resource claims")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("claim", "release", "who"):
        sp = sub.add_parser(name)
        sp.add_argument("key")
        if name != "who":
            sp.add_argument("--session", default=C.current_session_id())
        if name == "claim":
            sp.add_argument("--note", default="")
    ls = sub.add_parser("list")
    ls.add_argument("--all", action="store_true", help="include stale claims")
    a = p.parse_args(argv)

    cards = C.load_cards()
    if a.cmd == "claim":
        if not a.session:
            print("claim needs --session (or ASB_SESSION_ID)", file=sys.stderr)
            return 2
        ok, holder = claim(a.key, a.session, a.note, cards)
        if ok:
            print("claimed %s for %s" % (a.key, a.session[:8]))
            return 0
        print("owned by session %s: post it a note (session_notes.py post --to %s --text ...)"
              % (describe(holder, cards), str(holder.get("session_id"))[:8]))
        return 3
    if a.cmd == "release":
        ok = release(a.key, a.session or None)
        print("released %s" % a.key if ok else "nothing to release for %s" % a.key)
        return 0 if ok else 1
    if a.cmd == "who":
        h = who(a.key, cards)
        if not h:
            print("unclaimed")
            return 1
        print("owned by session %s%s" % (describe(h, cards), " - " + h["note"] if h.get("note") else ""))
        return 0
    data = _load()
    shown = 0
    for k, v in sorted(data.items()):
        stale = is_stale(v, cards)
        if stale and not a.all:
            continue
        print("%s  %s  %s%s" % (k, describe(v, cards), v.get("note") or "", "  [stale]" if stale else ""))
        shown += 1
    if not shown:
        print("no live claims")
    return 0

if __name__ == "__main__":
    sys.exit(main())
