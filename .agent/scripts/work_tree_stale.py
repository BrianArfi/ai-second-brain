#!/usr/bin/env python3
"""work_tree_stale.py - propose work-tree nodes to archive, with the reason. No model.

A node is proposed when all of these hold for the node AND everything under it:
  * no live record (open commitment, open or breached waiting-on, open decision)
  * no open todo.md line tagged [node:<id>]
  * no Jira/Linear ticket mapped to it updated in the last --days (from the
    cached dump _temp/jira_all_issues.json that work_tree_link.py refreshes)
  * no record created, closed or decided under it in the last --days
  * it is not a domain or world, and not a `plan` node (planned work is not stale)

It only proposes. Archiving is `work_tree.py archive-node <id> --why "..."`, and
the weekly ledger audit puts this list in front of the owner. Built 26 Sep 2026, after
33 nodes turned out to be finished work nobody had retired.

    work_tree_stale.py                 # markdown list
    work_tree_stale.py --json out.json
    work_tree_stale.py --days 45
"""
import argparse
import collections
import datetime as dt
import json
import os
import re
import sys

BASE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
STATE = os.path.join(BASE, "journal", "state")
DAY = 86400.0

def load(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--json")
    args = ap.parse_args()
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    cutoff = now - args.days * DAY

    tree = load(os.path.join(STATE, "work_tree.json"), {"roots": []})
    nodes, kids, parent = {}, collections.defaultdict(list), {}

    def rec(n, p):
        nodes[n["id"]] = n
        parent[n["id"]] = p
        for c in n.get("children") or []:
            kids[n["id"]].append(c["id"])
            rec(c, n["id"])
    for r in tree.get("roots", []):
        rec(r, None)

    live = collections.Counter()
    last = collections.defaultdict(float)
    for fn, live_states in (("commitments.json", ("open",)), ("waiting_on.json", ("open", "breached")),
                            ("decisions.json", ("open",))):
        for r in load(os.path.join(STATE, fn), {}).get("items", {}).values():
            n = r.get("node")
            if r.get("status") in live_states:
                live[n] += 1
            for k in ("first_seen", "created_at", "closed_at", "decided_at"):
                v = r.get(k)
                if isinstance(v, (int, float)) and v > last[n]:
                    last[n] = v

    todo = collections.Counter()
    try:
        for line in open(os.path.join(BASE, "journal", "todo.md"), encoding="utf-8"):
            if re.match(r"\s*[-*]\s*\[ \]", line):
                for t in re.findall(r"\[node:([a-z0-9\-_]+)\]", line):
                    todo[t] += 1
    except OSError:
        pass

    tix = collections.Counter()
    links = load(os.path.join(STATE, "work_tree_links.json"), {})
    epic_node, default_node = links.get("epic_node", {}), links.get("project_default_node", {})
    for pk, blob in load(os.path.join(BASE, "_temp", "jira_all_issues.json"), {}).items():
        for iss in blob.get("issues", []):
            nid = epic_node.get(iss["key"]) or epic_node.get(iss.get("parent") or "") or default_node.get(pk)
            upd = (iss.get("updated") or "")[:10]
            try:
                if upd and dt.datetime.fromisoformat(upd).replace(tzinfo=dt.timezone.utc).timestamp() >= cutoff:
                    tix[nid] += 1
            except ValueError:
                pass

    def subtree(n):
        out = [n]
        for k in kids[n]:
            out += subtree(k)
        return out

    def archived(n):
        return nodes[n].get("status") == "archived"

    proposals = []
    for nid, n in nodes.items():
        if archived(nid) or n.get("kind") in ("domain", "world") or n.get("status") == "plan":
            continue
        if parent[nid] and archived(parent[nid]):
            continue
        sub = [s for s in subtree(nid) if not archived(s)]
        if any(live[s] or todo[s] or tix[s] for s in sub):
            continue
        newest = max((last[s] for s in sub), default=0)
        if newest >= cutoff:
            continue
        idle = ("no record ever filed under it" if not newest
                else f"last record activity {int((now - newest) / DAY)} days ago")
        proposals.append({"node": nid, "label": n.get("label"), "kind": n.get("kind"),
                          "children": len(sub) - 1,
                          "why": f"no live record, open todo or recent ticket; {idle}"})

    # propose the highest node only: a parent covers its idle children
    ids = {p["node"] for p in proposals}
    proposals = [p for p in proposals if not any(a in ids for a in _ancestors(p["node"], parent))]
    proposals.sort(key=lambda p: p["node"])

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(proposals, fh, ensure_ascii=False, indent=1)
    if not proposals:
        print(f"No node idle for {args.days} days. Nothing to archive.")
        return 0
    print(f"{len(proposals)} node(s) idle for {args.days}+ days. Archive with "
          "`work_tree.py archive-node <id> --why \"...\"` once the owner agrees:\n")
    for p in proposals:
        extra = f", plus {p['children']} child node(s)" if p["children"] else ""
        print(f"- `{p['node']}` {p['label']} ({p['kind']}{extra}): {p['why']}")
    return 0

def _ancestors(nid, parent):
    out, p = [], parent.get(nid)
    while p:
        out.append(p)
        p = parent.get(p)
    return out

if __name__ == "__main__":
    sys.exit(main())
