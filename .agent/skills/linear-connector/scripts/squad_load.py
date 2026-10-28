"""Points and load per squad, read-only.

Work runs on Linear Basic: one team per World, so cycles and cycle charts are
per World, not per squad. This report splits a World cycle by the "Squad" label
and sums estimates per squad and per person.

  python3 .agent/skills/linear-connector/scripts/squad_load.py [--team W1] [--cycle active|next|<number>] [--out PATH] [--json]

Per squad: issues, open issues, points (done / in progress / not started), unestimated
issues, and open issues/points per assignee (issue count is the load
measure while most issues carry no estimate). Issues with no Squad label land in "(no squad)".
Completed and canceled issues count as done and dropped; dropped is shown apart.
No Linear writes.
"""
import argparse
import collections
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
try:
    import linear_client as L
except SystemExit as e:  # missing API key
    print(str(e), file=sys.stderr)
    sys.exit(2)

TEAMS = ("W1", "W2", "W3")
NO_SQUAD = "(no squad)"

CYCLES_Q = """query($key:String!){teams(filter:{key:{eq:$key}}){nodes{
  activeCycle{id number startsAt endsAt}
  cycles(first:20, filter:{isPast:{eq:false}}){nodes{id number startsAt endsAt isActive}}}}}"""

ISSUES_Q = """query($cycle:ID!, $after:String){issues(first:100, after:$after,
  filter:{cycle:{id:{eq:$cycle}}}){
  nodes{identifier estimate state{type}
    assignee{name}
    labels{nodes{name parent{name}}}}
  pageInfo{hasNextPage endCursor}}}"""

def pick_cycle(team, which):
    t = L.gql(CYCLES_Q, {"key": team})["teams"]["nodes"][0]
    cycles = sorted(t["cycles"]["nodes"], key=lambda c: c["number"])
    if which == "active":
        return t["activeCycle"]
    if which == "next":
        act = t["activeCycle"]["number"] if t["activeCycle"] else 0
        return next((c for c in cycles if c["number"] > act), None)
    return next((c for c in cycles if str(c["number"]) == str(which)), None)

def squad_of(issue):
    for lab in issue["labels"]["nodes"]:
        if lab["parent"] and lab["parent"]["name"].lower() == "squad":
            return lab["name"]
    return NO_SQUAD

def bucket(state_type):
    if state_type == "completed":
        return "done"
    if state_type == "canceled":
        return "dropped"
    if state_type == "started":
        return "in_progress"
    return "not_started"  # backlog, unstarted, triage

def summarise(team, cycle):
    issues = L.paginate(ISSUES_Q, {"cycle": cycle["id"]}, ["issues"])
    squads = collections.defaultdict(lambda: {
        "issues": 0, "points": 0, "done": 0, "in_progress": 0, "not_started": 0,
        "dropped": 0, "unestimated": 0, "open_issues": 0,
        "people": collections.defaultdict(lambda: [0, 0])})
    for i in issues:
        s = squads[squad_of(i)]
        b = bucket(i["state"]["type"])
        pts = i["estimate"] or 0
        if b == "dropped":
            s["dropped"] += 1
            continue
        s["issues"] += 1
        if i["estimate"] is None:
            s["unestimated"] += 1
        s["points"] += pts
        s[b] += pts
        if b != "done":
            s["open_issues"] += 1
            who = s["people"][(i["assignee"] or {}).get("name", "(unassigned)")]
            who[0] += 1
            who[1] += pts
    return {"team": team, "cycle": cycle["number"],
            "starts": cycle["startsAt"][:10], "ends": cycle["endsAt"][:10],
            "squads": {k: {**v, "people": dict(sorted(v["people"].items(), key=lambda x: (-x[1][0], x[0])))}
                       for k, v in sorted(squads.items())}}

def to_markdown(reports):
    out = ["# Points and load per squad", ""]
    for r in reports:
        out += [f"## {r['team']} cycle {r['cycle']} ({r['starts']} to {r['ends']})", "",
                "| Squad | Issues | Open issues | Points | Done | In progress | Not started | % done | Unestimated | Dropped |",
                "| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for name, s in r["squads"].items():
            pct = f"{100 * s['done'] / s['points']:.0f}%" if s["points"] else "-"
            out.append(f"| {name} | {s['issues']} | {s['open_issues']} | {s['points']:g} | {s['done']:g} | {s['in_progress']:g} | "
                       f"{s['not_started']:g} | {pct} | {s['unestimated']} | {s['dropped']} |")
        out += ["", "Open load per person (issues / points, in progress + not started):", ""]
        for name, s in r["squads"].items():
            if s["people"]:
                people = ", ".join(f"{p} {n}/{v:g}" for p, (n, v) in s["people"].items())
                out.append(f"- **{name}**: {people}")
        out.append("")
    return "\n".join(out)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", choices=TEAMS, default=None, help="default: all three Worlds")
    ap.add_argument("--cycle", default="active", help="active, next, or a cycle number")
    ap.add_argument("--out", default=None, help="write the markdown report here")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    reports = []
    for team in ([a.team] if a.team else TEAMS):
        cycle = pick_cycle(team, a.cycle)
        if not cycle:
            print(f"[WARN] {team}: no cycle '{a.cycle}'", file=sys.stderr)
            continue
        reports.append(summarise(team, cycle))
    if a.json:
        print(json.dumps(reports, indent=2, ensure_ascii=False))
        return
    md = to_markdown(reports)
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(md + "\n")
        print(f"wrote {a.out}")
    else:
        print(md)

if __name__ == "__main__":
    main()
