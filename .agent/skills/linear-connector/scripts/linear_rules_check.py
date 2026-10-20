"""Daily Linear rules checker. Read-only: lists every violation of three rules.

Linear cannot block these at write time, so enforcement is a report.

  1. Every open issue in teams W1, W2, W3 has a label from the team label group "Squad".
     Exempt: Triage-type states, completed and canceled issues.
  2. Every project has exactly one label from the workspace project label group "Product".
     Exempt: completed and canceled projects, and the Scratch team (ZZT).
  3. Every initiative has an owner, a target date, and a description with a
     "Success metric" heading followed by non-empty text.

    python3 .agent/skills/linear-connector/scripts/linear_rules_check.py [--out PATH] [--json] [--commit]

Exit code: 0 clean, 1 violations, 2 API error. No Slack, no email, no Linear writes.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
try:
    import linear_client as L
except SystemExit as e:  # missing API key
    print(str(e), file=sys.stderr)
    sys.exit(2)

REPO_ROOT = L.REPO_ROOT
WORLDS = ("W1", "W2", "W3")
IGNORE_TEAM = "ZZT"
DONE_PROJECT = {"completed", "canceled", "cancelled"}

ISSUES_Q = """query($after:String,$f:IssueFilter){issues(first:50,after:$after,filter:$f){
 nodes{identifier title url team{key} state{name type} assignee{name} creator{name}
  labels(first:15){nodes{name parent{name}}}}
 pageInfo{hasNextPage endCursor}}}"""
PROJECTS_Q = """query($after:String){projects(first:50,after:$after){
 nodes{id name url state status{type name} lead{name} creator{name} teams(first:5){nodes{key}}
  labels(first:15){nodes{name parent{name}}}}
 pageInfo{hasNextPage endCursor}}}"""
INITS_Q = """query($after:String){initiatives(first:100,after:$after){
 nodes{id name url status owner{name} creator{name} targetDate description content}
 pageInfo{hasNextPage endCursor}}}"""

def wib_today():
    now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=7)
    return now.date().isoformat()

def check_issues():
    flt = {"team": {"key": {"in": list(WORLDS)}},
           "state": {"type": {"nin": ["triage", "completed", "canceled"]}}}
    out = []
    for i in L.paginate(ISSUES_Q, {"f": flt}, ["issues"]):
        groups = [(l.get("parent") or {}).get("name") for l in i["labels"]["nodes"]]
        if "Squad" in groups:
            continue
        out.append({"world": i["team"]["key"], "id": i["identifier"], "title": i["title"],
                    "url": i["url"], "state": i["state"]["name"],
                    "owner": (i["assignee"] or {}).get("name") or "UNASSIGNED"})
    return out

def check_projects():
    out = []
    for p in L.paginate(PROJECTS_Q, {}, ["projects"]):
        teams = [t["key"] for t in p["teams"]["nodes"]]
        if teams and all(t == IGNORE_TEAM for t in teams):
            continue
        st = ((p.get("status") or {}).get("type") or p.get("state") or "").lower()
        if st in DONE_PROJECT:
            continue
        prod = [l["name"] for l in p["labels"]["nodes"] if (l.get("parent") or {}).get("name") == "Product"]
        if len(prod) == 1:
            continue
        world = next((t for t in teams if t in WORLDS), None) or (teams[0] if teams else "No team")
        out.append({"world": world, "name": p["name"], "url": p["url"],
                    "problem": "no Product label" if not prod else "several Product labels: " + ", ".join(prod),
                    "owner": (p["lead"] or {}).get("name") or (p["creator"] or {}).get("name") or "NO LEAD"})
    return out

def success_metric_ok(text):
    """True when a 'Success metric' heading is followed by non-empty text."""
    lines = (text or "").splitlines()
    head = re.compile(r"^\s*(#{1,6}\s*)?(\*\*|__)?\s*success\s+metrics?\s*:?\s*(\*\*|__)?\s*:?\s*$", re.I)
    any_head = re.compile(r"^\s*#{1,6}\s")
    for n, line in enumerate(lines):
        if head.match(line):
            for nxt in lines[n + 1:]:
                if any_head.match(nxt):
                    break
                if nxt.strip() and not re.fullmatch(r"[\s\-*_=#]*", nxt):
                    return True
            return False
    return False

def check_initiatives():
    out = []
    for i in L.paginate(INITS_Q, {}, ["initiatives"]):
        why = []
        if not i["owner"]:
            why.append("no owner")
        if not i["targetDate"]:
            why.append("no target date")
        if not (success_metric_ok(i.get("content")) or success_metric_ok(i.get("description"))):
            why.append("no Success metric heading with text")
        if why:
            out.append({"world": "Workspace", "name": i["name"], "url": i["url"],
                        "problem": ", ".join(why),
                        "owner": (i["owner"] or {}).get("name") or (i["creator"] or {}).get("name") or "NO OWNER"})
    return out

def render(res, date):
    n1, n2, n3 = (len(res[k]) for k in ("squad", "product", "initiative"))
    total = n1 + n2 + n3
    label = "0 violations" if not total else f"{total} violations"
    lines = [f"# Linear rules check {date}", "",
             f"**{label}** | Rule 1 Squad label: {n1} | Rule 2 Product label: {n2} | Rule 3 Initiative: {n3}", ""]
    titles = {"squad": "Rule 1: every open W1/W2/W3 issue has a Squad label",
              "product": "Rule 2: every project has exactly one Product label",
              "initiative": "Rule 3: every initiative has owner, target date, Success metric"}
    for key in ("squad", "product", "initiative"):
        rows = res[key]
        lines += [f"## {titles[key]} ({len(rows)})", ""]
        if not rows:
            lines += ["0 violations", ""]
            continue
        for w in sorted({r["world"] for r in rows}):
            lines += [f"### {w}", ""]
            for r in sorted((r for r in rows if r["world"] == w), key=lambda r: r.get("id") or r["name"]):
                if key == "squad":
                    lines.append(f"- [{r['id']}]({r['url']}) {r['title']} ({r['state']}) - fix: {r['owner']}")
                else:
                    lines.append(f"- [{r['name']}]({r['url']}) - {r['problem']} - fix: {r['owner']}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"

def commit_outputs(paths, date):
    """Cron path: commit and push only the report files, never the whole tree."""
    def git(*args):
        return subprocess.run(["git", "-C", REPO_ROOT, *args], capture_output=True, text=True)
    rel = [os.path.relpath(p, REPO_ROOT).replace(os.sep, "/") for p in paths]
    git("add", *rel)
    if git("diff", "--cached", "--quiet", "--", *rel).returncode == 0:
        print("commit: nothing new")
        return
    git("commit", "-q", "-m", f"chore(linear-rules): daily check {date}", "--", *rel)
    pull = git("pull", "-q", "--rebase", "--autostash")
    push = git("push", "-q")
    print("commit: pushed" if push.returncode == 0 else
          f"commit: push failed ({(pull.stderr or push.stderr).strip()[:200]})")

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=os.path.join(REPO_ROOT, "journal", "linear_rules", "latest.md"))
    ap.add_argument("--commit", action="store_true", help="commit and push the report files (cron path)")
    ap.add_argument("--json", action="store_true", help="print JSON instead of markdown")
    a = ap.parse_args()
    try:
        res = {"squad": check_issues(), "product": check_projects(), "initiative": check_initiatives()}
    except Exception as e:  # transport, auth or GraphQL error
        print(f"linear_rules_check: API error: {e}", file=sys.stderr)
        return 2
    date = wib_today()
    md = render(res, date)
    out = os.path.abspath(a.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    paths = (out, os.path.join(os.path.dirname(out), f"{date}.md"))
    for path in paths:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(md)
    total = sum(len(v) for v in res.values())
    if a.commit:
        commit_outputs(paths, date)
    if a.json:
        print(json.dumps({"date": date, "total": total, "counts": {k: len(v) for k, v in res.items()}, **res}, indent=2))
    else:
        print(md)
    return 1 if total else 0

if __name__ == "__main__":
    sys.exit(main())
