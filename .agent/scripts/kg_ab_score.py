#!/usr/bin/env python3
"""kg_ab_score.py - score the graph-vs-grep A/B, mechanically and blind.

The scorer never sees which arm is which until the very last line. Arms are
supplied as arm1/arm2; the mapping is read from a separate key file that is
only opened after every number has been computed. This is the part of the
protocol that stops the tool's author from grading his own tool.

Arm result format (one JSON file per arm):
  {"T01": ["COM-0425", "Clients/Work/OMS/PRD_OMS_Refund_Flow.md"], "T02": [...]}

Scoring:
  recall    = |found ∩ gold| / |gold|     the metric that decides pass/fail
  precision = |found ∩ gold| / |found|    reported, not decisive
  A miss is a fact the evening update would have lost. Recall is not tradeable.

Significance: paired sign test over tasks. With n around 30 an eyeballed
average is not evidence, and a two-point recall gap is noise.

Usage:
  python3 .agent/scripts/kg_ab_score.py --arm1 a1.json --arm2 a2.json \
      --tasks journal/state/kg_ab_tasks.json [--key key.json]
"""

import argparse
import json
import math
import os
import sys

def norm(x):
    x = str(x).strip().strip("`\"'")
    x = x.replace("%20", " ")
    return x.lower().rstrip("/")

def match(found, gold):
    """A gold item counts as found on an exact id match, or, for paths, when
    the basenames agree. Directory prefixes differ harmlessly between arms."""
    f = {norm(x) for x in found}
    fb = {os.path.basename(x) for x in f}
    hit = set()
    for g in gold:
        gn = norm(g)
        if gn in f or (("/" in gn) and os.path.basename(gn) in fb):
            hit.add(g)
    return hit

def sign_test(diffs):
    """Two-sided exact sign test. Ties dropped, as the test requires."""
    pos = sum(1 for d in diffs if d > 0)
    neg = sum(1 for d in diffs if d < 0)
    n = pos + neg
    if n == 0:
        return 1.0, pos, neg
    k = min(pos, neg)
    tail = sum(math.comb(n, i) for i in range(k + 1))
    p = min(1.0, 2 * tail / (2 ** n))
    return p, pos, neg

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", default="journal/state/kg_ab_tasks.json")
    ap.add_argument("--arm1", required=True)
    ap.add_argument("--arm2", required=True)
    ap.add_argument("--key", help="JSON mapping arm1/arm2 to grep/graph. "
                                  "Opened only after scoring.")
    ap.add_argument("--budget", type=int, default=15,
                    help="tool calls each arm was allowed (for the record)")
    args = ap.parse_args()

    tasks = json.load(open(args.tasks, encoding="utf-8"))["tasks"]
    a1 = json.load(open(args.arm1, encoding="utf-8"))
    a2 = json.load(open(args.arm2, encoding="utf-8"))

    rows, d_recall = [], []
    for t in tasks:
        gold = t["gold"]
        f1, f2 = a1.get(t["id"], []), a2.get(t["id"], [])
        h1, h2 = match(f1, gold), match(f2, gold)
        r1, r2 = len(h1) / len(gold), len(h2) / len(gold)
        p1 = len(h1) / len(f1) if f1 else 0.0
        p2 = len(h2) / len(f2) if f2 else 0.0
        rows.append((t["id"], len(gold), r1, p1, r2, p2, sorted(set(gold) - h1),
                     sorted(set(gold) - h2)))
        d_recall.append(r1 - r2)

    n = len(rows)
    R1 = sum(r[2] for r in rows) / n
    R2 = sum(r[4] for r in rows) / n
    P1 = sum(r[3] for r in rows) / n
    P2 = sum(r[5] for r in rows) / n
    total_gold = sum(r[1] for r in rows)
    miss1 = sum(len(r[6]) for r in rows)
    miss2 = sum(len(r[7]) for r in rows)

    print(f"tasks {n}   gold items {total_gold}   tool-call budget per arm {args.budget}\n")
    print(f"{'task':6} {'gold':>5} {'arm1 R':>7} {'arm1 P':>7} {'arm2 R':>7} {'arm2 P':>7}")
    for tid, g, r1, p1, r2, p2, _, _ in rows:
        flag = "  <" if r1 > r2 else ("  >" if r2 > r1 else "")
        print(f"{tid:6} {g:>5} {r1:>7.2f} {p1:>7.2f} {r2:>7.2f} {p2:>7.2f}{flag}")

    print(f"\n{'MEAN':6} {total_gold:>5} {R1:>7.2f} {P1:>7.2f} {R2:>7.2f} {P2:>7.2f}")
    print(f"missed gold items: arm1 {miss1}/{total_gold}   arm2 {miss2}/{total_gold}")

    p, pos, neg = sign_test(d_recall)
    print(f"\npaired sign test on recall: arm1 better on {pos} tasks, "
          f"arm2 better on {neg}, tied {n - pos - neg}")
    print(f"  p = {p:.4f}  ->  {'significant at 0.05' if p < 0.05 else 'NOT significant'}")

    print("\nPRE-REGISTERED CRITERIA")
    print("  pass  : recall >= other arm AND read-cost down >= 30%")
    print("  fail  : any recall loss, at any cost saving")
    print("  fail  : difference not significant (added complexity unpaid)")

    if args.key and os.path.exists(args.key):
        key = json.load(open(args.key, encoding="utf-8"))
        print(f"\nUNBLINDING: arm1 = {key.get('arm1')}   arm2 = {key.get('arm2')}")
    else:
        print("\nstill blind: pass --key to reveal which arm was which")
    return 0

if __name__ == "__main__":
    sys.exit(main())
