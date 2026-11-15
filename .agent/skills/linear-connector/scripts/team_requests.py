#!/usr/bin/env python3
"""Team Requests intake: Slack form submissions -> Linear REQ team.

YourManager asked on 2 Nov 2026 for one "Engage with my team" door for requests to
the Project (PMO, Ines) and Growth (Teammate) teams, visible up to him. Two Slack
Workflow Builder forms post into each team's own request channel; this script turns each post into
a Linear issue in the REQ team's Triage, labelled Growth Team or Project Team,
and links the Slack message as a synced thread so Linear's own Slack bot posts
status changes and comments back into that thread. Requesters never need Linear.

Message contract (set in each workflow's "Send a message" step):
    first line   [REQ:growth] <title>    or    [REQ:project] <title>
    other lines  Label: value            (any labels; all go into the description)
    one line     Urgency: <...> or Priority: <...>   (mapped to Linear priority)
    one line     Requested by: <@U...>   (the "Person who submitted form" variable)

the owner approved this automation on 2 Nov 2026 (session c786669d): issue creation
from a form post is the approved flow, so `sync` needs no per-issue --approved.

PMO Intake row (Teammate asked on 5 Nov 2026): every Project Team request also
becomes a new row in the "PMO Intake Inbox" Sheet (tab Intake_Raw, columns A to
N). Teammate's own Apps Script takes it from there, including the scope doc, so
this job no longer creates a Scoping Note (that step ran 4 to 5 Nov 2026). A
failed append stays in state["intake_pending"] and retries on the next sync.
Uses the Work Drive token (you@).

Usage:
    python3 team_requests.py sync [--dry-run]      # poll channel, file new requests
    python3 team_requests.py parse <file>          # show what a message would become
    python3 team_requests.py status                # config + processed count
"""
import argparse
import json
import os
import re
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import linear_client as L  # noqa: E402

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(SKILL_DIR, "team_requests.json")
STATE_PATH = os.path.join(SKILL_DIR, "team_requests_state.json")
SLACK_TOKEN_ENV = os.path.join(SKILL_DIR, "..", "slack-connector", "token.env")

TAG_RE = re.compile(r"^\s*\[REQ:(growth|project)\]\s*(.*)$", re.I)
# Linear priority: 1 Urgent, 2 High, 3 Medium, 4 Low, 0 none.
PRIORITY_WORDS = [
    (1, ("urgent", "blocker", "churn", "immediate")),
    (2, ("high",)),
    (3, ("normal", "medium")),
    (4, ("low", "nice to have")),
]

def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return default

def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)

def slack_token():
    tok = os.environ.get("SLACK_USER_TOKEN")
    if not tok and os.path.exists(SLACK_TOKEN_ENV):
        for line in open(SLACK_TOKEN_ENV, encoding="utf-8"):
            if line.startswith("SLACK_USER_TOKEN="):
                tok = line.split("=", 1)[1].strip().strip('"')
    if not tok:
        sys.exit("team_requests: SLACK_USER_TOKEN not found")
    return tok

def slack(method, **params):
    r = requests.get(f"https://slack.com/api/{method}", params=params,
                     headers={"Authorization": f"Bearer {slack_token()}"}, timeout=30)
    body = r.json()
    if not body.get("ok"):
        raise RuntimeError(f"Slack {method}: {body.get('error')}")
    return body

def parse(text):
    """Return {team, title, priority, requester, body} or None if not a request."""
    lines = [ln.rstrip() for ln in (text or "").splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return None
    m = TAG_RE.match(lines[0].replace("*", ""))
    if not m:
        return None
    team, title = m.group(1).lower(), m.group(2).strip()
    priority, requester, fields = 0, None, {}
    for ln in lines[1:]:
        key, _, val = ln.replace("*", "").partition(":")
        key, low = key.strip().lower(), val.strip().lower()
        if key and key not in fields:
            fields[key] = val.strip()
        if key.startswith(("urgency", "priority", "requested priority")) and not priority:
            for level, words in PRIORITY_WORDS:
                if any(w in low for w in words):
                    priority = level
                    break
        if key.startswith("requested by") and not requester:
            u = re.search(r"<@(U[A-Z0-9]+)", val)
            requester = u.group(1) if u else val.strip()
    return {
        "team": team,
        "title": (title or "Untitled request")[:200],
        "priority": priority,
        "requester": requester,
        "body": "\n".join(lines[1:]).strip(),
        "fields": fields,
    }

def requester_name(uid):
    if not uid or not uid.startswith("U"):
        return uid or "unknown"
    try:
        p = slack("users.info", user=uid)["user"]
        return p.get("real_name") or p.get("name") or uid
    except Exception:
        return uid

def file_issue(cfg, req, permalink, dry_run=False):
    who = requester_name(req["requester"])
    desc = (f"**Requested by:** {who}\n**Slack submission:** {permalink}\n\n---\n\n"
            + req["body"].replace("<@", "@").replace(">", ""))
    payload = {
        "teamId": cfg["team_id"],
        "title": req["title"],
        "description": desc,
        "labelIds": [cfg["labels"][req["team"]]],
        "priority": req["priority"],
    }
    if dry_run:
        print(json.dumps(payload, indent=2)[:1500])
        return None
    issue = L.gql(
        "mutation($i:IssueCreateInput!){issueCreate(input:$i){issue{id identifier url}}}",
        {"i": payload})["issueCreate"]["issue"]
    # Synced thread: Linear's Slack bot mirrors status changes and comments
    # into the submission thread. Fall back to a plain link if the workspace
    # Slack integration refuses.
    try:
        L.gql("mutation($id:String!,$u:String!){attachmentLinkSlack(issueId:$id,url:$u,"
              "syncToCommentThread:true){success}}", {"id": issue["id"], "u": permalink})
        issue["synced"] = True
    except Exception as e:
        print(f"  slack sync link failed ({e}); adding plain link", file=sys.stderr)
        L.gql("mutation($id:String!,$u:String!){attachmentLinkURL(issueId:$id,url:$u,"
              "title:\"Slack submission\"){success}}", {"id": issue["id"], "u": permalink})
        issue["synced"] = False
    for uid in (cfg.get("urgent_subscribers") or []) if req["priority"] == 1 else []:
        try:
            L.gql("mutation($id:String!,$u:[String!]){issueUpdate(id:$id,input:{subscriberIds:$u}){success}}",
                  {"id": issue["id"], "u": [uid]})
        except Exception as e:
            print(f"  subscribe {uid} failed: {e}", file=sys.stderr)
    return issue

WORK_TOKEN = os.path.join(SKILL_DIR, "..", "work-drive-connector", "token.json")

def sheets_service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials.from_authorized_user_file(WORK_TOKEN)
    if not creds.valid:
        creds.refresh(Request())
    return build("sheets", "v4", credentials=creds, cache_discovery=False)

def intake_row(req, tz):
    """One Intake_Raw row, columns A to N (Submission Date .. Target Go-Live Date)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    f = req.get("fields") or {}
    def get(key):
        return (f.get(key) or "").replace("<@", "@").replace(">", "").strip()
    submitted = datetime.fromtimestamp(float(req.get("ts") or 0), ZoneInfo(tz))
    cr = get("change request").lower()
    return [
        submitted.strftime("%Y-%m-%d %H:%M:%S"),
        "@" + requester_name(req.get("requester")),
        req["title"],
        "Yes" if cr.startswith("yes") else "No",
        get("parent project"),
        get("project type"),
        get("client"),
        "",
        get("executive sponsor"),
        get("target market"),
        get("impacted product"),
        get("problem statement"),
        get("priority") or get("urgency"),
        get("target go-live date"),
    ]

def append_intake_row(cfg, req, dry_run=False):
    """Add the form data as a new row in the PMO Intake Inbox Sheet."""
    sh = cfg.get("intake_sheet") or {}
    if not sh.get("spreadsheet_id"):
        return None
    if dry_run:
        print("  intake row -> " + json.dumps(intake_row(req, "Asia/Dubai"))[:600])
        return None
    svc = sheets_service()
    tz = svc.spreadsheets().get(spreadsheetId=sh["spreadsheet_id"],
                                fields="properties.timeZone").execute()["properties"]["timeZone"]
    res = svc.spreadsheets().values().append(
        spreadsheetId=sh["spreadsheet_id"], range=f"'{sh['tab']}'!A:N",
        valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS",
        body={"values": [intake_row(req, tz)]}).execute()
    return res["updates"]["updatedRange"]

def run_intake(cfg, state, key, req, dry_run):
    try:
        rng = append_intake_row(cfg, req, dry_run)
    except Exception as e:
        print(f"  intake row failed ({str(e)[:200]}); will retry", file=sys.stderr)
        state.setdefault("intake_pending", {})[key] = {"req": req}
        return
    if rng:
        state.setdefault("intake", {})[key] = rng
        state.get("intake_pending", {}).pop(key, None)
        print(f"  intake row {rng}")

def cmd_sync(dry_run):
    cfg = load_json(CONFIG_PATH, {})
    # Each team owns its request channel: {"growth": "C...", "project": "C..."}.
    channels = {k: v for k, v in (cfg.get("channels") or {}).items() if v}
    if not channels:
        sys.exit("team_requests: no channels set in team_requests.json")
    state = load_json(STATE_PATH, {"oldest": {}, "done": {}})
    if not isinstance(state.get("oldest"), dict):
        state["oldest"] = {}
    filed = 0
    for team, chan in channels.items():
        oldest = state["oldest"].get(chan, "0")
        msgs = slack("conversations.history", channel=chan, oldest=oldest,
                     limit=100).get("messages", [])
        for msg in sorted(msgs, key=lambda m: float(m["ts"])):
            key = f"{chan}:{msg['ts']}"
            if key in state["done"]:
                continue
            req = parse(msg.get("text", ""))
            if not req:
                continue
            req["team"] = team  # the channel decides the team, whatever the tag says
            req["ts"] = msg["ts"]
            link = slack("chat.getPermalink", channel=chan, message_ts=msg["ts"])["permalink"]
            print(f"[{team}] {req['title']} (p{req['priority']}) {link}")
            issue = file_issue(cfg, req, link, dry_run)
            if issue:
                state["done"][key] = issue["identifier"]
                state["oldest"][chan] = max(oldest, msg["ts"], key=float)
                save_json(STATE_PATH, state)
                print(f"  -> {issue['identifier']} {issue['url']} synced={issue['synced']}")
                filed += 1
            if team == "project":
                run_intake(cfg, state, key, req, dry_run)
                if not dry_run:
                    save_json(STATE_PATH, state)
    for key, item in list((state.get("intake_pending") or {}).items()):
        if not dry_run:
            run_intake(cfg, state, key, item["req"], dry_run)
            save_json(STATE_PATH, state)
    print(f"team_requests: {filed} filed, {len(state['done'])} total")

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync")
    s.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("parse")
    p.add_argument("file")
    sub.add_parser("status")
    a = ap.parse_args()
    if a.cmd == "sync":
        cmd_sync(a.dry_run)
    elif a.cmd == "parse":
        print(json.dumps(parse(open(a.file, encoding="utf-8").read()), indent=2))
    else:
        st = load_json(STATE_PATH, {"done": {}})
        print(json.dumps({"config": load_json(CONFIG_PATH, {}), "filed": len(st["done"])}, indent=2))

if __name__ == "__main__":
    main()
