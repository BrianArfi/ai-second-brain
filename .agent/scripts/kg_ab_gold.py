#!/usr/bin/env python3
"""kg_ab_gold.py - build a blind gold set for the graph-vs-grep A/B test.

The point of this file is to remove the author of the tool from the design of
the exam. Topics and answers are lifted mechanically out of evening updates
that were written BEFORE the knowledge graph existed, by a different process.
Nobody picks a question because the graph happens to answer it well.

One task = one paragraph of a past evening update.
  topic  = the paragraph's heading, stripped of narrative
  gold   = every ledger id and repo document path that paragraph cited

An arm passes a task by recovering the cited sources from the topic alone.

Usage:
  python3 .agent/scripts/kg_ab_gold.py --list
  python3 .agent/scripts/kg_ab_gold.py --out journal/state/kg_ab_tasks.json
"""

import argparse
import json
import os
import re
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DASHBOARD = os.path.join(REPO, "Dashboard.md")

LEDGER_ID = re.compile(r"\b((?:COM|WAIT|DEC)-\d{3,})\b")
JIRA_ID = re.compile(r"\b((?:MBA|STOR|MPS|SPL)-\d+)\b")
MD_PATH = re.compile(r"\]\((?!https?:|mailto:|#)([^)]+?\.md)(?:#[^)]*)?\)")
DAY_HEAD = re.compile(r"^##\s+[^\n]*?(July|Jul|August|Agu)[^\n]*$", re.M)
SUB_HEAD = re.compile(r"^###\s+(.+?)\s*$", re.M)

# Words that carry narrative voice rather than subject matter. A topic must
# read like something the owner would actually type into a search, not like a
# headline, otherwise the test measures headline-writing and not retrieval.
NOISE = re.compile(
    r"\b(dan|yang|itu|ini|tapi|di|ke|dari|jadi|tidak|belum|sudah|masih|akan|"
    r"karena|untuk|dengan|pada|dalam|sendiri|justru|ternyata|malah|lagi|"
    r"hari|malam|pagi|kemarin|sekarang|nanti|semua|tiga|dua|empat|lima|"
    r"the|and|of|to|in|on|is|are|was|were|a|an)\b", re.I)

def make_topic(title, para):
    """A realistic question, with every answer redacted out of it.

    Using the heading alone produced unusable stubs like 'Skor terhadap
    Rencana'. Using the opening prose instead gives something a person would
    actually ask, provided all identifiers and links are stripped first: the
    exam must never contain its own answer key.
    """
    body = para.split("\n", 1)[1] if "\n" in para else ""
    body = re.sub(r"^[>|#*\-\s]+", "", body, flags=re.M)
    body = MD_PATH.sub(" ", body)
    body = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", body)      # keep link text
    body = LEDGER_ID.sub(" ", body)
    body = JIRA_ID.sub(" ", body)
    body = re.sub(r"[`*_>|]", " ", body)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", body) if s.strip()]
    lead = " ".join(sentences[:2])[:260]
    clean_title = LEDGER_ID.sub(" ", JIRA_ID.sub(" ", title)).strip()
    topic = f"{clean_title}. {lead}".strip(". ").strip()
    return re.sub(r"\s+", " ", topic)

def leaks(topic, gold):
    """Any gold identifier or path fragment surviving in the topic."""
    t = topic.lower()
    bad = []
    for g in gold:
        if g.lower() in t:
            bad.append(g)
        elif "/" in g and os.path.basename(g).lower().replace(".md", "") in t:
            bad.append(g)
    return bad

def sections(txt):
    """Split the dashboard into day sections, then into ### paragraphs."""
    heads = [(m.start(), m.group(0)) for m in DAY_HEAD.finditer(txt)]
    for i, (pos, head) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else len(txt)
        yield head, txt[pos:end]

def build_tasks():
    txt = open(DASHBOARD, encoding="utf-8").read()
    tasks = []
    for day_head, body in sections(txt):
        day = re.sub(r"^##\s*[^\w]*", "", day_head).split(":")[0].strip()
        subs = [(m.start(), m.group(1)) for m in SUB_HEAD.finditer(body)]
        for j, (pos, title) in enumerate(subs):
            end = subs[j + 1][0] if j + 1 < len(subs) else len(body)
            para = body[pos:end]
            gold_ids = sorted(set(LEDGER_ID.findall(para)) | set(JIRA_ID.findall(para)))
            gold_docs = sorted({p.replace("%20", " ") for p in MD_PATH.findall(para)
                                if not p.startswith("file://")})
            gold = gold_ids + gold_docs
            if len(gold) < 3:
                continue        # too thin to score
            topic = make_topic(title, para)
            if len(topic.split()) < 8:
                continue
            bad = leaks(topic, gold)
            if bad:
                # Never ship a task whose prompt contains its own answer.
                continue
            tasks.append({
                "id": f"T{len(tasks) + 1:02d}",
                "day": day,
                "source_heading": title,
                "topic": topic,
                "gold": gold,
                "gold_size": len(gold),
            })
    return tasks

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    tasks = build_tasks()
    print(f"{len(tasks)} scorable tasks "
          f"({sum(t['gold_size'] for t in tasks)} gold items total)\n")
    if args.list or not args.out:
        for t in tasks:
            print(f"  {t['id']}  [{t['day']}]  gold={t['gold_size']}")
            print(f"       topic: {t['topic']}")
            print(f"       gold : {', '.join(t['gold'][:8])}"
                  f"{' ...' if len(t['gold']) > 8 else ''}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump({"note": "Gold set mined from evening updates written "
                               "before the knowledge graph existed. Arms must "
                               "never see the gold field.",
                       "tasks": tasks}, fh, ensure_ascii=False, indent=1)
        print(f"\nwrote {args.out}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
