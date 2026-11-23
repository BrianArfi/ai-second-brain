"""Daily Squad auto-label for open W1/W2/W3 issues (Linear rule 1).

Gives an open issue with no Squad label the Squad its evidence names, and
leaves everything else for linear_rules_check.py to report. Conservative by
design: a wrong Squad hides work from the right squad view, so an issue
without clear evidence stays unlabelled.

Evidence, first match wins:
  1. Parent issue carries a Squad label.
  2. Project carries a Product label that is also a Squad label in the
     issue's team (Product "DevOps" = Squad "DevOps Squad").
  3. Jira source board in the synced Jira link: TRAV Travel Shop, MBA B2C
     Super App, MSP and STOR Ecom Solutions, ML LiveOps, MIM Peoppl Suite,
     RP Giftcardsby and Rewards, SP ExamplePlatform. SP is skipped when the
     project or title names LMS, Spotlight or SACO (those sit on the SP board
     but are not ExamplePlatform work). AFM is skipped (Example Program Migration
     carries Travel Shop and Ecom work alike).

    python3 .agent/skills/linear-connector/scripts/linear_squad_autolabel.py [--team W1] [--out PATH]
    python3 .agent/skills/linear-connector/scripts/linear_squad_autolabel.py --apply --approved

Without --apply it is a dry run and prints the plan. --apply needs --approved
(the owner gave standing approval for this daily job on 29 Oct 2026; the cron line
passes it). Only Squad labels are ever added; nothing is removed or renamed.
"""
import argparse
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
try:
    import linear_client as L
except SystemExit as e:  # missing API key
    print(str(e), file=sys.stderr)
    sys.exit(2)
sys.path.insert(0, os.path.join(L.REPO_ROOT, ".agent", "scripts"))
from file_utils import require_send_approval  # noqa: E402

WORLDS = ("W1", "W2", "W3")
BOARD_SQUAD = {"TRAV": "Travel Shop", "MBA": "B2C Super App", "MSP": "Ecom Solutions",
               "STOR": "Ecom Solutions", "ML": "LiveOps", "MIM": "Peoppl Suite",
               "RP": "Giftcardsby and Rewards", "SP": "ExamplePlatform"}
SP_SKIP = re.compile(r"\b(LMS|Spotlight|SACO|Offer-to-Product)\b", re.I)
PRODUCT_TO_SQUAD = {"DevOps": "DevOps Squad",
                    # 17 Nov 2026: Product label split into two products, one squad
                    "GiftCardsBy": "Giftcardsby and Rewards",
                    "Rewards Portal": "Giftcardsby and Rewards"}

LABELS_Q = """query($after:String){issueLabels(first:100,after:$after){
 nodes{id name team{key} parent{name} retiredAt} pageInfo{hasNextPage endCursor}}}"""
ISSUES_Q = """query($after:String,$f:IssueFilter){issues(first:25,after:$after,filter:$f){
 nodes{id identifier title url team{key}
  labels(first:10){nodes{name parent{name}}}
  parent{labels(first:10){nodes{name parent{name}}}}
  project{name labels(first:10){nodes{name parent{name}}}}
  attachments(first:5){nodes{url title}}}
 pageInfo{hasNextPage endCursor}}}"""
ADD_M = """mutation($id:String!,$l:[String!]!){issueUpdate(id:$id,input:{addedLabelIds:$l}){success}}"""

def squad_labels():
    out = {}
    for l in L.paginate(LABELS_Q, {}, ["issueLabels"]):
        if (l.get("parent") or {}).get("name") == "Squad" and l.get("team") and not l.get("retiredAt"):
            out[(l["team"]["key"], l["name"])] = l["id"]
    return out

def squads_of(labels):
    return [l["name"] for l in (labels or {}).get("nodes", []) if (l.get("parent") or {}).get("name") == "Squad"]

def decide(i, valid):
    team = i["team"]["key"]
    ok = lambda s: s if (team, s) in valid else None
    par = squads_of((i.get("parent") or {}).get("labels"))
    if len(par) == 1 and ok(par[0]):
        return par[0], "parent squad"
    proj = i.get("project") or {}
    prods = [l["name"] for l in (proj.get("labels") or {}).get("nodes", []) if (l.get("parent") or {}).get("name") == "Product"]
    if len(prods) == 1:
        s = ok(PRODUCT_TO_SQUAD.get(prods[0], prods[0]))
        if s:
            return s, f"project Product label {prods[0]}"
    text = " ".join((a.get("url") or "") + " " + (a.get("title") or "") for a in i["attachments"]["nodes"])
    boards = set(re.findall(r"\b(" + "|".join(BOARD_SQUAD) + r"|AFM)-\d+\b", text))
    if len(boards) == 1:
        b = boards.pop()
        if b == "SP" and SP_SKIP.search((proj.get("name") or "") + " " + i["title"]):
            return None, "SP board but LMS/Spotlight/SACO work"
        if b in BOARD_SQUAD and ok(BOARD_SQUAD[b]):
            return BOARD_SQUAD[b], f"Jira board {b}"
    return None, "no clear evidence"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", choices=WORLDS)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--approved", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args()
    if a.apply:
        require_send_approval("add Squad labels to Linear issues", a.approved, destination="Linear")
    valid = squad_labels()
    flt = {"team": {"key": {"in": [a.team] if a.team else list(WORLDS)}},
           "state": {"type": {"nin": ["triage", "completed", "canceled"]}}}
    done, skipped = [], []
    for i in L.paginate(ISSUES_Q, {"f": flt}, ["issues"]):
        if squads_of(i["labels"]):
            continue
        squad, why = decide(i, valid)
        if not squad:
            skipped.append((i["identifier"], i["title"], why))
            continue
        if a.apply:
            L.gql(ADD_M, {"id": i["id"], "l": [valid[(i["team"]["key"], squad)]]})
        done.append((i["identifier"], i["title"], squad, why))
    verb = "Labelled" if a.apply else "Would label"
    lines = [f"# Squad auto-label ({'applied' if a.apply else 'dry run'})", "",
             f"{verb} {len(done)}; left for the rules report {len(skipped)}.", "",
             "| Issue | Title | Squad | Evidence |", "| :--- | :--- | :--- | :--- |"]
    lines += [f"| {k} | {t[:60].replace('|', '/')} | {s} | {w} |" for k, t, s, w in done]
    lines += ["", "## Left unlabelled", "", "| Issue | Title | Why |", "| :--- | :--- | :--- |"]
    lines += [f"| {k} | {t[:60].replace('|', '/')} | {w} |" for k, t, w in skipped]
    text = "\n".join(lines) + "\n"
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text)
    print(f"{verb} {len(done)}, skipped {len(skipped)}" + (f" -> {a.out}" if a.out else ""))
    if not a.out:
        print(text)

if __name__ == "__main__":
    main()
