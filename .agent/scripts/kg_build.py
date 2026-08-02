#!/usr/bin/env python3
"""kg_build.py - build the PSB knowledge graph from typed data we already have.

Deterministic by construction: every edge comes from a named JSON field or a
literal markdown link. No LLM, no network, no embeddings. A full rebuild is
sub-second, so this can run on every ledger write without a budget question.

Design notes and the evaluation that led here: docs/graph_layer_proposal.md
Retrieval design is adapted from Graphify-Labs/graphify (Apache-2.0) as prior
art; no code was copied, and the semantic/LLM half is deliberately not carried
over because it is not reproducible.

Provenance rules (deliberately stricter than graphify's):
  EXTRACTED - the relation is an explicit typed field or a literal link.
  INFERRED  - derived by us. Only ONE edge type qualifies: person mentioned in
              a MOM participant list, matched against the explicit alias table.
  AMBIGUOUS - the default when confidence is not set. Never assume EXTRACTED.

Hard boundaries:
  - Never writes to any ledger. Read-only over journal/state/*.json.
  - Never merges two person ids by string similarity. Splits are REPORTED for
    the owner to resolve, never auto-joined (see feedback_no_guessing_names).
  - The output is a disposable build artifact, not a source of truth.

Usage:
  python3 .agent/scripts/kg_build.py                 # build + print summary
  python3 .agent/scripts/kg_build.py --audit         # also write the audit report
  python3 .agent/scripts/kg_build.py --out FILE      # override output path
"""

import argparse
import json
import os
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
STATE = os.path.join(REPO, "journal", "state")
DEFAULT_OUT = os.path.join(STATE, "graph.json")
DEFAULT_AUDIT = os.path.join(STATE, "kg_audit.md")

SKIP_DIRS = {
    ".git", "node_modules", "_asb-app", "meetbot", "scratch", "_backups",
    "antigravity-extension", ".venv", "venv", "__pycache__", "dashboard-data",
}

EXTRACTED, INFERRED, AMBIGUOUS = "EXTRACTED", "INFERRED", "AMBIGUOUS"

# Placeholder values that must never become a node. Left in, "unknown" becomes
# the single most connected thing in the graph and poisons every traversal.
PLACEHOLDERS = {"", "unknown", "none", "null", "general", "tbd", "n/a", "na",
                "other", "misc", "-"}

def is_placeholder(val):
    return str(val or "").strip().lower() in PLACEHOLDERS

MD_LINK = re.compile(r"\[[^\]]*\]\((?!https?:|mailto:|#)([^)\s]+?\.md)(?:#[^)]*)?\)")
FATHOM_ID = re.compile(r"fathom\.video/calls/(\d+)")
MOM_ROW = re.compile(r"^\|\s*([A-Za-z][A-Za-z /]*?)\s*\|\s*(.+?)\s*\|\s*$", re.M)
MOM_PARTICIPANTS_LINE = re.compile(r"^\*\*Participants\*\*\s*:\s*(.+)$", re.M)
MOM_TITLE = re.compile(r"^#\s+MOM:\s*(.+)$", re.M)
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.M)
BOLD_LEAD = re.compile(r"^\*\*(.{3,80}?)\*\*", re.M)
CODE_FENCE = re.compile(r"^```.*?^```", re.M | re.S)

# A document indexed by filename alone is unfindable by topic. Headings and
# bold lead-ins are the document's own table of contents: deterministic to
# extract, cheap, and they carry the vocabulary someone would actually search.
DOC_TEXT_CAP = 3000

def doc_search_text(txt):
    body = CODE_FENCE.sub("", txt)
    parts = HEADING.findall(body)[:120] + BOLD_LEAD.findall(body)[:80]
    seen, out = set(), []
    for p in parts:
        p = re.sub(r"[`*\[\]]", "", p).strip()
        key = p.lower()
        if p and key not in seen:
            seen.add(key)
            out.append(p)
    return " | ".join(out)[:DOC_TEXT_CAP]

# --------------------------------------------------------------------------- io

def load_json(name):
    path = os.path.join(STATE, name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)

def items_of(blob, key="items"):
    """Ledgers are {meta..., items: [...]} or {meta..., <key>: {...}}."""
    if blob is None:
        return []
    node = blob.get(key, blob) if isinstance(blob, dict) else blob
    if isinstance(node, dict):
        return list(node.values())
    return node if isinstance(node, list) else []

def rel(path):
    return os.path.relpath(path, REPO).replace(os.sep, "/")

# ------------------------------------------------------------------------ graph

class Graph:
    def __init__(self):
        self.nodes = {}
        self.edges = []
        self._seen_edges = set()

    def node(self, nid, ntype, label, **attrs):
        if not nid:
            return None
        if nid in self.nodes:
            existing = self.nodes[nid]
            # first writer wins on label; later passes only fill blanks
            for k, v in attrs.items():
                if v not in (None, "", []) and not existing.get(k):
                    existing[k] = v
            return nid
        rec = {"id": nid, "type": ntype, "label": label or nid}
        rec.update({k: v for k, v in attrs.items() if v not in (None, "", [])})
        self.nodes[nid] = rec
        return nid

    def edge(self, src, tgt, relation, confidence=AMBIGUOUS, evidence=None):
        if not src or not tgt or src == tgt:
            return
        key = (src, tgt, relation)
        if key in self._seen_edges:
            return
        self._seen_edges.add(key)
        rec = {"source": src, "target": tgt, "relation": relation,
               "confidence": confidence}
        if evidence:
            rec["evidence"] = evidence
        self.edges.append(rec)

    def prune_dangling(self):
        """Drop edges pointing at nodes that were never created."""
        before = len(self.edges)
        self.edges = [e for e in self.edges
                      if e["source"] in self.nodes and e["target"] in self.nodes]
        return before - len(self.edges)

# ------------------------------------------------------------------- identities

def norm_name(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()

def slugify_name(s):
    return re.sub(r"[^a-z0-9]+", "-", norm_name(s)).strip("-")

class People:
    """Registry of known humans. Resolution is exact-match only, by design."""

    def __init__(self, blob):
        self.people = (blob or {}).get("people", {}) or {}
        self.by_slug = dict(self.people)
        self.by_name = {}
        for slug, rec in self.people.items():
            for cand in [rec.get("name"), *(rec.get("aliases") or [])]:
                if cand:
                    self.by_name.setdefault(norm_name(cand), slug)
            self.by_name.setdefault(norm_name(slug.replace("-", " ")), slug)
        self.unregistered = {}

    def resolve_slug(self, slug):
        """A ledger slug -> a person node id. Unknown slugs become their own
        node, flagged unregistered. Never guessed into a known person."""
        if not slug:
            return None
        if slug in self.by_slug:
            return "person:" + slug
        self.unregistered[slug] = self.unregistered.get(slug, 0) + 1
        return "person:" + slug

    def resolve_name(self, name):
        """A display name -> a person node id, ONLY on exact alias/name match."""
        key = norm_name(name)
        if not key:
            return None
        slug = self.by_name.get(key)
        return "person:" + slug if slug else None

    def is_registered(self, slug):
        return slug in self.by_slug

# -------------------------------------------------------------------- builders

def add_people(g, people):
    for slug, rec in people.people.items():
        g.node("person:" + slug, "person", rec.get("name") or slug,
               role=rec.get("role"), team=rec.get("team"),
               slack_id=rec.get("slack_id"), registered=True,
               page=rec.get("page"))

def ensure_person(g, people, pid, fallback_slug):
    if pid and pid not in g.nodes:
        g.node(pid, "person", fallback_slug.replace("-", " ").title(),
               registered=people.is_registered(fallback_slug))
    return pid

def meeting_id(url_or_id):
    if not url_or_id:
        return None
    m = FATHOM_ID.search(str(url_or_id))
    if m:
        return "meeting:" + m.group(1)
    if str(url_or_id).isdigit():
        return "meeting:" + str(url_or_id)
    return None

def add_commitments(g, people, blob):
    for c in items_of(blob):
        cid = c.get("id")
        if not cid:
            continue
        nid = g.node("commitment:" + cid, "commitment",
                     f"{cid} {(c.get('text') or '')[:90]}".strip(),
                     status=c.get("status"), due=c.get("due"),
                     project=c.get("project"), portfolio=c.get("portfolio"),
                     search_text=c.get("text"), permalink=c.get("permalink"))
        slug = c.get("to_slug")
        if slug:
            pid = ensure_person(g, people, people.resolve_slug(slug), slug)
            g.edge(nid, pid, "owed_to", EXTRACTED, "commitments.json:to_slug")
        for field, relation in (("portfolio", "belongs_to"), ("project", "belongs_to")):
            val = c.get(field)
            if not is_placeholder(val):
                iid = g.node("initiative:" + slugify_name(val), "initiative", str(val))
                g.edge(nid, iid, relation, EXTRACTED, f"commitments.json:{field}")
        mid = meeting_id((c.get("source") or {}).get("ref") or c.get("permalink"))
        if mid:
            g.node(mid, "meeting", mid.split(":", 1)[1])
            g.edge(nid, mid, "sourced_from", EXTRACTED, "commitments.json:source.ref")

def add_waiting(g, people, blob):
    for w in items_of(blob):
        wid = w.get("id")
        if not wid:
            continue
        nid = g.node("waiting:" + wid, "waiting",
                     f"{wid} {(w.get('what') or '')[:90]}".strip(),
                     status=w.get("status") or "open", sla_hours=w.get("sla_hours"),
                     search_text=w.get("what"))
        slug = w.get("owner_slug")
        if slug:
            pid = ensure_person(g, people, people.resolve_slug(slug), slug)
            g.edge(nid, pid, "blocked_on", EXTRACTED, "waiting_on.json:owner_slug")
        esc = w.get("escalate_to")
        if esc:
            pid = people.resolve_name(esc)
            if pid:
                g.edge(nid, pid, "escalates_to", EXTRACTED, "waiting_on.json:escalate_to")
            else:
                # a name we cannot resolve is recorded, not guessed
                fid = g.node("person:" + slugify_name(esc), "person", esc,
                             registered=False)
                g.edge(nid, fid, "escalates_to", AMBIGUOUS,
                       "waiting_on.json:escalate_to (unresolved name)")

def add_decisions(g, people, blob):
    for d in items_of(blob):
        did = d.get("id")
        if not did:
            continue
        nid = g.node("decision:" + did, "decision",
                     f"{did} {(d.get('title') or '')[:90]}".strip(),
                     status=d.get("status"), project=d.get("project"),
                     deadline=d.get("deadline"),
                     search_text=" ".join(filter(None, [d.get("title"), d.get("decision")])))
        slug = d.get("decider_slug")
        if slug:
            pid = ensure_person(g, people, people.resolve_slug(slug), slug)
            g.edge(nid, pid, "decided_by", EXTRACTED, "decisions.json:decider_slug")
        for s in (d.get("stakeholder_slugs") or []):
            pid = ensure_person(g, people, people.resolve_slug(s), s)
            g.edge(nid, pid, "involves", EXTRACTED, "decisions.json:stakeholder_slugs")
        proj = d.get("project")
        if not is_placeholder(proj):
            iid = g.node("initiative:" + slugify_name(proj), "initiative", str(proj))
            g.edge(nid, iid, "belongs_to", EXTRACTED, "decisions.json:project")
        for src in (d.get("sources") or []):
            mid = meeting_id(src.get("url") if isinstance(src, dict) else src)
            if mid:
                g.node(mid, "meeting", mid.split(":", 1)[1])
                g.edge(nid, mid, "sourced_from", EXTRACTED, "decisions.json:sources")

def add_portfolio(g, blob):
    teams = (blob or {}).get("teams") or {}
    if isinstance(teams, list):
        teams = {t.get("name", str(i)): t for i, t in enumerate(teams)}
    for tname, tval in teams.items():
        tid = g.node("team:" + slugify_name(tname), "team", str(tname))
        inits = tval.get("initiatives") if isinstance(tval, dict) else None
        if isinstance(inits, dict):
            inits = list(inits.values())
        for init in (inits or []):
            iname = init.get("name") if isinstance(init, dict) else str(init)
            if not iname:
                continue
            iid = g.node("initiative:" + slugify_name(iname), "initiative", iname,
                         status=(init.get("status") if isinstance(init, dict) else None))
            g.edge(iid, tid, "owned_by_team", EXTRACTED, "portfolio.json:teams")

def add_tickets(g, people, blob):
    for t in items_of(blob, "tickets"):
        key = t.get("key") or t.get("id")
        if not key:
            continue
        title = t.get("title") or t.get("summary") or ""
        nid = g.node("ticket:" + str(key), "ticket", f"{key} {title[:80]}".strip(),
                     status=t.get("status"), due=t.get("due"),
                     priority=t.get("priority"), search_text=title)
        owner = t.get("owner")
        if owner:
            pid = people.resolve_name(owner)
            if pid:
                g.edge(nid, pid, "owned_by", EXTRACTED, "tickets.json:owner")
            else:
                fid = g.node("person:" + slugify_name(owner), "person", owner,
                             registered=False)
                g.edge(nid, fid, "owned_by", AMBIGUOUS,
                       "tickets.json:owner (name not in people.json)")
        for field in ("project", "initiative_id"):
            val = t.get(field)
            if not is_placeholder(val):
                iid = g.node("initiative:" + slugify_name(val), "initiative", str(val))
                g.edge(nid, iid, "belongs_to", EXTRACTED, f"tickets.json:{field}")

def add_doc_areas(g):
    """Connect documents to the folder they are filed under.

    A path is a literal fact, so these edges are EXTRACTED. Without them 795
    documents sit at degree 0: findable by search but connected to nothing, so
    traversal from them returns their own node and stops. Area nodes get large
    degrees on purpose; hub avoidance keeps queries from routing through them.
    """
    for nid, n in list(g.nodes.items()):
        if n.get("type") != "document":
            continue
        d = os.path.dirname(n.get("source_file") or "")
        if not d:
            continue
        aid = g.node("area:" + d, "area", d.split("/")[-1], path=d)
        g.edge(nid, aid, "filed_under", EXTRACTED, "repo path")
        parent = os.path.dirname(d)
        if parent:
            pid = g.node("area:" + parent, "area", parent.split("/")[-1], path=parent)
            g.edge(aid, pid, "inside", EXTRACTED, "repo path")

# ------------------------------------------------------------------- documents

def walk_markdown():
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for f in files:
            if f.endswith(".md"):
                yield os.path.join(root, f)

def resolve_link(from_path, raw):
    raw = raw.replace("%20", " ")
    if raw.startswith("file://"):
        cands = [raw[7:]]
    else:
        cands = [os.path.normpath(os.path.join(os.path.dirname(from_path), raw)),
                 os.path.normpath(os.path.join(REPO, raw.lstrip("./")))]
    for c in cands:
        if os.path.exists(c):
            return rel(c)
    return None

def add_documents(g, people):
    dead_links = []
    for path in walk_markdown():
        rp = rel(path)
        try:
            txt = open(path, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        title = os.path.splitext(os.path.basename(path))[0].replace("_", " ")
        did = g.node("doc:" + rp, "document", title, source_file=rp,
                     search_text=doc_search_text(txt))

        for m in MD_LINK.finditer(txt):
            target = resolve_link(path, m.group(1))
            if target:
                g.edge(did, "doc:" + target, "references", EXTRACTED,
                       f"{rp}: markdown link")
            else:
                dead_links.append((rp, m.group(1)))

        if os.path.basename(path).startswith("MOM_"):
            add_meeting_from_mom(g, people, rp, txt, did)
    return dead_links

def add_meeting_from_mom(g, people, rp, txt, did):
    rows = {k.strip().lower(): v.strip() for k, v in MOM_ROW.findall(txt)}
    title_m = MOM_TITLE.search(txt)
    label = title_m.group(1).strip() if title_m else os.path.basename(rp)

    rec_ids = FATHOM_ID.findall(txt)
    mid = "meeting:" + rec_ids[0] if rec_ids else "meeting:file:" + rp
    g.node(mid, "meeting", label, date=rows.get("date"), time=rows.get("time"),
           source_file=rp, recording=rec_ids[0] if rec_ids else None,
           search_text=rows.get("subject"))
    g.edge(mid, did, "minuted_in", EXTRACTED, f"{rp}: MOM file")

    fac = rows.get("facilitator")
    if fac:
        pid = people.resolve_name(fac.split("(")[0])
        if pid:
            g.edge(mid, pid, "facilitated_by", EXTRACTED, f"{rp}: Facilitator row")

    raw_parts = rows.get("participants")
    if not raw_parts:
        m = MOM_PARTICIPANTS_LINE.search(txt)
        raw_parts = m.group(1) if m else None
    if raw_parts:
        for chunk in re.split(r",| and ", re.sub(r"\*\*", "", raw_parts)):
            name = chunk.split("(")[0].strip(" .*")
            if len(name) < 3:
                continue
            pid = people.resolve_name(name)
            if pid:
                # alias-table match only. An unmatched name is left out, never
                # coerced into the nearest known person.
                g.edge(mid, pid, "attended_by", INFERRED,
                       f"{rp}: participant name matched people.json alias")

# ----------------------------------------------------------------------- audit

def audit(g, people, dead_links):
    used = Counter()
    for e in g.edges:
        for endpoint in (e["source"], e["target"]):
            if endpoint.startswith("person:"):
                used[endpoint[len("person:"):]] += 1

    unregistered = {s: n for s, n in used.items() if not people.is_registered(s)}

    # Split-identity candidates. REPORTED ONLY. Two slugs are flagged when one
    # is a strict prefix of the other on a token boundary, which is the shape a
    # composite or truncated slug actually takes. Never auto-merged.
    slugs = sorted(used)
    splits = []
    for a in slugs:
        for b in slugs:
            if a != b and b.startswith(a + "-"):
                splits.append((a, used[a], b, used[b]))

    composites = [(s, n) for s, n in used.items()
                  if any(s != k and s.startswith(k + "-") for k in people.by_slug)]

    # Meetings cited by a commitment or decision but never minuted.
    minuted = {e["source"] for e in g.edges if e["relation"] == "minuted_in"}
    cited = {e["target"] for e in g.edges if e["relation"] == "sourced_from"}
    unminuted = sorted(cited - minuted)

    open_no_mom = 0
    for e in g.edges:
        if e["relation"] == "sourced_from" and e["target"] in set(unminuted):
            src = g.nodes.get(e["source"], {})
            if src.get("type") == "commitment" and src.get("status") == "open":
                open_no_mom += 1

    # Initiative aliases. commitments.json:portfolio uses lowercase slugs while
    # :project uses display names, so the same initiative lands twice ('b2c' and
    # 'B2C SuperApp'). Same rule as people: reported, never auto-merged.
    init_deg = Counter()
    for e in g.edges:
        for endpoint in (e["source"], e["target"]):
            if endpoint.startswith("initiative:"):
                init_deg[endpoint] += 1
    inits = sorted(init_deg)
    init_splits = []
    for a in inits:
        sa = a[len("initiative:"):]
        for b in inits:
            if a != b and b[len("initiative:"):].startswith(sa + "-"):
                init_splits.append((sa, init_deg[a],
                                    b[len("initiative:"):], init_deg[b]))

    return {
        "initiative_splits": init_splits,
        "unregistered": sorted(unregistered.items(), key=lambda x: -x[1]),
        "splits": splits,
        "composites": sorted(composites, key=lambda x: -x[1]),
        "unminuted": unminuted,
        "open_commitments_without_mom": open_no_mom,
        "dead_links": dead_links,
    }

def write_audit(rep, g, path):
    L = []
    L.append("# Knowledge Graph Audit\n")
    L.append("> Generated by `.agent/scripts/kg_build.py`. Read-only findings.")
    L.append("> Nothing here is auto-corrected. Every merge is the owner's call.\n")

    L.append("## 1. Person ids used by ledgers but not registered in people.json\n")
    L.append(f"{len(rep['unregistered'])} ids, "
             f"{sum(n for _, n in rep['unregistered'])} edge references.\n")
    L.append("| uses | slug |")
    L.append("|---:|:---|")
    for slug, n in rep["unregistered"][:60]:
        L.append(f"| {n} | `{slug}` |")

    L.append("\n## 2. Split-identity candidates (one slug is a prefix of another)\n")
    L.append("Same human filed under more than one id, or two humans mashed into one.")
    L.append("Confirm each by hand, then add the loser as an alias in `people.json`.\n")
    L.append("| slug A | uses | slug B | uses |")
    L.append("|:---|---:|:---|---:|")
    for a, na, b, nb in rep["splits"]:
        L.append(f"| `{a}` | {na} | `{b}` | {nb} |")

    L.append("\n## 3. Composite ids containing a registered person plus more\n")
    L.append("These are almost certainly two owners collapsed into one id.\n")
    L.append("| uses | slug |")
    L.append("|---:|:---|")
    for slug, n in rep["composites"]:
        L.append(f"| {n} | `{slug}` |")

    L.append("\n## 4. Meetings cited as evidence but never minuted\n")
    L.append(f"{len(rep['unminuted'])} recordings are cited by a commitment or "
             f"decision with no MOM file anywhere in the repo.")
    L.append(f"**{rep['open_commitments_without_mom']} OPEN commitments** rest on "
             "one of these as their only evidence.\n")
    for mid in rep["unminuted"][:60]:
        rid = mid.split(":", 1)[1]
        L.append(f"- `{rid}` https://fathom.video/calls/{rid}")

    L.append("\n## 5. Initiative alias candidates\n")
    L.append("`portfolio` stores lowercase slugs, `project` stores display names, "
             "so one initiative can land as two nodes.\n")
    L.append("| slug A | edges | slug B | edges |")
    L.append("|:---|---:|:---|---:|")
    for a, na, b, nb in rep["initiative_splits"]:
        L.append(f"| `{a}` | {na} | `{b}` | {nb} |")

    L.append("\n## 6. Dead document links\n")
    L.append(f"{len(rep['dead_links'])} markdown links resolve to nothing, "
             "neither directory-relative nor repo-root-relative.\n")
    L.append("| source | target |")
    L.append("|:---|:---|")
    for src, tgt in rep["dead_links"][:80]:
        L.append(f"| {src} | `{tgt}` |")

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")

# ------------------------------------------------------------------------ main

def build():
    g = Graph()
    people = People(load_json("people.json"))
    add_people(g, people)
    add_commitments(g, people, load_json("commitments.json"))
    add_waiting(g, people, load_json("waiting_on.json"))
    add_decisions(g, people, load_json("decisions.json"))
    add_portfolio(g, load_json("portfolio.json"))
    add_tickets(g, people, load_json("tickets.json"))
    dead = add_documents(g, people)
    add_doc_areas(g)
    pruned = g.prune_dangling()
    return g, people, dead, pruned

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--audit", action="store_true", help="also write the audit report")
    ap.add_argument("--audit-out", default=DEFAULT_AUDIT)
    args = ap.parse_args()

    t0 = time.time()
    g, people, dead, pruned = build()
    rep = audit(g, people, dead)
    elapsed = time.time() - t0

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "generator": ".agent/scripts/kg_build.py",
        "note": "Build artifact. Discovery index only, never a source of truth. "
                "Ledgers in journal/state/ remain the SSOT.",
        "build_seconds": round(elapsed, 3),
        "nodes": list(g.nodes.values()),
        "edges": g.edges,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)

    by_type = Counter(n["type"] for n in g.nodes.values())
    by_conf = Counter(e["confidence"] for e in g.edges)
    by_rel = Counter(e["relation"] for e in g.edges)

    print(f"built in {elapsed:.2f}s -> {rel(args.out)}")
    print(f"  nodes {len(g.nodes)}   edges {len(g.edges)}   "
          f"(pruned {pruned} dangling)")
    print("  nodes by type : " + ", ".join(f"{k} {v}" for k, v in by_type.most_common()))
    print("  edges by conf : " + ", ".join(f"{k} {v}" for k, v in by_conf.most_common()))
    print("  top relations : " + ", ".join(f"{k} {v}" for k, v in by_rel.most_common(6)))
    print()
    print("audit:")
    print(f"  unregistered person ids           : {len(rep['unregistered'])}")
    print(f"  split-identity candidates         : {len(rep['splits'])}")
    print(f"  composite ids (2 humans in one)   : {len(rep['composites'])}")
    print(f"  meetings cited but never minuted  : {len(rep['unminuted'])}")
    print(f"  OPEN commitments w/o a MOM        : {rep['open_commitments_without_mom']}")
    print(f"  initiative alias candidates       : {len(rep['initiative_splits'])}")
    print(f"  dead document links               : {len(rep['dead_links'])}")

    if args.audit:
        write_audit(rep, g, args.audit_out)
        print(f"\naudit report -> {rel(args.audit_out)}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
