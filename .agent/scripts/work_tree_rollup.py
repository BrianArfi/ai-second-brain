#!/usr/bin/env python3
"""The reverse link: from a work-tree node to every ledger record filed under it.

The link is stored once, as the `node` field on each COM / WAIT / DEC record,
and only the ledger CLIs write it. This script derives the other direction, so
"what is the latest state of node X" is one lookup instead of a read of three
ledgers. Nothing here is ever written into `work_tree.json`: two writers of the
same link drift apart, and the tree is curated by hand.

Output: `journal/state/work_tree_rollup.index.json` (a `*.index.json`, so a
merge conflict on it takes upstream's copy and the next render rewrites it).
`ledger_sync.py` re-renders it after every ledger mutation.

Per node it holds:
  own     live record ids filed on the node itself, by ledger and state
  sub     the same counts for the node plus everything under it
  health  computed from the records, unless the node carries a manual
          `health_override` (set with `work_tree.py set-health`, needs a reason)
  last_activity_wib   newest timestamp on any record in the subtree
  next_refs           the curated `next_refs` on the node, each with its status
  stale_mentions      ledger ids named in the node's prose that are now closed

`live_records` holds a one-line card for every live record that has a node,
so a reader can list a node's records without opening the three ledgers.

Health, worst first:
  critical  an item that slipped in the last 7 days (commitment past due,
            decision past deadline, waiting-on breached)
  risk      an item overdue for longer, due in the next 3 days, or live for
            more than 30 days (ledger hygiene rule 2: every record leaves
            within 30 days)
  ok        live records, none of the above
  quiet     no live records

    python3 .agent/scripts/work_tree_rollup.py --write
    python3 .agent/scripts/work_tree_rollup.py show nw-afmig
"""
import argparse
import datetime
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(BASE, ".agent", "scripts"))
from work_tree import UNFILED, is_container_root, load_tree, node_index  # noqa: E402

STATE = os.path.join(BASE, "journal", "state")
OUT = os.path.join(STATE, "work_tree_rollup.index.json")
WIB = datetime.timezone(datetime.timedelta(hours=7))

LEDGERS = (
    ("COM", "commitments.json", {"open"}),
    ("WAIT", "waiting_on.json", {"open", "breached"}),
    ("DEC", "decisions.json", {"open"}),
)
# `updated_at` is left out on purpose: a refile or a typo fix bumps it, and
# neither is the work moving.
TIME_FIELDS = ("first_seen", "since", "created_at", "decided_at",
               "closed_at", "breached_at", "last_nudge_at")
PROSE_FIELDS = ("summary", "problem", "progress", "blocker", "next", "moved")
LEDGER_ID = re.compile(r"\b(?:COM|WAIT|DEC)-\d{4}\b")
HEALTH_RANK = {"quiet": 0, "ok": 1, "risk": 2, "critical": 3}
SLIPPED_DAYS = 7
FRESH_DAYS = 30
SOON_DAYS = 3

def now():
    return datetime.datetime.now(WIB)

def wib(ts):
    return datetime.datetime.fromtimestamp(ts, WIB).strftime("%Y-%m-%dT%H:%M+07:00") if ts else None

def day(s):
    try:
        return datetime.date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None

def load_records():
    out = {}
    for prefix, fn, live in LEDGERS:
        path = os.path.join(STATE, fn)
        if not os.path.exists(path):
            continue
        with open(path) as fh:
            items = json.load(fh).get("items", {})
        for rid, r in items.items():
            out[rid] = (prefix, live, r)
    return out

def classify(prefix, live, r, today, now_ts):
    """-> (bucket, flags) where bucket is 'live' or 'closed' and flags is a
    subset of {'overdue', 'slipped', 'soon', 'stale'}. 'slipped' is overdue
    for SLIPPED_DAYS or less, the only thing that turns a node red. Older
    overdue items are still overdue, and past FRESH_DAYS they are stale: a
    backlog nobody touched is a hygiene problem for the Saturday audit, not
    a fresh alarm on every node above it."""
    status = r.get("status")
    if status not in live:
        return "closed", set()
    born = r.get("first_seen") or r.get("since") or r.get("created_at") or now_ts
    late_days = None
    if prefix == "WAIT":
        if status == "breached":
            late_days = (now_ts - (r.get("breached_at") or born)) / 86400
    else:
        due = day(r.get("due") if prefix == "COM" else r.get("deadline"))
        if due and due < today:
            late_days = (today - due).days
        elif due and (due - today).days <= SOON_DAYS:
            return "live", {"soon"}
    if late_days is not None:
        flags = {"overdue"}
        if late_days <= SLIPPED_DAYS:
            flags.add("slipped")
        if late_days > FRESH_DAYS:
            flags.add("stale")
        return "live", flags
    return "live", ({"stale"} if (now_ts - born) / 86400 > FRESH_DAYS else set())

def health_of(c):
    if c["slipped"]:
        return "critical"
    if c["overdue"] or c["soon"] or c["stale"]:
        return "risk"
    if c["live"]:
        return "ok"
    return "quiet"

def blank():
    return {"live": 0, "overdue": 0, "slipped": 0, "soon": 0, "stale": 0,
            "closed_14d": 0, "COM": 0, "WAIT": 0, "DEC": 0}

def override_active(ov, today):
    if not ov or not ov.get("status") or not ov.get("why"):
        return False
    until = day(ov.get("until"))
    return until is None or until >= today

def build():
    tree = load_tree()
    idx = node_index(tree)
    records = load_records()
    t_now = now()
    today, now_ts = t_now.date(), t_now.timestamp()

    own = {nid: {"COM": [], "WAIT": [], "DEC": [], "overdue": [], "slipped": [],
                 "soon": [], "stale": [], "counts": blank(), "last": 0} for nid in idx}
    status_of = {}
    unfiled_live, dangling = [], []
    live_records = {}
    for rid, (prefix, live, r) in records.items():
        status_of[rid] = r.get("status")
        bucket, flags = classify(prefix, live, r, today, now_ts)
        nid = r.get("node") or UNFILED
        if nid == UNFILED:
            if bucket == "live":
                unfiled_live.append(rid)
            continue
        if nid not in idx:
            dangling.append(rid)
            continue
        o = own[nid]
        last = max((r.get(k) for k in TIME_FIELDS
                    if isinstance(r.get(k), (int, float))), default=0)
        o["last"] = max(o["last"], last)
        if bucket == "closed":
            if r.get("closed_at") or r.get("decided_at"):
                closed = r.get("closed_at") or r.get("decided_at")
                if now_ts - closed <= 14 * 86400:
                    o["counts"]["closed_14d"] += 1
            continue
        live_records[rid] = {
            "node": nid, "status": r.get("status"),
            "text": " ".join(str(r.get("text") or r.get("what") or r.get("title") or "").split())[:140],
            "who": r.get("to") or r.get("owner") or r.get("decider") or None,
            "due": r.get("due") or r.get("deadline") or None,
            "flags": sorted(flags),
        }
        o[prefix].append(rid)
        o["counts"]["live"] += 1
        o["counts"][prefix] += 1
        for flag in flags:
            o[flag].append(rid)
            o["counts"][flag] += 1

    out_nodes = {}

    def rec(node):
        nid = node["id"]
        o = own[nid]
        sub = dict(o["counts"])
        last = o["last"]
        worst = health_of(o["counts"])
        for child in node.get("children") or []:
            # An archived subtree still gets its own entry, so its old
            # records stay findable, but it adds nothing to the live parent.
            cs, cl, ch = rec(child)
            if child.get("status") == "archived":
                continue
            for k in sub:
                sub[k] += cs[k]
            last = max(last, cl)
            if HEALTH_RANK[ch] > HEALTH_RANK[worst]:
                worst = ch
        ov = node.get("health_override")
        active = override_active(ov, today)
        mentioned = sorted(set(LEDGER_ID.findall(" ".join(
            str(node.get(f) or "") for f in PROSE_FIELDS))))
        out_nodes[nid] = {
            "path": idx[nid]["path"],
            "archived": idx[nid]["archived"],
            "own": {k: sorted(o[k]) for k in ("COM", "WAIT", "DEC", "overdue", "slipped", "soon", "stale")},
            "own_counts": o["counts"],
            "sub": sub,
            "health": ov["status"] if active else worst,
            "health_computed": worst,
            "health_override": ov if active else None,
            "last_activity_wib": wib(last),
            "next_refs": [{"id": x, "status": status_of.get(x, "unknown")}
                          for x in node.get("next_refs") or []],
            "stale_mentions": [x for x in mentioned
                               if status_of.get(x) not in (None, "open", "breached", "pending")],
        }
        return sub, last, (ov["status"] if active and ov["status"] in HEALTH_RANK else worst)

    for root in tree.get("roots", []):
        rec(root)

    on_container_root = sorted(
        rid for nid, n in out_nodes.items() if is_container_root(nid, idx)
        for rid in n["own"]["COM"] + n["own"]["WAIT"] + n["own"]["DEC"])
    return {
        "_note": "Generated by .agent/scripts/work_tree_rollup.py from the node field "
                 "on every ledger record. Never edit by hand; never copy into work_tree.json.",
        "generated_wib": t_now.strftime("%Y-%m-%dT%H:%M+07:00"),
        "totals": {"records": len(records), "unfiled_live": len(unfiled_live),
                   "dangling": len(dangling), "live_on_container_root": len(on_container_root)},
        "unfiled_live": sorted(unfiled_live),
        "dangling": sorted(dangling),
        "live_on_container_root": on_container_root,
        "nodes": out_nodes,
        "live_records": live_records,
    }

def write(data):
    tmp = OUT + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, OUT)

def format_node(nid, n):
    s = n["sub"]
    lines = [f"  health  : {n['health']}"
             + (f"  (manual: {n['health_override']['why']}; computed {n['health_computed']})"
                if n["health_override"] else ""),
             f"  ledger  : {s['live']} live (COM {s['COM']}, WAIT {s['WAIT']}, DEC {s['DEC']})"
             f"  slipped {s['slipped']}  overdue {s['overdue']}  due soon {s['soon']}  stale {s['stale']}"
             f"  closed 14d {s['closed_14d']}   [this node and below]",
             f"  activity: {n['last_activity_wib'] or '-'}"]
    for k in ("slipped", "soon", "COM", "WAIT", "DEC"):
        if n["own"][k]:
            lines.append(f"  {k.lower():<8}: {', '.join(n['own'][k])}")
    if n["next_refs"]:
        lines.append("  next    : " + ", ".join(f"{r['id']} ({r['status']})" for r in n["next_refs"]))
    if n["stale_mentions"]:
        lines.append("  prose names closed records: " + ", ".join(n["stale_mentions"]))
    return "\n".join(lines)

def main():
    p = argparse.ArgumentParser(description="Node -> ledger records rollup")
    p.add_argument("--write", action="store_true", help="write the rollup file")
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("show")
    s.add_argument("node_id")
    args = p.parse_args()

    data = build()
    if args.write:
        write(data)
    if args.cmd == "show":
        n = data["nodes"].get(args.node_id)
        if not n:
            print(f"unknown node '{args.node_id}'", file=sys.stderr)
            return 1
        print(f"{args.node_id}  {n['path']}")
        print(format_node(args.node_id, n))
        return 0
    t = data["totals"]
    print(f"rollup: {t['records']} records, {len(data['nodes'])} nodes; live unfiled "
          f"{t['unfiled_live']}, dangling {t['dangling']}, live on a top-level area "
          f"{t['live_on_container_root']}" + ("  (written)" if args.write else ""))
    return 0

if __name__ == "__main__":
    sys.exit(main() or 0)
