"""Create outcome initiatives in Work Linear and attach existing projects to them.

Initiative = a dated outcome (quarter goal, OKR-like) with a success metric.
Project = a deliverable, an MVP included. Milestone = a checkpoint inside one project.

    python3 initiative_restructure.py --plan <plan.json>                  # dry run, writes nothing
    python3 initiative_restructure.py --plan <plan.json> --apply --approved

The plan format is Clients/Work/Roadmap_2026-27/linear_initiatives_plan_2026-11-18.json.
Bulk Linear changes are announced first and applied only after 2 hours with no concern
(memory: feedback-linear-changes-announce-wait-2h). --approved goes on only after that.
"""
import argparse
import json
import sys

import linear_client as L
import linear_standard as S

OWNER_EMAIL = "you@yourcompany.com"

PROJECTS_Q = """query($after:String){projects(first:50,after:$after){nodes{id name state
  initiatives{nodes{name}}} pageInfo{hasNextPage endCursor}}}"""
INITS_Q = """query($after:String){initiatives(first:50,after:$after){nodes{id name}
  pageInfo{hasNextPage endCursor}}}"""
CREATE_INIT = """mutation($input:InitiativeCreateInput!){initiativeCreate(input:$input){
  success initiative{id name url}}}"""
LINK = """mutation($input:InitiativeToProjectCreateInput!){initiativeToProjectCreate(input:$input){success}}"""

def description(i, links):
    rows = "\n".join(f"- {p}" for p in i["roadmap_projects"])
    return (f"## Outcome\n{i['outcome']}\n\n## Success metric\n{i['metric']}\n\n"
            f"## Owner and squads\nOwner: {i['owner']}. Squads: {i['squads']}.\n\n"
            f"## Roadmap projects\n{rows}\n\n## Links\n{links}\n")

def resolve(name, projects):
    exact = [p for p in projects if p["name"] == name]
    if exact:
        return exact
    return [p for p in projects if p["name"].startswith(name)]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--approved", action="store_true")
    a = ap.parse_args()
    if a.apply and not a.approved:
        sys.exit("Refusing to write: --apply needs --approved (announced, 2 hours, no concern).")

    plan = json.load(open(a.plan, encoding="utf-8"))
    projects = [p for p in L.paginate(PROJECTS_Q, {}, ["projects"]) if p["state"] != "canceled"]
    existing = {i["name"]: i["id"] for i in L.paginate(INITS_Q, {}, ["initiatives"])}
    owner_id = L._user_id(OWNER_EMAIL) if a.apply else None

    problems, links_todo = 0, []
    for i in plan["initiatives"]:
        desc = description(i, plan["links"])
        issues = S.check_initiative(i["name"], desc, i["owner"], i["target"])
        tag = "EXISTS" if i["name"] in existing else "CREATE"
        print(f"\n[{i['key']}] {tag} {i['name']} | target {i['target']}")
        for x in issues:
            problems += 1
            print(f"   STANDARD: {x}")
        for n in i["linear_projects"]:
            hit = resolve(n, projects)
            if len(hit) != 1:
                problems += 1
                print(f"   UNRESOLVED ({len(hit)} matches): {n}")
                continue
            p = hit[0]
            cur = [x["name"] for x in p["initiatives"]["nodes"]]
            note = f" (already under: {', '.join(cur)})" if cur else ""
            print(f"   link: {p['name']}{note}")
            links_todo.append((i, p))

    print(f"\n{len(plan['initiatives'])} initiatives, {len(links_todo)} project links, {problems} problems.")
    if not a.apply:
        print("Dry run. Nothing written.")
        return
    if problems:
        sys.exit("Fix the problems above before --apply.")

    ids = dict(existing)
    for i in plan["initiatives"]:
        if i["name"] in ids:
            continue
        r = L.gql(CREATE_INIT, {"input": {"name": i["name"], "description": description(i, plan["links"]),
                                          "ownerId": owner_id, "targetDate": i["target"], "status": "Planned"}})
        ids[i["name"]] = r["initiativeCreate"]["initiative"]["id"]
        print(f"created {i['name']}: {r['initiativeCreate']['initiative']['url']}")
    for i, p in links_todo:
        if i["name"] in [x["name"] for x in p["initiatives"]["nodes"]]:
            continue
        L.gql(LINK, {"input": {"initiativeId": ids[i["name"]], "projectId": p["id"]}})
        print(f"linked {p['name']} -> {i['name']}")

if __name__ == "__main__":
    main()
