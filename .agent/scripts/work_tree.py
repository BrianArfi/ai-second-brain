#!/usr/bin/env python3
"""The work tree as a schema: stable node ids every ticket can point at.

`journal/state/work_tree.json` began as a reporting artifact, hand-maintained
per window for the dashboard Work tab. Once every commitment, decision,
waiting-on and chase carries a `node` field naming one of its nodes (CLAUDE.md,
"Every Ticket Belongs To A Work-Tree Node"), those ids stop being labels in a
report and become permanent identifiers. A node renamed during a weekly refresh
would silently orphan every record pointing at it.

So this module owns the invariants that make the ids safe to depend on:

  * ids are unique across the whole tree, and permanent
  * a node is retired by `status: archived`, never by deletion or rename
  * writes take the ledger lock, because the tree is now written by more than
    the person editing it by hand
  * `portfolio.json` keeps its own id space and is joined by an explicit
    mapping table, `journal/state/work_tree_alias.json`, validated here

Importable by the ledger CLIs:

    from work_tree import resolve_or_die, suggest, node_index

Command line:

    python3 .agent/scripts/work_tree.py find apple        # search for a node
    python3 .agent/scripts/work_tree.py show apple-uat    # one node, in full
    python3 .agent/scripts/work_tree.py list --kind client
    python3 .agent/scripts/work_tree.py add-node --id promo-p2 --label "Phase 2" \
        --kind phase --parent promo --summary "..."
    python3 .agent/scripts/work_tree.py archive-node apple-stc --why "shipped"
    python3 .agent/scripts/work_tree.py alias --initiative mp-exampleprogram-eshop --node exampleprogram
    python3 .agent/scripts/work_tree.py validate
"""
import argparse
import datetime
import difflib
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TREE = os.path.join(BASE, "journal", "state", "work_tree.json")
ALIAS = os.path.join(BASE, "journal", "state", "work_tree_alias.json")
PORTFOLIO = os.path.join(BASE, "journal", "state", "portfolio.json")

# The one value that means "this record has no home yet, and that is known".
# Allowed only for unattended runs, and only with a reason. See CLAUDE.md.
UNFILED = "unfiled"

VALID_KINDS = {"domain", "world", "client", "drop", "track", "item", "thread",
               "initiative", "phase", "product", "service", "group",
               "domain-note"}

def wib_now():
    return (datetime.datetime.utcnow() + datetime.timedelta(hours=7)).strftime(
        "%Y-%m-%dT%H:%M+07:00")

# ------------------------------------------------------------------ loading

def load_tree():
    with open(TREE) as fh:
        return json.load(fh)

def save_tree(tree):
    tree["refreshed_wib"] = wib_now()
    tmp = TREE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(tree, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, TREE)

def walk(tree=None):
    """Every node, depth first, as (node, parent_id, path_labels)."""
    tree = tree if tree is not None else load_tree()
    out = []

    def rec(node, parent_id, path):
        here = path + [node.get("label", node["id"])]
        out.append((node, parent_id, here))
        for child in node.get("children", []) or []:
            rec(child, node["id"], here)

    for root in tree.get("roots", []):
        rec(root, None, [])
    return out

def node_index(tree=None):
    """id -> {node, parent, path, archived}. The lookup every caller wants."""
    idx = {}
    for node, parent, path in walk(tree):
        idx[node["id"]] = {
            "node": node,
            "parent": parent,
            "path": " > ".join(path),
            "kind": node.get("kind"),
            "label": node.get("label", node["id"]),
            "archived": node.get("status") == "archived",
        }
    return idx

# ------------------------------------------------------------- resolution

def suggest(query, idx=None, n=5):
    """Closest nodes to a query, matched on id, label and full path."""
    idx = idx if idx is not None else node_index()
    q = (query or "").lower().strip()
    if not q:
        return []
    scored = []
    for nid, meta in idx.items():
        hay = f"{nid} {meta['label']} {meta['path']}".lower()
        if q in hay:
            # Substring hits rank above fuzzy ones, shortest path first so a
            # client beats one of its own threads on an ambiguous query.
            scored.append((0, len(meta["path"]), nid))
        else:
            # Compare against the id and the label separately as well as
            # together: a typo'd id ("aple-uat") scores badly against the
            # concatenation but very well against the id on its own, and a
            # typo is the most common way this function gets called.
            ratio = max(
                difflib.SequenceMatcher(None, q, cand).ratio()
                for cand in (nid.lower(), meta["label"].lower(),
                             f"{nid} {meta['label']}".lower())
            )
            if ratio > 0.55:
                scored.append((1 - ratio, len(meta["path"]), nid))
    scored.sort()
    return [nid for _, _, nid in scored[:n]]

class UnknownNode(Exception):
    pass

def resolve_or_die(node_id, allow_unfiled=False, idx=None):
    """Return the node id, or exit with the closest matches printed.

    Ledger CLIs call this before writing. Failing loudly here is the whole
    point: a record filed under a plausible-but-wrong node reports as tracked
    and never surfaces again, which is worse than one that was never filed.
    """
    idx = idx if idx is not None else node_index()
    node_id = (node_id or "").strip()
    if not node_id:
        raise UnknownNode("no --node given. Every record needs a work-tree node.")
    if node_id == UNFILED:
        if allow_unfiled:
            return UNFILED
        raise UnknownNode(
            "--node unfiled is for unattended runs only (cron, sweep, subagent), "
            "and needs --node-why. In a live session, ask the owner instead.")
    if node_id in idx:
        if idx[node_id]["archived"]:
            raise UnknownNode(f"node '{node_id}' is archived; file under a live node.")
        return node_id
    close = suggest(node_id, idx)
    hint = "\n".join(f"    {c}  ({idx[c]['path']})" for c in close) or "    (no close match)"
    raise UnknownNode(f"unknown node '{node_id}'. Closest:\n{hint}\n"
                      f"  Search: python3 .agent/scripts/work_tree.py find <text>")

# ----------------------------------------------------------------- aliases

def load_alias():
    if not os.path.exists(ALIAS):
        return {"_note": "portfolio.json initiative id -> work_tree.json node id. "
                         "Two id spaces drift by default, so this table is validated "
                         "on every work_tree_link.py --check.",
                "schema_version": 1, "map": {}}
    with open(ALIAS) as fh:
        return json.load(fh)

def save_alias(data):
    data["updated_wib"] = wib_now()
    tmp = ALIAS + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, ALIAS)

def portfolio_initiatives():
    if not os.path.exists(PORTFOLIO):
        return {}
    with open(PORTFOLIO) as fh:
        p = json.load(fh)
    out = {}
    for team in p.get("teams", []) or []:
        for ini in team.get("initiatives", []) or []:
            out[ini["id"]] = {"name": ini.get("name", ""), "team": team.get("name", "")}
    return out

# ---------------------------------------------------------------- commands

def cmd_find(args):
    idx = node_index()
    hits = suggest(args.query, idx, n=args.limit)
    if not hits:
        print(f"no node matches '{args.query}'")
        return 1
    for nid in hits:
        m = idx[nid]
        flag = "  [archived]" if m["archived"] else ""
        print(f"{nid:<18} {m['kind'] or '':<11} {m['path']}{flag}")
    return 0

def cmd_show(args):
    idx = node_index()
    if args.node_id not in idx:
        return cmd_find(argparse.Namespace(query=args.node_id, limit=5))
    m = idx[args.node_id]
    n = m["node"]
    print(f"{args.node_id}  ({m['kind']})")
    print(f"  path    : {m['path']}")
    print(f"  parent  : {m['parent']}")
    print(f"  status  : {n.get('status', '-')}")
    if n.get("summary"):
        print(f"  summary : {n['summary']}")
    refs = n.get("refs") or []
    print(f"  refs    : {len(refs)}")
    kids = n.get("children") or []
    if kids:
        print(f"  children: {', '.join(k['id'] for k in kids)}")
    return 0

def cmd_list(args):
    idx = node_index()
    for nid, m in idx.items():
        if args.kind and m["kind"] != args.kind:
            continue
        if m["archived"] and not args.all:
            continue
        print(f"{nid:<18} {m['kind'] or '':<11} {m['path']}")
    return 0

def cmd_add_node(args):
    tree = load_tree()
    idx = node_index(tree)
    if args.id in idx:
        print(f"node '{args.id}' already exists: {idx[args.id]['path']}", file=sys.stderr)
        return 1
    if args.kind not in VALID_KINDS:
        print(f"kind '{args.kind}' not in {sorted(VALID_KINDS)}", file=sys.stderr)
        return 1
    node = {"id": args.id, "label": args.label, "kind": args.kind,
            "status": args.status, "summary": args.summary or "", "children": []}
    if args.parent:
        if args.parent not in idx:
            print(f"unknown parent '{args.parent}'", file=sys.stderr)
            return 1
        idx[args.parent]["node"].setdefault("children", []).append(node)
        where = idx[args.parent]["path"]
    else:
        tree.setdefault("roots", []).append(node)
        where = "(root)"
    save_tree(tree)
    print(f"added node {args.id} under {where}")
    return 0

def cmd_archive_node(args):
    tree = load_tree()
    idx = node_index(tree)
    if args.node_id not in idx:
        print(f"unknown node '{args.node_id}'", file=sys.stderr)
        return 1
    n = idx[args.node_id]["node"]
    n["status"] = "archived"
    n["archived_wib"] = wib_now()
    if args.why:
        n["archived_why"] = args.why
    save_tree(tree)
    print(f"archived {args.node_id} (id kept; records pointing at it still resolve)")
    return 0

def cmd_alias(args):
    data = load_alias()
    inis = portfolio_initiatives()
    if args.list:
        for pid, nid in sorted(data["map"].items()):
            print(f"{pid:<26} -> {nid}   ({inis.get(pid, {}).get('name', '?')})")
        missing = [p for p in inis if p not in data["map"]]
        if missing:
            print(f"\nunmapped initiatives ({len(missing)}): {', '.join(missing)}")
        return 0
    if not (args.initiative and args.node):
        print("need --initiative and --node, or --list", file=sys.stderr)
        return 1
    if args.initiative not in inis:
        print(f"unknown portfolio initiative '{args.initiative}'", file=sys.stderr)
        return 1
    try:
        nid = resolve_or_die(args.node)
    except UnknownNode as e:
        print(str(e), file=sys.stderr)
        return 1
    data["map"][args.initiative] = nid
    save_alias(data)
    print(f"{args.initiative} -> {nid}")
    return 0

def cmd_validate(args):
    tree = load_tree()
    problems = []

    seen = {}
    for node, parent, path in walk(tree):
        nid = node["id"]
        if nid in seen:
            problems.append(f"duplicate node id '{nid}': {seen[nid]} and {' > '.join(path)}")
        seen[nid] = " > ".join(path)
        if node.get("kind") not in VALID_KINDS:
            problems.append(f"node '{nid}' has unknown kind '{node.get('kind')}'")
        if not node.get("label"):
            problems.append(f"node '{nid}' has no label")

    idx = node_index(tree)
    alias = load_alias()
    inis = portfolio_initiatives()
    for pid, nid in alias.get("map", {}).items():
        if pid not in inis:
            problems.append(f"alias '{pid}' is not a portfolio initiative any more")
        if nid not in idx:
            problems.append(f"alias '{pid}' points at unknown node '{nid}'")
        elif idx[nid]["archived"]:
            problems.append(f"alias '{pid}' points at archived node '{nid}'")
    unmapped = [p for p in inis if p not in alias.get("map", {})]

    print(f"nodes: {len(idx)}   archived: {sum(1 for m in idx.values() if m['archived'])}")
    print(f"portfolio initiatives: {len(inis)}   mapped: {len(inis) - len(unmapped)}   unmapped: {len(unmapped)}")
    if unmapped:
        print("  unmapped: " + ", ".join(sorted(unmapped)))
    for p in problems:
        print(f"DRIFT: {p}")
    return 1 if problems else 0

def cmd_coverage(args):
    """How much of the ledgers has a home, and what is still unfiled.

    The number that matters is open-and-unfiled: a closed record with no node
    is history, but an open one is work nobody can find from the tree.
    """
    ledgers = {"commitments": "commitments.json", "decisions": "decisions.json",
               "waiting_on": "waiting_on.json"}
    # "decided" is a CLOSED state for a decision, the same way "done" closes a
    # commitment. Counting it as live inflated open+unfiled by every decided
    # record that never got a node: 23 of 87 on 24 Aug 2026.
    live = {"open", "pending"}
    tot = unfiled = open_unfiled = 0
    rows = []
    for name, fn in ledgers.items():
        path = os.path.join(BASE, "journal", "state", fn)
        if not os.path.exists(path):
            continue
        items = json.load(open(path)).get("items", {})
        u = [r for r in items.values() if (r.get("node") or UNFILED) == UNFILED]
        ou = [r for r in u if r.get("status") in live]
        rows.append((name, len(items), len(u), len(ou)))
        tot += len(items)
        unfiled += len(u)
        open_unfiled += len(ou)
    for name, n, u, ou in rows:
        print(f"{name:<12} {n:>4} records   unfiled {u:>4}   open+unfiled {ou:>4}")
    pct = 100.0 * (tot - unfiled) / max(tot, 1)
    print(f"{'TOTAL':<12} {tot:>4} records   unfiled {unfiled:>4}   "
          f"open+unfiled {open_unfiled:>4}   filed {pct:.0f}%")
    if open_unfiled:
        print(f"\n{open_unfiled} open record(s) have no work-tree node. Triage list: "
              f"journal/state/work_tree_triage.json")
    if args.fail_on_open and open_unfiled > args.fail_on_open:
        return 1
    return 0

def main():
    p = argparse.ArgumentParser(description="Work tree: stable node ids for every ticket")
    sub = p.add_subparsers(dest="cmd")

    f = sub.add_parser("find", help="search nodes by id, label or path")
    f.add_argument("query")
    f.add_argument("--limit", type=int, default=8)

    s = sub.add_parser("show", help="one node in full")
    s.add_argument("node_id")

    l = sub.add_parser("list")
    l.add_argument("--kind")
    l.add_argument("--all", action="store_true", help="include archived")

    a = sub.add_parser("add-node")
    a.add_argument("--id", required=True)
    a.add_argument("--label", required=True)
    a.add_argument("--kind", required=True)
    a.add_argument("--parent")
    a.add_argument("--summary")
    a.add_argument("--status", default="active")

    ar = sub.add_parser("archive-node")
    ar.add_argument("node_id")
    ar.add_argument("--why")

    al = sub.add_parser("alias", help="portfolio initiative id <-> node id")
    al.add_argument("--initiative")
    al.add_argument("--node")
    al.add_argument("--list", action="store_true")

    sub.add_parser("validate")

    cv = sub.add_parser("coverage", help="how many records have a node")
    cv.add_argument("--fail-on-open", type=int, default=None,
                    help="exit 1 when more than N open records are unfiled")

    args = p.parse_args()
    fn = {"find": cmd_find, "show": cmd_show, "list": cmd_list,
          "add-node": cmd_add_node, "archive-node": cmd_archive_node,
          "alias": cmd_alias, "validate": cmd_validate,
          "coverage": cmd_coverage}.get(args.cmd)
    if not fn:
        p.print_help()
        return 2
    return fn(args)

READONLY = {"find", "show", "list", "validate", "coverage", None}

if __name__ == "__main__":
    _cmd = next((a for a in sys.argv[1:] if not a.startswith("-")), None)
    if _cmd in READONLY:
        sys.exit(main() or 0)
    # Writes go through the same lock as the ledgers: the tree is no longer
    # edited only by hand, and a lost node now orphans records.
    sys.path.insert(0, os.path.join(BASE, ".agent", "scripts"))
    from ledger_lock import hold_ledger_lock
    hold_ledger_lock("work_tree")
    sys.exit(main() or 0)
