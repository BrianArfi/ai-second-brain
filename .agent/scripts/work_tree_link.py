"""Link every ticket, roadmap row, PRD, design and artifact into the work tree.

The work tree is the reading surface: opening a node should answer "what is this,
what tickets are live under it, which PRD defines it, where is the design, what
did we last say about it" without leaving the page. That only holds if the links
are regenerated from the real systems, so this script rebuilds them:

    journal/state/work_tree_links.json   curated join table (node <- epics, MPL, docs)
  + _temp/jira_all_issues.json           live Jira dump (dump_all_issues.py)
  + Master Product List, Roadmap Breakdown tab
  + journal/state/*.index.json           ledger records already referenced
    -> journal/state/work_tree.json      refs + sources, merged in place
    -> journal/work_tree_index.md        the full readable index, every ticket listed

Only ever adds. Curated prose (summary, problem, progress, blocker, moved) is
never touched, and existing refs/sources survive a re-run.

    python3 .agent/scripts/work_tree_link.py            # refresh Jira, then link
    python3 .agent/scripts/work_tree_link.py --no-fetch # reuse the last dump
    python3 .agent/scripts/work_tree_link.py --check    # report coverage, write nothing
"""
import argparse
import csv
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.parse

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TREE = os.path.join(BASE, "journal", "state", "work_tree.json")
LINKS = os.path.join(BASE, "journal", "state", "work_tree_links.json")
JIRA_DUMP = os.path.join(BASE, "_temp", "jira_all_issues.json")
LINEAR_DUMP = os.path.join(BASE, "_temp", "linear_all_issues.json")
MPL_CSV = os.path.join(BASE, "_temp", "mpl_roadmap_breakdown.csv")
MPL_CELLS = os.path.join(BASE, "_temp", "mpl_rb_cells.json")
INDEX_MD = os.path.join(BASE, "journal", "work_tree_index.md")

MPL_SHEET_ID = "<YOUR_DRIVE_ID>"
MPL_TAB = "Roadmap Breakdown"
FETCH_SHEETS = os.path.join(BASE, ".agent", "skills", "work-drive-connector", "fetch_sheets.py")
DUMP_ISSUES = os.path.join(BASE, ".agent", "skills", "jira-connector", "scripts", "dump_all_issues.py")
DUMP_LINEAR = os.path.join(BASE, ".agent", "skills", "linear-connector", "scripts", "dump_all_issues.py")
LINEAR_TEAMS = os.path.join(BASE, ".agent", "skills", "linear-connector", "teams.json")

JIRA_DOMAIN = {"MP": "yourcompany.atlassian.net", "MPS": "yourcompany.atlassian.net",
               "MSP": "examplevendor.atlassian.net", "MBA": "examplevendor.atlassian.net",
               "STOR": "examplevendor.atlassian.net"}
LEDGER_RE = re.compile(r"^(WAIT|COM|DEC|CR)-\d+$")
JIRA_RE = re.compile(r"^(MP|MPS|MSP|MBA|STOR)-\d+$")

def linear_config():
    """Team keys discovered by `linear_client.py teams --write`, or None.

    Read from disk rather than hardcoded because Linear derives issue
    identifiers from team keys, and guessing them into a regex is how a ticket
    ends up filed under a plausible-but-wrong system.
    """
    if not os.path.exists(LINEAR_TEAMS):
        return None
    with open(LINEAR_TEAMS) as fh:
        cfg = json.load(fh)
    return cfg if cfg.get("teams") else None

_LINEAR_CFG = linear_config()
LINEAR_KEYS = sorted((_LINEAR_CFG or {}).get("teams", {}))
LINEAR_WORKSPACE = (_LINEAR_CFG or {}).get("workspace", "yourcompany")
# A team key that collides with a Jira project key would be claimed by JIRA_RE
# and linked to Atlassian, so it is excluded here and reported by the connector.
LINEAR_RE = (re.compile(r"^(" + "|".join(k for k in LINEAR_KEYS if k not in JIRA_DOMAIN) + r")-\d+$")
             if [k for k in LINEAR_KEYS if k not in JIRA_DOMAIN] else None)
# Marker on every generated source chip, so a re-run replaces its own output
# instead of stacking a second copy next to it.
GEN = "​"  # zero-width space, invisible in the dashboard

def wib_now():
    return (datetime.datetime.utcnow() + datetime.timedelta(hours=7)).strftime("%Y-%m-%dT%H:%M+07:00")

def run(cmd, **kw):
    return subprocess.run(cmd, cwd=BASE, capture_output=True, text=True, **kw)

# ---------------------------------------------------------------- inputs

def refresh_jira():
    r = run([sys.executable, DUMP_ISSUES, "--out", JIRA_DUMP])
    if r.returncode != 0:
        raise SystemExit(f"jira dump failed:\n{r.stderr[-2000:]}")

def refresh_linear():
    """Never fatal. Through the 25 Aug cutover both trackers run in parallel,
    and a Linear outage or a missing API key must not take the Jira half of the
    work tree down with it."""
    if not _LINEAR_CFG:
        print("[linear] teams.json not written yet, skipping", file=sys.stderr)
        return
    r = run([sys.executable, DUMP_LINEAR, "--out", LINEAR_DUMP])
    if r.returncode != 0:
        print(f"[linear] dump failed, continuing with Jira only:\n{r.stderr[-1000:]}", file=sys.stderr)

def load_linear():
    """The Linear dump, keyed like the Jira one so the two merge into one dict."""
    if not os.path.exists(LINEAR_DUMP):
        return {}
    with open(LINEAR_DUMP) as fh:
        return json.load(fh)

def refresh_mpl():
    r = run([sys.executable, FETCH_SHEETS, "get", MPL_SHEET_ID, MPL_TAB])
    if r.returncode != 0:
        raise SystemExit(f"MPL fetch failed:\n{r.stderr[-2000:]}")
    os.makedirs(os.path.dirname(MPL_CSV), exist_ok=True)
    with open(MPL_CSV, "w") as fh:
        fh.write(r.stdout)

def load_mpl():
    """Roadmap Breakdown rows collapsed to Component|Feature, with doc URLs."""
    doc_links = {}
    if os.path.exists(MPL_CELLS):
        cells = json.load(open(MPL_CELLS))
        comp = feat = None
        for row in cells[1:]:
            row = (row + [{"v": "", "links": []}] * 6)[:6]
            if row[0]["v"].strip():
                comp = row[0]["v"].strip()
            if row[1]["v"].strip():
                feat = row[1]["v"].strip()
            for cell in (row[4], row[5]):
                for l in cell.get("links", []):
                    url = l.get("url")
                    if url:
                        doc_links.setdefault((comp, feat), {})[url] = (l.get("text") or cell["v"]).strip()

    feats = {}
    if not os.path.exists(MPL_CSV):
        return feats
    rows = list(csv.reader(open(MPL_CSV)))
    comp = feat = None
    for r in rows[1:]:
        r = [x.strip() for x in (r + [""] * 6)[:6]]
        if r[0]:
            comp = r[0]
        if r[1]:
            feat = r[1]
        if not comp:
            continue
        e = feats.setdefault((comp, feat), {"items": [], "phases": set(), "status": set(), "docs": {}, "doc_names": set()})
        if r[2]:
            e["items"].append(r[2])
        if r[3]:
            e["phases"].add(r[3])
        if r[4]:
            e["status"].add(r[4])
        if r[5]:
            e["doc_names"].add(r[5])
    for k, v in feats.items():
        v["docs"] = doc_links.get(k, {})
    return feats

def load_ledger_index():
    out = {}
    for name in ("commitments", "waiting_on", "decisions"):
        p = os.path.join(BASE, "journal", "state", f"{name}.index.json")
        if not os.path.exists(p):
            continue
        try:
            data = json.load(open(p))
        except Exception:
            continue
        recs = data.get("records") or data.get("items") or (data if isinstance(data, list) else [])
        for rec in recs if isinstance(recs, list) else []:
            rid = rec.get("id")
            if rid:
                out[rid] = rec
    return out

# ---------------------------------------------------------------- helpers

def walk(nodes, parent=None):
    for n in nodes:
        yield n, parent
        yield from walk(n.get("children", []), n)

def jql_url(project, keys=None, epics=None):
    if project in LINEAR_KEYS:
        # Linear has no JQL. A team's board is the closest stable equivalent,
        # and a single ticket is addressable on its own.
        if keys and len(keys) == 1:
            return f"https://linear.app/{LINEAR_WORKSPACE}/issue/{keys[0]}"
        return f"https://linear.app/{LINEAR_WORKSPACE}/team/{project}/all"
    if epics:
        jql = f'project = {project} AND parent in ({", ".join(sorted(epics))}) ORDER BY status, updated DESC'
    else:
        jql = f'key in ({", ".join(sorted(keys))}) ORDER BY status, updated DESC'
    return f"https://{JIRA_DOMAIN[project]}/issues/?jql=" + urllib.parse.quote(jql, safe="")

def add_source(node, label, url, kind):
    """Add a generated chip, replacing the previous generation's version of it."""
    srcs = node.setdefault("sources", [])
    label = GEN + label
    for s in srcs:
        if s.get("url") == url or s.get("label") == label:
            s.update({"label": label, "url": url, "kind": kind})
            return
    srcs.append({"label": label, "url": url, "kind": kind})

def add_refs(node, new):
    refs = node.setdefault("refs", [])
    have = set(refs)
    def is_linear(r):
        return bool(LINEAR_RE and LINEAR_RE.match(r))

    ledger = [r for r in refs if LEDGER_RE.match(r)]
    jira = [r for r in refs if JIRA_RE.match(r)]
    linear = [r for r in refs if is_linear(r)]
    other = [r for r in refs if r not in ledger and r not in jira and r not in linear]
    for r in new:
        if r not in have:
            (linear if is_linear(r) else jira).append(r)
            have.add(r)
    by_key = lambda k: (k.split("-")[0], int(k.split("-")[1]))
    node["refs"] = (other + ledger
                    + sorted(set(jira), key=by_key)
                    + sorted(set(linear), key=by_key))

# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true", help="reuse the cached Jira/MPL pulls")
    ap.add_argument("--check", action="store_true", help="report coverage, write nothing")
    args = ap.parse_args()

    if not args.no_fetch:
        refresh_jira()
        refresh_linear()
        refresh_mpl()

    tree = json.load(open(TREE))
    cfg = json.load(open(LINKS))
    jira = json.load(open(JIRA_DUMP))
    # Both trackers run in parallel until the 25 Aug cutover, so a node shows
    # its Jira and its Linear tickets side by side. After cutover the Jira half
    # simply goes quiet; nothing here needs re-plumbing.
    linear = load_linear()
    for team_key, blob in linear.items():
        if team_key in jira:
            print(f"[linear] team key {team_key} collides with a Jira project key, skipped. "
                  "Source-qualify it before linking.", file=sys.stderr)
            continue
        jira[team_key] = blob
    mpl = load_mpl()
    ledger = load_ledger_index()

    nodes = {n["id"]: n for n, _ in walk(tree["roots"])}
    parents = {n["id"]: (p["id"] if p else None) for n, p in walk(tree["roots"])}

    # A few nodes still carry a bare URL string from before sources were objects.
    # The dashboard reads s.label/s.url, so those render as an empty chip.
    for node in nodes.values():
        for i, s in enumerate(node.get("sources") or []):
            if isinstance(s, str):
                kind = "fathom" if "fathom.video" in s else "slack" if "slack.com" in s else "doc"
                label = "Meeting recording" if kind == "fathom" else s.rsplit("/", 1)[-1] or s
                node["sources"][i] = {"label": label, "url": s, "kind": kind}

    epic_node = cfg["epic_node"]
    default_node = cfg["project_default_node"]

    # ---- assign every open ticket to a node ----------------------------
    by_node = {}
    unmapped_epics = {}
    fallback = {}
    for pk, blob in jira.items():
        for iss in blob["issues"]:
            key, parent = iss["key"], iss.get("parent")
            nid = epic_node.get(key) or (epic_node.get(parent) if parent else None)
            if not nid:
                nid = default_node.get(pk)
                bucket = parent or f"(no epic) {pk}"
                fallback.setdefault(nid, {}).setdefault(bucket, 0)
                fallback[nid][bucket] += 1
                if parent and parent not in epic_node:
                    unmapped_epics.setdefault(parent, {"project": pk, "count": 0,
                                                       "summary": iss.get("parent_summary")})
                    unmapped_epics[parent]["count"] += 1
            if nid not in nodes:
                unmapped_epics.setdefault(f"!node:{nid}", {"project": pk, "count": 0, "summary": "node id not in tree"})
                unmapped_epics[f"!node:{nid}"]["count"] += 1
                continue
            by_node.setdefault(nid, []).append(iss)

    # ---- assign every MPL feature to a node -----------------------------
    mpl_map = cfg["mpl_node"]
    mpl_by_node = {}
    unmapped_mpl = []
    for (comp, feat), v in mpl.items():
        nid = mpl_map.get(f"{comp}|{feat}") or mpl_map.get(f"{comp}|*")
        if not nid or nid not in nodes:
            unmapped_mpl.append(f"{comp}|{feat}")
            continue
        mpl_by_node.setdefault(nid, []).append((comp, feat, v))

    if args.check:
        print(f"nodes in tree        : {len(nodes)}")
        print(f"tickets assigned     : {sum(len(v) for v in by_node.values())} across {len(by_node)} nodes")
        per_source = {}
        for issues in by_node.values():
            for i in issues:
                per_source[i.get("source", "jira")] = per_source.get(i.get("source", "jira"), 0) + 1
        print(f"  by source          : {per_source or '{}'}")
        if not _LINEAR_CFG:
            print("  linear             : not connected (run linear_client.py teams --write)")
        elif not os.path.exists(LINEAR_DUMP):
            print("  linear             : connected, no dump yet")
        print(f"MPL features assigned: {sum(len(v) for v in mpl_by_node.values())} across {len(mpl_by_node)} nodes")
        print(f"\nepics with no mapping ({len(unmapped_epics)}), fell back to the project default:")
        for k, v in sorted(unmapped_epics.items(), key=lambda x: -x[1]["count"]):
            print(f"  {k:<12} {v['count']:>3} tickets  {(v['summary'] or '')[:60]}")
        print(f"\nMPL features with no mapping ({len(unmapped_mpl)}):")
        for k in unmapped_mpl:
            print(f"  {k}")
        return

    # ---- merge into the tree --------------------------------------------
    stamp = wib_now()
    node_links = cfg["node_links"]
    for nid, node in nodes.items():
        touched = False

        issues = by_node.get(nid, [])
        if issues:
            epics = sorted({i["key"] for i in issues if i["type"] == "Epic"})
            add_refs(node, epics)
            per_project = {}
            for i in issues:
                per_project.setdefault(i["key"].split("-")[0], []).append(i)
            for pk, group in sorted(per_project.items()):
                blocked = sum(1 for i in group if (i["status"] or "").lower() in
                              ("blocked", "on hold", "impediment"))
                review = sum(1 for i in group if (i["status"] or "").lower() in
                             ("under review", "in review", "code review"))
                bits = [f"{len(group)} open"]
                if review:
                    bits.append(f"{review} in review")
                if blocked:
                    bits.append(f"{blocked} blocked")
                add_source(node, f"{pk}: {', '.join(bits)}",
                           jql_url(pk, keys=[i["key"] for i in group]), "jira")
            touched = True

        for comp, feat, v in mpl_by_node.get(nid, []):
            for url, name in v["docs"].items():
                add_source(node, f"PRD: {name}"[:110], url, "doc")
            touched = True

        extra = node_links.get(nid, {})
        for kind, chips in (("doc", extra.get("docs", [])), ("doc", extra.get("figma", [])),
                            ("doc", extra.get("artifacts", [])), ("slack", extra.get("slack", []))):
            for c in chips:
                add_source(node, c["label"], c["url"], kind)
                touched = True

        # last status: newest ledger note or newest ticket update under this node
        stamps = [i["updated"][:10] for i in issues if i.get("updated")]
        for r in node.get("refs", []):
            rec = ledger.get(r)
            if rec and rec.get("updated_at"):
                stamps.append(str(rec["updated_at"])[:10])
        if stamps:
            node["updated_wib"] = max(stamps)
            touched = True

        if touched:
            node["linked_wib"] = stamp

    tree["refreshed_wib"] = stamp
    tree.setdefault("linkage", {})
    tree["linkage"] = {
        "generated_wib": stamp,
        "tickets_linked": sum(len(v) for v in by_node.values()),
        "mpl_features_linked": sum(len(v) for v in mpl_by_node.values()),
        "unmapped_epics": len(unmapped_epics),
        "unmapped_mpl_features": unmapped_mpl,
        "source": "journal/state/work_tree_links.json via .agent/scripts/work_tree_link.py",
    }
    # tmp + os.replace, like every other writer of journal/state. A plain
    # open(TREE, "w") truncates the file first, so a crash partway through the
    # dump left work_tree.json unparseable -- and the read this write is based
    # on happened at line 247, before two network fetches (Jira, Google Sheets),
    # so the window is minutes wide rather than milliseconds.
    _tmp = TREE + ".tmp"
    with open(_tmp, "w") as fh:
        json.dump(tree, fh, indent=1, ensure_ascii=False)
    os.replace(_tmp, TREE)

    write_index(tree, nodes, parents, by_node, mpl_by_node, node_links, unmapped_epics, unmapped_mpl, stamp)
    print(f"linked {tree['linkage']['tickets_linked']} tickets and "
          f"{tree['linkage']['mpl_features_linked']} MPL features into {TREE}")
    print(f"wrote {INDEX_MD}")

def write_index(tree, nodes, parents, by_node, mpl_by_node, node_links,
                unmapped_epics, unmapped_mpl, stamp):
    def path(nid):
        out, cur = [], nid
        while cur:
            out.append(nodes[cur]["label"])
            cur = parents.get(cur)
        return " › ".join(reversed(out))

    L = ["# Work Tree Index",
         "",
         "> Generated by `.agent/scripts/work_tree_link.py`. Do not hand-edit: the next run overwrites it.",
         f"> Mapping lives in [`journal/state/work_tree_links.json`](state/work_tree_links.json). Refreshed {stamp}.",
         "",
         f"Every open ticket across MP, MPS, MSP, MBA and STOR, every feature on the Master Product List "
         f"*Roadmap Breakdown* tab, and every PRD, design file and published artifact, filed under the work-tree "
         f"node that owns it. {tree['linkage']['tickets_linked']} tickets, "
         f"{tree['linkage']['mpl_features_linked']} roadmap features.",
         ""]

    for nid in [n["id"] for n, _ in walk(tree["roots"])]:
        node = nodes[nid]
        issues = by_node.get(nid, [])
        feats = mpl_by_node.get(nid, [])
        extra = node_links.get(nid, {})
        if not (issues or feats or extra):
            continue
        L += [f"## {path(nid)}", "",
              f"`{nid}` · {node.get('kind','')} · status **{node.get('status','?')}** "
              f"· [open in dashboard](http://localhost:3737/#work/{nid})", ""]
        if node.get("summary"):
            L += [node["summary"], ""]

        for label, chips in (("PRDs and docs", extra.get("docs", [])),
                             ("Design", extra.get("figma", [])),
                             ("Artifacts", extra.get("artifacts", [])),
                             ("Channels", extra.get("slack", []))):
            if chips:
                L.append(f"**{label}**: " + " · ".join(f"[{c['label']}]({c['url']})" for c in chips))
                L.append("")

        if feats:
            L += ["**Master Product List (Roadmap Breakdown)**", "",
                  "| Feature | Phase | PRD status | Items | Document |",
                  "| :-- | :-- | :-- | --: | :-- |"]
            for comp, feat, v in sorted(feats):
                docs = " · ".join(f"[{n}]({u})" for u, n in v["docs"].items()) or \
                       " · ".join(sorted(v["doc_names"])) or "—"
                L.append(f"| {comp} — {feat} | {'; '.join(sorted(v['phases'])) or '—'} | "
                         f"{'; '.join(sorted(v['status'])) or '—'} | {len(v['items'])} | {docs} |")
            L.append("")

        if issues:
            epics = [i for i in issues if i["type"] == "Epic"]
            rest = [i for i in issues if i["type"] != "Epic"]
            by_epic = {}
            for i in rest:
                by_epic.setdefault(i.get("parent") or "— no epic", []).append(i)
            L += [f"**Tickets** ({len(issues)} open)", ""]
            # An epic can be closed while its children are still open, so fall
            # back to the parent summary carried on the child ticket.
            epic_label = {i["parent"]: i["parent_summary"] for i in rest
                          if i.get("parent") and i.get("parent_summary")}
            epic_label.update({e["key"]: e["summary"] for e in epics})
            for ekey in sorted(by_epic, key=lambda k: (k == "— no epic", k)):
                group = by_epic[ekey]
                head = epic_label.get(ekey)
                title = f"{ekey} — {head}" if head else ekey
                L += [f"<details><summary>{title} ({len(group)})</summary>", "",
                      "| Key | Type | Status | Assignee | Summary |",
                      "| :-- | :-- | :-- | :-- | :-- |"]
                for i in sorted(group, key=lambda x: (x["status"] or "", x["key"])):
                    L.append(f"| [{i['key']}]({i['url']}) | {i['type']} | {i['status']} | "
                             f"{i['assignee'] or '—'} | {(i['summary'] or '').replace('|', '/')[:90]} |")
                L += ["", "</details>", ""]

    if unmapped_epics or unmapped_mpl:
        L += ["## Not yet mapped", "",
              "These fell through to a project default. Add them to `work_tree_links.json` to file them properly.", ""]
        if unmapped_epics:
            L += ["| Epic | Project | Tickets | Summary |", "| :-- | :-- | --: | :-- |"]
            for k, v in sorted(unmapped_epics.items(), key=lambda x: -x[1]["count"]):
                L.append(f"| {k} | {v['project']} | {v['count']} | {(v['summary'] or '')[:70]} |")
            L.append("")
        for k in unmapped_mpl:
            L.append(f"- MPL feature not mapped: `{k}`")
        L.append("")

    with open(INDEX_MD, "w") as fh:
        fh.write("\n".join(L) + "\n")

if __name__ == "__main__":
    # Rewrites work_tree.json wholesale, and the read it is based on happens
    # before refresh_jira() and refresh_mpl() go to the network -- so without the
    # lock, anything writing the tree during those fetches gets overwritten by a
    # snapshot taken minutes earlier. work_tree.py already takes this same lock.
    sys.path.insert(0, os.path.join(BASE, ".agent", "scripts"))
    from ledger_lock import hold_ledger_lock
    hold_ledger_lock("work_tree")
    main()
