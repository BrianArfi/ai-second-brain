"""Linear connector for Work's workspace (linear.app/yourcompany).

Linear is one GraphQL endpoint, so this client is a thin `gql()` plus a set of
verbs that mirror the Jira connector's shape: same portfolio boundary, same
concentration check, same digest, so a reader of one can read the other.

    python3 .agent/skills/linear-connector/scripts/linear_client.py verify-connection
    python3 .agent/skills/linear-connector/scripts/linear_client.py teams
    python3 .agent/skills/linear-connector/scripts/linear_client.py cycle-status --team ENG

Credentials: LINEAR_API_KEY, from the environment or `token.env` next to this
skill (gitignored; see token.env.example). Generate one at
Linear -> Settings -> Security & access -> API keys -> Personal API key.

Writes (create-issue, update-issue, comment) are gated by the shared outbound
approval helper and refuse to run without --approved.
"""
import argparse
import json
import os
import signal
import sys

import requests

# Force UTF-8 on Windows stdout/stderr to prevent encoding crashes
try:
    if sys.stdout.encoding != 'utf-8':
        sys.stdout.reconfigure(encoding='utf-8')
    if sys.stderr.encoding != 'utf-8':
        sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.abspath(os.path.join(SKILL_DIR, '..', '..', '..'))
sys.path.insert(0, os.path.join(REPO_ROOT, '.agent', 'scripts'))
from file_utils import require_send_approval  # noqa: E402

TEAMS_PATH = os.path.join(SKILL_DIR, "teams.json")
API_URL = "https://api.linear.app/graphql"
DEFAULT_TIMEOUT = 60
PAGE_SIZE = 100          # Linear's hard ceiling is 250; 100 keeps payloads sane

# Credentials come from env vars or a token.env next to this skill
# (token.env is gitignored; see token.env.example).
def _load_token_env():
    for candidate in (
        os.path.join(os.path.dirname(__file__), "..", "token.env"),
        os.path.join(os.path.dirname(__file__), "token.env"),
    ):
        if os.path.exists(candidate):
            with open(candidate) as fh:
                for line in fh:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip().strip('"'))

_load_token_env()

API_KEY = os.environ.get("LINEAR_API_KEY", "")
if not API_KEY:
    sys.exit(
        "linear_client: set LINEAR_API_KEY (env or .agent/skills/linear-connector/token.env).\n"
        "Generate one at Linear -> Settings -> Security & access -> API keys."
    )

# A personal API key goes in raw. `Bearer <token>` is the OAuth form and Linear
# rejects a personal key sent that way, which surfaces as a bare 400.
HEADERS = {"Authorization": API_KEY, "Content-Type": "application/json"}
WORKSPACE = os.environ.get("LINEAR_WORKSPACE", "yourcompany")

def issue_url(key):
    return f"https://linear.app/{WORKSPACE}/issue/{key}"

def team_url(key):
    return f"https://linear.app/{WORKSPACE}/team/{key}/all"

# ---------------------------------------------------------------- transport

def gql(query, variables=None):
    """One GraphQL round trip. Raises on transport AND on GraphQL-level errors.

    Linear returns HTTP 200 with an `errors` array for things like a bad field
    or a permission failure, so checking status_code alone reports success on a
    response that carries no data.
    """
    resp = requests.post(
        API_URL,
        json={"query": query, "variables": variables or {}},
        headers=HEADERS,
        timeout=DEFAULT_TIMEOUT,
    )
    if resp.status_code == 401:
        raise RuntimeError("Linear rejected the API key (401). Regenerate it in Settings -> Security & access.")
    if resp.status_code != 200:
        raise RuntimeError(f"Linear API {resp.status_code}: {resp.text[:400]}")
    body = resp.json()
    if body.get("errors"):
        msgs = "; ".join(e.get("message", str(e)) for e in body["errors"])
        raise RuntimeError(f"Linear GraphQL error: {msgs[:400]}")
    return body["data"]

def paginate(query, variables, path):
    """Page a connection to exhaustion. `path` walks data -> ... -> the connection.

    Every list endpoint here goes through this. Linear defaults to 50 per page,
    so a team with 300 issues silently returns 50 to a caller that forgets.
    """
    out, cursor = [], None
    while True:
        data = gql(query, {**variables, "after": cursor})
        node = data
        for step in path:
            node = node[step]
        out.extend(node.get("nodes", []))
        info = node.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            return out
        cursor = info.get("endCursor")
        if not cursor:
            return out

# ---------------------------------------------------------------- team map

def load_teams():
    """The committed team map, or None before discovery has been run."""
    if not os.path.exists(TEAMS_PATH):
        return None
    with open(TEAMS_PATH) as fh:
        return json.load(fh)

TEAMS = load_teams()

# Jira project keys already in this repo's namespace. A Linear team key that
# collides with one of these makes `ABC-123` ambiguous between two systems, and
# work_tree_link.py's JIRA_RE would claim it and link to Atlassian.
JIRA_KEYS = {"MP", "MPS", "MSP", "MBA", "STOR"}

def team_keys():
    return sorted((TEAMS or {}).get("teams", {}).keys())

def portfolio_teams(portfolio):
    if not TEAMS:
        sys.exit("linear_client: teams.json not written yet. Run `teams --write` first.")
    keys = TEAMS.get("portfolios", {}).get(portfolio)
    if keys is None:
        sys.exit(f"linear_client: unknown portfolio '{portfolio}'. Known: {sorted(TEAMS.get('portfolios', {}))}")
    return keys

# ---------------------------------------------------------------- read verbs

def verify_connection():
    data = gql("""
        query { viewer { id name email }
                organization { id name urlKey } }
    """)
    return {"viewer": data["viewer"], "organization": data["organization"]}

TEAMS_Q = """
query($after: String) {
  teams(first: %d, after: $after) {
    nodes { id key name description
            members { nodes { id } }
            activeCycle { id number startsAt endsAt } }
    pageInfo { hasNextPage endCursor }
  }
}
""" % PAGE_SIZE

def list_teams():
    nodes = paginate(TEAMS_Q, {}, ["teams"])
    out = []
    for t in nodes:
        out.append({
            "id": t["id"],
            "key": t["key"],
            "name": t["name"],
            "members": len((t.get("members") or {}).get("nodes") or []),
            "active_cycle": (t.get("activeCycle") or {}).get("number"),
            "url": team_url(t["key"]),
            "collides_with_jira": t["key"] in JIRA_KEYS,
        })
    return sorted(out, key=lambda t: t["key"])

PROJECTS_Q = """
query($after: String) {
  projects(first: %d, after: $after) {
    nodes { id name state progress targetDate
            lead { name }
            teams { nodes { key } } }
    pageInfo { hasNextPage endCursor }
  }
}
""" % PAGE_SIZE

def list_projects(team=None):
    nodes = paginate(PROJECTS_Q, {}, ["projects"])
    out = []
    for p in nodes:
        keys = [t["key"] for t in (p.get("teams") or {}).get("nodes", [])]
        if team and team not in keys:
            continue
        out.append({
            "id": p["id"], "name": p["name"], "state": p["state"],
            "progress": p.get("progress"), "target_date": p.get("targetDate"),
            "lead": (p.get("lead") or {}).get("name"), "teams": keys,
        })
    return sorted(out, key=lambda p: p["name"])

CYCLES_Q = """
query($team: String!, $after: String) {
  cycles(first: %d, after: $after, filter: {team: {key: {eq: $team}}}) {
    nodes { id number startsAt endsAt completedAt progress
            team { key } }
    pageInfo { hasNextPage endCursor }
  }
}
""" % PAGE_SIZE

def list_cycles(team):
    nodes = paginate(CYCLES_Q, {"team": team}, ["cycles"])
    return sorted(
        [{"id": c["id"], "number": c["number"], "starts_at": c["startsAt"],
          "ends_at": c["endsAt"], "completed_at": c.get("completedAt"),
          "progress": c.get("progress")} for c in nodes],
        key=lambda c: c["number"],
    )

ISSUES_Q = """
query($filter: IssueFilter, $after: String) {
  issues(first: %d, after: $after, filter: $filter, orderBy: updatedAt) {
    nodes {
      id identifier title url priority priorityLabel
      createdAt updatedAt completedAt dueDate estimate
      state { name type }
      assignee { name email }
      team { key name }
      project { name }
      cycle { number }
      parent { identifier title }
      labels { nodes { name } }
    }
    pageInfo { hasNextPage endCursor }
  }
}
""" % PAGE_SIZE

                                # Linear state type -> the Jira statusCategory
                                # name work_tree_link and the digests key off.
STATE_CATEGORY = {"completed": "Done", "canceled": "Done",
                  "started": "In Progress", "unstarted": "To Do",
                  "backlog": "To Do", "triage": "To Do"}

def flatten(issue):
    """Same field names as the Jira dump, so downstream code stays single-path.

    `type` is "Issue" here and gets promoted to "Epic" by the dump for any
    identifier that turns out to be some other issue's parent -- Linear has no
    epic issue type, a parent issue IS the epic.
    """
    state_type = (issue.get("state") or {}).get("type")
    return {
        "key": issue["identifier"],
        "url": issue.get("url") or issue_url(issue["identifier"]),
        "summary": issue.get("title"),
        "type": "Issue",
        "state_type": state_type,
        "category": STATE_CATEGORY.get(state_type, "To Do"),
        "status": (issue.get("state") or {}).get("name"),
        "assignee": (issue.get("assignee") or {}).get("name"),
        "team": (issue.get("team") or {}).get("key"),
        "project": (issue.get("project") or {}).get("name"),
        "cycle": (issue.get("cycle") or {}).get("number"),
        "parent": (issue.get("parent") or {}).get("identifier"),
        "parent_summary": (issue.get("parent") or {}).get("title"),
        "priority": issue.get("priorityLabel"),
        "labels": [l["name"] for l in (issue.get("labels") or {}).get("nodes", [])],
        "created": issue.get("createdAt"),
        "updated": issue.get("updatedAt"),
        "resolved": issue.get("completedAt"),
        "duedate": issue.get("dueDate"),
        "estimate": issue.get("estimate"),
        "source": "linear",
    }

def search_issues(team=None, query=None, state=None, assignee=None,
                  cycle=None, include_done=False, limit=None):
    f = {}
    if team:
        f["team"] = {"key": {"eq": team}}
    if state:
        f["state"] = {"name": {"eqIgnoreCase": state}}
    if assignee:
        f["assignee"] = {"name": {"containsIgnoreCase": assignee}}
    if query:
        f["title"] = {"containsIgnoreCase": query}
    if cycle == "active":
        f["cycle"] = {"isActive": {"eq": True}}
    elif cycle is not None:
        f["cycle"] = {"number": {"eq": int(cycle)}}
    if not include_done:
        f["state"] = {**f.get("state", {}), "type": {"nin": ["completed", "canceled"]}}

    issues = [flatten(i) for i in paginate(ISSUES_Q, {"filter": f}, ["issues"])]
    return issues[:limit] if limit else issues

ISSUE_Q = """
query($id: String!) {
  issue(id: $id) {
    id identifier title description url priorityLabel
    createdAt updatedAt completedAt dueDate estimate
    state { name type } assignee { name email } creator { name }
    team { key name } project { name } cycle { number }
    parent { identifier title } labels { nodes { name } }
    comments(first: 50) { nodes { body createdAt user { name } } }
  }
}
"""

def get_issue(key):
    issue = gql(ISSUE_Q, {"id": key})["issue"]
    if not issue:
        raise RuntimeError(f"No Linear issue {key} (or no access to its team).")
    out = flatten(issue)
    out["description"] = issue.get("description")
    out["creator"] = (issue.get("creator") or {}).get("name")
    out["comments"] = [
        {"author": (c.get("user") or {}).get("name"), "at": c["createdAt"], "body": c["body"]}
        for c in (issue.get("comments") or {}).get("nodes", [])
    ]
    return out

def cycle_status(team, stale_before=None):
    """Active-cycle snapshot, field-for-field the shape of jira_client sprint-status."""
    cycles = [c for c in list_cycles(team) if not c["completed_at"]]
    active = None
    for c in cycles:
        active = c
        break
    if not active:
        return {"team": team, "cycle": None, "note": "no open cycle on this team"}

    issues = search_issues(team=team, cycle=active["number"], include_done=True)
    done = [i for i in issues if i["state_type"] in ("completed", "canceled")]
    open_issues = [i for i in issues if i["state_type"] not in ("completed", "canceled")]

    by_status, by_assignee = {}, {}
    for i in issues:
        by_status[i["status"]] = by_status.get(i["status"], 0) + 1
    for i in open_issues:
        name = i["assignee"] or "Unassigned"
        by_assignee[name] = by_assignee.get(name, 0) + 1

    stale = []
    if stale_before:
        stale = [i for i in open_issues if (i["updated"] or "")[:10] < stale_before]

    return {
        "team": team,
        "cycle": {"number": active["number"], "starts_at": active["starts_at"],
                  "ends_at": active["ends_at"]},
        "total": len(issues),
        "done": len(done),
        "open": len(open_issues),
        "by_status": dict(sorted(by_status.items(), key=lambda x: -x[1])),
        "by_assignee": dict(sorted(by_assignee.items(), key=lambda x: -x[1])),
        "open_issues": open_issues,
        "stale": stale,
        "url": team_url(team),
    }

def daily_digest():
    """Markdown digest across every discovered team, with the concentration alert."""
    if not TEAMS:
        return "> [!NOTE]\n> `teams.json` not written yet. Run `linear_client.py teams --write` first."

    out = [f"### Linear -- {TEAMS.get('workspace', WORKSPACE)}", ""]
    for key in team_keys():
        info = TEAMS["teams"][key]
        try:
            st = cycle_status(key)
        except RuntimeError as exc:
            out.append(f"**{key} -- {info.get('name', key)}**: unavailable ({exc})")
            continue
        if not st.get("cycle"):
            out.append(f"**{key} -- {info.get('name', key)}**: no open cycle.")
            out.append("")
            continue

        c = st["cycle"]
        pct = round(100 * st["done"] / st["total"], 1) if st["total"] else 0.0
        out.append(f"**{key} -- {info.get('name', key)}** (Cycle {c['number']}, ends {(c['ends_at'] or '')[:10]})")
        out.append(f"- {st['done']}/{st['total']} done ({pct}%), {st['open']} open")

        # >40% of open work on one person is the bottleneck threshold the Jira
        # connector uses. Same number here so the two digests read the same.
        if st["open"]:
            for name, count in st["by_assignee"].items():
                share = count / st["open"]
                if share > 0.4 and name != "Unassigned":
                    out.append(f"> **Bottleneck**: {name} holds {count}/{st['open']} open "
                               f"({round(share * 100)}%) -- extreme risk for delivery delays.")
                break

        out.append("")
        out.append("| Assignee | Open | Statuses |")
        out.append("| :--- | ---: | :--- |")
        for name, count in st["by_assignee"].items():
            dist = {}
            for i in st["open_issues"]:
                if (i["assignee"] or "Unassigned") == name:
                    dist[i["status"]] = dist.get(i["status"], 0) + 1
            out.append(f"| **{name}** | {count} | " + ", ".join(f"{k}: {v}" for k, v in dist.items()) + " |")
        out.append("")
    return "\n".join(out)

# ---------------------------------------------------------------- discovery

def write_teams_json(portfolio_map=None):
    """Discovery: turn the live team list into the committed teams.json.

    Portfolios start empty on purpose. Filing a team under the wrong portfolio
    silently mixes boards across the owner's four portfolio boundaries, which the
    Jira SKILL.md explicitly forbids, so it is the owner's call, not a guess.
    """
    teams = list_teams()
    existing = load_teams() or {}
    prev_teams = existing.get("teams", {})
    prev_ports = existing.get("portfolios", {})

    payload = {
        "workspace": WORKSPACE,
        "teams": {
            t["key"]: {
                "id": t["id"],
                "name": t["name"],
                "members": t["members"],
                "portfolio": prev_teams.get(t["key"], {}).get("portfolio"),
            } for t in teams
        },
        "portfolios": prev_ports or {
            "marketplace": [], "platform": [], "b2c": [], "ecom-solution": [],
        },
    }
    with open(TEAMS_PATH, "w") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")

    collisions = [t["key"] for t in teams if t["collides_with_jira"]]
    return {"written": TEAMS_PATH, "teams": len(teams),
            "keys": [t["key"] for t in teams],
            "jira_key_collisions": collisions,
            "unassigned_portfolio": [k for k, v in payload["teams"].items() if not v["portfolio"]]}

# ---------------------------------------------------------------- write verbs

def _team_id(key):
    for t in list_teams():
        if t["key"] == key:
            return t["id"]
    raise RuntimeError(f"No Linear team with key {key}. Known: {[t['key'] for t in list_teams()]}")

def _user_id(name_or_email):
    users = paginate("""
        query($after: String) {
          users(first: %d, after: $after) { nodes { id name email }
                                            pageInfo { hasNextPage endCursor } }
        }
    """ % PAGE_SIZE, {}, ["users"])
    needle = name_or_email.lower()
    for u in users:
        if needle in (u["name"] or "").lower() or needle == (u["email"] or "").lower():
            return u["id"]
    raise RuntimeError(f"No Linear user matching '{name_or_email}'.")

def _state_id(team_key, state_name):
    states = paginate("""
        query($team: String!, $after: String) {
          workflowStates(first: %d, after: $after, filter: {team: {key: {eq: $team}}}) {
            nodes { id name type } pageInfo { hasNextPage endCursor }
          }
        }
    """ % PAGE_SIZE, {"team": team_key}, ["workflowStates"])
    for s in states:
        if s["name"].lower() == state_name.lower():
            return s["id"]
    raise RuntimeError(f"No state '{state_name}' on team {team_key}. Have: {[s['name'] for s in states]}")

def create_issue(team, title, description=None, assignee=None, project=None, approved=False):
    require_send_approval(f"create Linear issue in {team}", approved)
    payload = {"teamId": _team_id(team), "title": title}
    if description:
        payload["description"] = description
    if assignee:
        payload["assigneeId"] = _user_id(assignee)
    if project:
        for p in list_projects(team):
            if p["name"].lower() == project.lower():
                payload["projectId"] = p["id"]
                break
        else:
            raise RuntimeError(f"No project '{project}' on team {team}.")

    data = gql("""
        mutation($input: IssueCreateInput!) {
          issueCreate(input: $input) { success issue { identifier url title } }
        }
    """, {"input": payload})
    res = data["issueCreate"]
    if not res.get("success"):
        raise RuntimeError("Linear reported issueCreate success=false")
    return res["issue"]

def update_issue(key, state=None, assignee=None, title=None, approved=False):
    require_send_approval(f"update Linear issue {key}", approved)
    current = get_issue(key)
    payload = {}
    if title:
        payload["title"] = title
    if assignee:
        payload["assigneeId"] = _user_id(assignee)
    if state:
        payload["stateId"] = _state_id(current["team"], state)
    if not payload:
        raise RuntimeError("update-issue: nothing to change; pass --state, --assignee or --title.")

    data = gql("""
        mutation($id: String!, $input: IssueUpdateInput!) {
          issueUpdate(id: $id, input: $input) {
            success issue { identifier url title state { name } assignee { name } }
          }
        }
    """, {"id": key, "input": payload})
    res = data["issueUpdate"]
    if not res.get("success"):
        raise RuntimeError("Linear reported issueUpdate success=false")
    return res["issue"]

def comment(key, body, approved=False):
    require_send_approval(f"comment on Linear issue {key}", approved)
    data = gql("""
        mutation($input: CommentCreateInput!) {
          commentCreate(input: $input) { success comment { id url createdAt } }
        }
    """, {"input": {"issueId": key, "body": body}})
    res = data["commentCreate"]
    if not res.get("success"):
        raise RuntimeError("Linear reported commentCreate success=false")
    return res["comment"]

# ---------------------------------------------------------------- CLI

def _timeout_handler(signum, frame):
    sys.exit("linear_client: timed out after 180s")

def main():
    if os.name != 'nt':
        signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(180)

    action = sys.argv[1] if len(sys.argv) > 1 else "daily-digest"
    rest = sys.argv[2:]

    def parse(*specs):
        p = argparse.ArgumentParser(prog=f"linear_client.py {action}")
        for args, kwargs in specs:
            p.add_argument(*args, **kwargs)
        return p.parse_args(rest)

    def dump(obj):
        print(json.dumps(obj, indent=2, ensure_ascii=False))

    if action in ("verify-connection", "verify-connections"):
        dump(verify_connection())

    elif action == "teams":
        args = parse((["--write"], {"action": "store_true",
                                    "help": "write/refresh teams.json from the live workspace"}))
        if args.write:
            res = write_teams_json()
            dump(res)
            if res["jira_key_collisions"]:
                print(f"[WARN] Team keys collide with Jira project keys: {res['jira_key_collisions']}. "
                      "Refs must be source-qualified before work-tree linking.", file=sys.stderr)
            if res["unassigned_portfolio"]:
                print(f"[WARN] No portfolio set for: {res['unassigned_portfolio']}. "
                      "Fill teams.json portfolios before portfolio-scoped reads.", file=sys.stderr)
        else:
            dump(list_teams())

    elif action == "projects":
        args = parse((["--team"], {"default": None}))
        dump(list_projects(args.team))

    elif action == "cycles":
        args = parse((["--team"], {"required": True}))
        dump(list_cycles(args.team))

    elif action == "cycle-status":
        args = parse(
            (["--team"], {"default": None, "help": "team key; omit to use --portfolio"}),
            (["--portfolio"], {"default": None, "help": "portfolio name from teams.json"}),
            (["--stale-before"], {"default": None, "help": "YYYY-MM-DD; flag open issues not updated since"}),
        )
        if not args.team and not args.portfolio:
            sys.exit("cycle-status: pass --team or --portfolio")
        keys = [args.team] if args.team else portfolio_teams(args.portfolio)
        dump([cycle_status(k, args.stale_before) for k in keys])

    elif action == "issue":
        if not rest:
            sys.exit("issue: pass an identifier, e.g. ENG-123")
        dump(get_issue(rest[0]))

    elif action == "issues":
        args = parse(
            (["--team"], {"default": None}),
            (["--query"], {"default": None, "help": "substring match on title"}),
            (["--state"], {"default": None}),
            (["--assignee"], {"default": None}),
            (["--cycle"], {"default": None, "help": "cycle number, or 'active'"}),
            (["--include-done"], {"action": "store_true"}),
            (["--limit"], {"type": int, "default": None}),
        )
        dump(search_issues(args.team, args.query, args.state, args.assignee,
                           args.cycle, args.include_done, args.limit))

    elif action == "daily-digest":
        print(daily_digest())

    elif action == "create-issue":
        args = parse(
            (["--team"], {"required": True}),
            (["--title"], {"required": True}),
            (["--description"], {"default": None}),
            (["--assignee"], {"default": None}),
            (["--project"], {"default": None}),
            (["--approved"], {"action": "store_true",
                              "help": "Confirm the owner has explicitly approved this write"}),
        )
        issue = create_issue(args.team, args.title, args.description,
                             args.assignee, args.project, args.approved)
        print(f"Created: {issue['identifier']} -> {issue['url']}")

    elif action == "update-issue":
        if not rest or rest[0].startswith("-"):
            sys.exit("update-issue: pass an identifier first, e.g. update-issue ENG-123 --state Done")
        key, rest = rest[0], rest[1:]
        args = parse(
            (["--state"], {"default": None}),
            (["--assignee"], {"default": None}),
            (["--title"], {"default": None}),
            (["--approved"], {"action": "store_true"}),
        )
        issue = update_issue(key, args.state, args.assignee, args.title, args.approved)
        print(f"Updated: {issue['identifier']} -> {issue['url']}")

    elif action == "comment":
        if not rest or rest[0].startswith("-"):
            sys.exit("comment: pass an identifier first, e.g. comment ENG-123 --body '...'")
        key, rest = rest[0], rest[1:]
        args = parse(
            (["--body"], {"default": None}),
            (["--body-file"], {"default": None, "help": "read the comment from a file"}),
            (["--approved"], {"action": "store_true"}),
        )
        body = args.body
        if args.body_file:
            with open(args.body_file) as fh:
                body = fh.read()
        if not body:
            sys.exit("comment: pass --body or --body-file")
        res = comment(key, body, args.approved)
        print(f"Commented on {key} -> {res.get('url') or issue_url(key)}")

    else:
        print(f"Unknown action: {action}", file=sys.stderr)
        print(__doc__, file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
