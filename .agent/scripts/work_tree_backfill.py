#!/usr/bin/env python3
"""Give every existing ledger record a work-tree node.

The rule (CLAUDE.md, "Every Ticket Belongs To A Work-Tree Node") is enforced at
the CLI for new records. This script handles the ones already in the ledgers,
which on 14 Aug 2026 was ~720 records with 61 linked.

It is deliberately two-speed. Confident mappings are written; everything else is
written to a triage file for the owner, with the top candidate nodes for each record,
because a record filed under a plausible-but-wrong node reports as tracked and
never surfaces again. That is the failure this whole rule exists to prevent, so
the script would rather leave a record unfiled than guess it into the wrong home.

Signals, strongest first:

  ref     the record id is already listed in a node's `refs`      (curated, trusted)
  jira    a Jira key in the record text sits under exactly one node
  alias   the record's portfolio initiative_id maps via work_tree_alias.json
  label   a distinctive node label appears in the record text

    python3 .agent/scripts/work_tree_backfill.py                 # dry run + coverage
    python3 .agent/scripts/work_tree_backfill.py --apply         # write confident ones
    python3 .agent/scripts/work_tree_backfill.py --triage-out journal/state/work_tree_triage.json
"""
import argparse
import collections
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(BASE, ".agent", "scripts"))
from work_tree import UNFILED, load_alias, node_index, suggest, walk  # noqa: E402

STATE = os.path.join(BASE, "journal", "state")
TRIAGE = os.path.join(STATE, "work_tree_triage.json")

LEDGERS = {
    "commitments": {"file": "commitments.json", "text": ("text", "project")},
    "decisions": {"file": "decisions.json", "text": ("title", "decision", "notes", "project")},
    "waiting_on": {"file": "waiting_on.json", "text": ("what", "owner")},
}

JIRA_RE = re.compile(r"\b(MP|MPS|MSP|MBA|STOR)-\d+\b")

# Labels too generic to file on. "Drop 2" appears in half the Example Program records
# and identifies a node only in combination with something else.
STOPWORDS = {"drop 1", "drop 2", "drop 3", "old world", "new world", "phase 1",
             "phase 2", "uat and demo", "other", "misc", "general", "platform",
             "marketplace", "b2c super app", "e-commerce solution"}

AUTO_THRESHOLD = 0.75

def blob(rec, fields):
    parts = []
    for f in fields:
        v = rec.get(f)
        if isinstance(v, str):
            parts.append(v)
    src = rec.get("source") or {}
    if isinstance(src, dict):
        parts.append(str(src.get("ref") or src.get("permalink") or ""))
    for s in rec.get("sources") or []:
        if isinstance(s, dict):
            parts.append(f"{s.get('label', '')} {s.get('url', '')}")
    return " ".join(parts).lower()

def source_key(rec):
    """The document or thread a record came from, if it names one."""
    src = rec.get("source") or {}
    if isinstance(src, dict):
        k = src.get("ref") or src.get("permalink") or ""
        if k:
            return str(k)
    for s in rec.get("sources") or []:
        if isinstance(s, dict) and s.get("url"):
            return str(s["url"])
    return None

def build_ref_maps(idx):
    """record id -> node, and jira key -> node, from the curated refs."""
    by_record, by_jira = {}, {}
    for node, _parent, _path in walk():
        nid = node["id"]
        for r in node.get("refs", []) or []:
            key = r if isinstance(r, str) else (r.get("key") or r.get("id") or "")
            key = str(key).strip()
            if not key:
                continue
            if JIRA_RE.fullmatch(key):
                # A Jira key under two nodes identifies neither.
                by_jira[key] = None if key in by_jira and by_jira[key] != nid else nid
            else:
                by_record[key] = nid
    return by_record, {k: v for k, v in by_jira.items() if v}

def label_terms(idx):
    """node id -> the distinctive lowercase terms that identify it."""
    out = {}
    for nid, meta in idx.items():
        terms = set()
        for t in (meta["label"], nid.replace("-", " ")):
            t = t.lower().strip()
            if len(t) >= 5 and t not in STOPWORDS:
                terms.add(t)
        out[nid] = terms
    return out

def ancestors(nid, idx):
    """nid and every node above it, root last."""
    chain, seen = [], set()
    while nid and nid in idx and nid not in seen:
        seen.add(nid)
        chain.append(nid)
        nid = idx[nid]["parent"]
    return chain

def common_ancestor(nids, idx):
    chains = [ancestors(n, idx) for n in nids if n in idx]
    if not chains:
        return None
    shared = set(chains[0])
    for c in chains[1:]:
        shared &= set(c)
    if not shared:
        return None
    # The deepest node they all share: the most specific true statement.
    return min(shared, key=lambda n: -len(idx[n]["path"]))

def score_record(text, idx, terms, initiative_id, alias, by_record, by_jira, rid):
    """Return (node, confidence, why) for the best guess, plus runners-up."""
    if rid in by_record:
        return by_record[rid], 0.99, "ref", []

    hits = []
    for m in JIRA_RE.finditer(text.upper()):
        nid = by_jira.get(m.group(0))
        if nid:
            hits.append((0.90, nid, f"jira:{m.group(0)}"))

    if initiative_id and initiative_id in alias.get("map", {}):
        hits.append((0.80, alias["map"][initiative_id], f"alias:{initiative_id}"))

    # Curated phrase table: the project strings and client names people actually
    # write ("ExampleCo", "B2C SuperApp", "Aseel") rather than the node's own label.
    # These outrank generic label matches, because a record tagged project
    # "B2C SuperApp" that happens to mention storefront belongs to B2C, and
    # letting the incidental mention tie the curated tag sends it to triage for
    # no reason.
    for phrase, nid in (alias.get("terms") or {}).items():
        if nid not in idx:
            continue
        # Short terms match on word boundaries only, so "oms" does not fire on
        # "customs" and "pim" does not fire on "shipment".
        hit = (re.search(rf"\b{re.escape(phrase)}\b", text) if len(phrase) <= 4
               else phrase in text)
        if hit:
            # Grade by phrase length so a record matching both "oms" and
            # "b2c superapp" files under the more specific of the two instead
            # of tying and falling to triage.
            hits.append((min(0.89, 0.80 + 0.006 * len(phrase)), nid, f"term:{phrase}"))

    for nid, ts in terms.items():
        for t in ts:
            if t in text:
                # Longer, deeper labels are more specific, so they win ties.
                depth = idx[nid]["path"].count(">")
                conf = min(0.88, 0.45 + 0.03 * len(t) + 0.02 * depth)
                hits.append((conf, nid, f"label:{t}"))

    if not hits:
        return None, 0.0, "", []

    hits.sort(reverse=True)
    best_conf, best_nid, why = hits[0]
    # Two different nodes matched at effectively the same strength. Coin-secondaryping
    # between them is the one outcome worth avoiding, but sending it to triage is
    # not the only alternative: if the rivals sit under a common ancestor, that
    # ancestor is a true statement about the record. Coarse and right beats
    # precise and wrong, and beats unfiled. Only a fall-back all the way to a
    # root domain is too vague to be worth filing.
    rivals = [h for h in hits if h[1] != best_nid and h[0] >= best_conf - 0.03]
    if rivals and best_conf < 0.90:
        anc = common_ancestor([best_nid] + [h[1] for h in rivals], idx)
        if anc and idx[anc]["kind"] not in ("domain", "world"):
            best_nid, best_conf, why = anc, 0.78, f"{why}+ancestor"
        else:
            best_conf = min(best_conf, 0.60)
    runners = []
    for _c, nid, w in hits:
        if nid != best_nid and nid not in runners:
            runners.append(nid)
    return best_nid, best_conf, why, runners[:2]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write confident mappings")
    ap.add_argument("--threshold", type=float, default=AUTO_THRESHOLD)
    ap.add_argument("--triage-out", default=TRIAGE)
    args = ap.parse_args()

    idx = node_index()
    alias = load_alias()
    by_record, by_jira = build_ref_maps(idx)
    terms = label_terms(idx)

    # Pass 1: score everything, and note which node each source document
    # resolved to. Most unmatched records are action items extracted from a
    # MOM ("6 - Send invitation for the UAT"), where the topic lives in the
    # document, not the line. Their confidently-filed siblings from the same
    # document are the missing context.
    scored, cohort = {}, {}
    loaded = {}
    for name, spec in LEDGERS.items():
        path = os.path.join(STATE, spec["file"])
        with open(path) as fh:
            loaded[name] = (path, json.load(fh))
        for rid, rec in loaded[name][1]["items"].items():
            text = blob(rec, spec["text"])
            scored[(name, rid)] = score_record(
                text, idx, terms, rec.get("initiative_id"), alias,
                by_record, by_jira, rid)
            nid, conf, _why, _r = scored[(name, rid)]
            src = source_key(rec)
            if src and nid and conf >= args.threshold:
                cohort.setdefault(src, []).append(nid)

    # Pass 2: a source document with a clear majority node lends it to its
    # unmatched records. A split document lends nothing.
    for src, nids in list(cohort.items()):
        top, n = collections.Counter(nids).most_common(1)[0]
        cohort[src] = top if n >= max(2, 0.6 * len(nids)) else None

    triage, summary = [], {}
    for name, spec in LEDGERS.items():
        path, state = loaded[name]
        items = state["items"]
        filed = auto = amb = 0
        for rid, rec in items.items():
            if rec.get("node") and rec["node"] != UNFILED:
                filed += 1
                continue
            nid, conf, why, runners = scored[(name, rid)]
            if conf < args.threshold:
                inherited = cohort.get(source_key(rec))
                if inherited:
                    nid, conf, why = inherited, 0.76, f"cohort:{source_key(rec)}"
            if nid and conf >= args.threshold:
                auto += 1
                if args.apply:
                    rec["node"] = nid
                    rec["node_why"] = f"backfill:{why}"
            else:
                amb += 1
                if args.apply:
                    rec["node"] = UNFILED
                    rec["node_why"] = "backfill:ambiguous"
                label = (rec.get("text") or rec.get("title") or rec.get("what") or "")[:110]
                cands = [c for c in ([nid] if nid else []) + runners if c]
                if len(cands) < 3:
                    cands += [c for c in suggest(label, idx, n=3) if c not in cands]
                triage.append({
                    "ledger": name, "id": rid, "status": rec.get("status"),
                    "what": label, "project": rec.get("project"),
                    "candidates": cands[:3], "best_conf": round(conf, 2),
                })
        summary[name] = {"total": len(items), "already_filed": filed,
                         "auto_mapped": auto, "to_triage": amb}
        if args.apply:
            tmp = path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(state, fh, indent=1, ensure_ascii=False)
                fh.write("\n")
            os.replace(tmp, path)

    tot = {k: sum(s[k] for s in summary.values()) for k in
           ("total", "already_filed", "auto_mapped", "to_triage")}
    for name, s in summary.items():
        print(f"{name:<12} total {s['total']:>4}  filed {s['already_filed']:>4}  "
              f"auto {s['auto_mapped']:>4}  triage {s['to_triage']:>4}")
    pct = 100.0 * (tot["already_filed"] + tot["auto_mapped"]) / max(tot["total"], 1)
    print(f"{'TOTAL':<12} total {tot['total']:>4}  filed {tot['already_filed']:>4}  "
          f"auto {tot['auto_mapped']:>4}  triage {tot['to_triage']:>4}   coverage {pct:.0f}%")

    if args.apply:
        with open(args.triage_out, "w") as fh:
            json.dump({"_note": "Records the backfill would not guess. Clear with "
                                "<ledger>.py refile <ID> --node <node>.",
                       "count": len(triage), "items": triage}, fh, indent=1,
                      ensure_ascii=False)
            fh.write("\n")
        print(f"\nwrote {len(triage)} record(s) to {args.triage_out}")
    else:
        print("\ndry run. Re-run with --apply to write.")
    return 0

if __name__ == "__main__":
    _apply = "--apply" in sys.argv
    if _apply:
        from ledger_lock import hold_ledger_lock
        for _l in ("commitments", "decisions", "waiting_on"):
            hold_ledger_lock(_l)
    sys.exit(main())
