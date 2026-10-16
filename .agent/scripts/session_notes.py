#!/usr/bin/env python3
"""Handoff notes between sessions: leave a note for a session or a work-tree node.

A note lands at <cardDir>/../notes/<target>/<created>-<from_short>.json, where
target is a session id or "node-<node id>". The target session, or the next session
on that node, reads it at start (notes_inbox.py hook) and marks it delivered.

  session_notes.py post --to <session_ref | node:<id>> --text TEXT [--from ID]
  session_notes.py inbox [--session ID] [--node ID ...] [--all]
  session_notes.py mark-delivered <path> [<path> ...]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import session_coord_common as C  # noqa: E402
from ledger_lock import ledger_lock  # noqa: E402

def resolve_target(to, cards=None):
    """Folder name for `to`: node:<id> becomes node-<id>, else a full session id."""
    to = (to or "").strip()
    if to.startswith("node:"):
        return "node-" + C.safe_name(to[5:])
    card = C.find_card(to, cards)
    return C.safe_name(card["session_id"]) if card else C.safe_name(to)

def post(to, text, from_session, cards=None, now=None):
    target = resolve_target(to, cards)
    to_field = "node:" + target[5:] if target.startswith("node-") else target
    note = {"version": 1, "from": from_session or "unknown", "to": to_field,
            "text": text, "created_at": C.iso(now), "delivered_at": None}
    short = C.safe_name((from_session or "unknown")[:8])
    base = "%s-%s" % (C.compact(now), short)
    path = C.notes_dir() / target / (base + ".json")
    n = 1
    while path.exists():
        n += 1
        path = path.with_name("%s-%d.json" % (base, n))
    C.atomic_write_json(path, note)
    return path

def inbox(session="", nodes=(), include_delivered=False):
    """Notes for `session` and for each node, oldest first, as (path, note)."""
    base = C.notes_dir()
    targets = []
    if session:
        targets.append(C.safe_name(session))
    targets += ["node-" + C.safe_name(n) for n in nodes]
    out = []
    for t in targets:
        d = base / t
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.json")):
            n = C.read_json(f)
            if isinstance(n, dict) and (include_delivered or not n.get("delivered_at")):
                out.append((f, n))
    out.sort(key=lambda pn: str(pn[1].get("created_at") or ""))
    return out

def mark_delivered(paths, now=None):
    done = 0
    with ledger_lock("session_notes"):
        for p in paths:
            n = C.read_json(p)
            if isinstance(n, dict) and not n.get("delivered_at"):
                n["delivered_at"] = C.iso(now)
                C.atomic_write_json(p, n)
                done += 1
    return done

def format_note(note, cards=None):
    card = C.find_card(note.get("from"), cards)
    who = (card or {}).get("spoken_name") or str(note.get("from"))[:8]
    age = C.human_age(C.age_seconds(note.get("created_at")))
    return "from %s (%s): %s" % (who, age, note.get("text", "").strip())

def main(argv=None):
    p = argparse.ArgumentParser(description="Cross-session handoff notes")
    sub = p.add_subparsers(dest="cmd", required=True)
    po = sub.add_parser("post")
    po.add_argument("--to", required=True)
    po.add_argument("--text", required=True)
    po.add_argument("--from", dest="sender", default=C.current_session_id())
    ib = sub.add_parser("inbox")
    ib.add_argument("--session", default=C.current_session_id())
    ib.add_argument("--node", action="append", default=[])
    ib.add_argument("--all", action="store_true", help="include delivered notes")
    md = sub.add_parser("mark-delivered")
    md.add_argument("paths", nargs="+")
    a = p.parse_args(argv)

    cards = C.load_cards()
    if a.cmd == "post":
        path = post(a.to, a.text, a.sender, cards)
        print("note posted: %s" % path.as_posix())
        return 0
    if a.cmd == "inbox":
        nodes = list(a.node)
        card = C.find_card(a.session, cards)
        if card:
            nodes += [n for n in card.get("nodes") or [] if n not in nodes]
        items = inbox(card["session_id"] if card else a.session, nodes, a.all)
        for path, n in items:
            print("%s\n  %s" % (path.as_posix(), format_note(n, cards)))
        if not items:
            print("no undelivered notes")
        return 0
    n = mark_delivered([Path(x) for x in a.paths])
    print("marked %d delivered" % n)
    return 0

if __name__ == "__main__":
    sys.exit(main())
