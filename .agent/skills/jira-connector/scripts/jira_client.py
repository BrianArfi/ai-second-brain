import os
import re
import sys
import json
import requests
from requests.auth import HTTPBasicAuth

# Force UTF-8 on Windows stdout/stderr to prevent encoding crashes
try:
    if sys.stdout.encoding != 'utf-8':
        sys.stdout.reconfigure(encoding='utf-8')
    if sys.stderr.encoding != 'utf-8':
        sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

# Credentials come from env vars or a token.env next to this skill
# (token.env is gitignored from the public template; see token.env.example).
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

EMAIL = os.environ.get("JIRA_EMAIL", "")
TOKEN = os.environ.get("JIRA_API_TOKEN", "")
if not EMAIL or not TOKEN:
    sys.exit("jira_client: set JIRA_EMAIL and JIRA_API_TOKEN (env or token.env)")
AUTH = HTTPBasicAuth(EMAIL, TOKEN)
HEADERS = {"Accept": "application/json"}

# 4 Work development boards map
BOARDS = {
    608: {
        "name": "E-commerce Core",
        "domain": "examplevendor.atlassian.net",
        "project_key": "MSP"
    },
    508: {
        "name": "B2C Super App",
        "domain": "examplevendor.atlassian.net",
        "project_key": "MBA"
    },
    674: {
        "name": "Storefront (Teammate)",
        "domain": "examplevendor.atlassian.net",
        "project_key": "STOR"
    },
    52: {
        "name": "Marketplace",
        "domain": "yourcompany.atlassian.net",
        "project_key": "MP"
    },
    76: {
        "name": "Platform Team",
        "domain": "yourcompany.atlassian.net",
        "project_key": "MPS"
    }
}

# Each board belongs to exactly ONE of the owner's four portfolios. This map is the
# mechanical portfolio boundary: a Marketplace sprint review reads board 52 only,
# never MPS (Platform) or MSP/STOR (E-Commerce Solution).
PORTFOLIO_BOARDS = {
    'marketplace': [52],
    'platform': [76],
    'b2c': [508],
    'ecom-solution': [608, 674],
}

def standardize_status(status_name):
    """Maps custom Jira status names to a standard set for clear reporting."""
    s = status_name.upper().strip()
    if s in ["DONE", "CLOSED", "RESOLVED", "COMPLETED"]:
        return "DONE"
    if s in ["READY FOR REVIEW", "REVIEW IN PROGRESS", "REVIEW", "READY FOR TESTING", "QA IN PROGRESS", "UNDER REVIEW", "QA"]:
        return "UNDER REVIEW"
    if s in ["IN PROGRESS", "IN DEVELOPMENT", "DOING", "IMPLEMENTATION"]:
        return "IN PROGRESS"
    return "TO DO"

def _active_sprint_via_jql(domain, project_key):
    """Active-sprint snapshot for a board the agile sprint sub-resource refuses.

    Team-managed boards report `type: simple` and answer
    /board/<id>/sprint with 400 "The board does not support sprints", even
    though the project still runs sprints and the `sprint` JQL function still
    resolves them. Broke the three ExampleVendor boards (MSP/MBA/STOR) on 19 Aug
    2026, which silently emptied the sprint table in the morning briefing.
    Read the issues by JQL instead and recover the sprint name/end date from
    the issues' own sprint field.
    """
    # The Sprint field is a custom field on the classic search API, so resolve
    # its id rather than asking for "sprint", which silently returns nothing.
    sprint_field = "customfield_10020"
    try:
        all_fields = requests.get(f"https://{domain}/rest/api/3/field",
                                  headers=HEADERS, auth=AUTH, timeout=20).json()
        for f in all_fields:
            if f.get("name", "").lower() == "sprint":
                sprint_field = f["id"]
                break
    except Exception:
        pass

    fields = f"summary,status,assignee,issuetype,parent,updated,{sprint_field}"
    issues, start_at, token = [], 0, None
    while True:
        params = {
            "jql": f"project = {project_key} AND sprint in openSprints()",
            "maxResults": 100,
            "fields": fields,
        }
        if token:
            params["nextPageToken"] = token
        r = requests.get(f"https://{domain}/rest/api/3/search/jql",
                         headers=HEADERS, auth=AUTH, params=params, timeout=30)
        if r.status_code != 200:
            return {"error": f"JQL fallback error {r.status_code}: {r.text[:200]}"}
        data = r.json()
        page = data.get("issues", [])
        issues.extend(page)
        token = data.get("nextPageToken")
        start_at += len(page)
        if not token or not page:
            break

    if not issues:
        return {"error": "No active sprints found"}

    # Every issue carries the sprint it sits in; take the newest active one.
    name, end_date = "Active sprint", "N/A"
    for issue in issues:
        value = issue.get("fields", {}).get(sprint_field) or []
        if isinstance(value, dict):
            value = [value]
        for sp in value:
            if isinstance(sp, dict) and sp.get("state") == "active":
                name = sp.get("name", name)
                end_date = (sp.get("endDate") or "N/A")[:10]
                break
        if name != "Active sprint":
            break

    return {"sprint_name": name, "end_date": end_date,
            "issues": issues, "total_count": len(issues)}

def fetch_board_active_sprint_and_issues(board_id, info):
    """Fetches details for active sprint and issues from a board."""
    domain = info["domain"]
    url = f"https://{domain}/rest/agile/1.0/board/{board_id}/sprint?state=active"

    try:
        resp = requests.get(url, headers=HEADERS, auth=AUTH, timeout=15)
        if resp.status_code == 404:
            return {"error": "Access Blocked / 404 Not Found (Permission Pending)"}
        if resp.status_code == 400 and "does not support sprints" in resp.text:
            return _active_sprint_via_jql(domain, info["project_key"])
        if resp.status_code != 200:
            return {"error": f"API Error {resp.status_code}: {resp.text[:200]}"}

        sprints = resp.json().get("values", [])
        if not sprints:
            return {"error": "No active sprints found"}
            
        sprint = sprints[0]
        sprint_id = sprint["id"]
        sprint_name = sprint["name"]
        end_date = sprint.get("endDate", "N/A")[:10]
        
        # Query issues for the active sprint. Paginate: a busy board runs past
        # the 100-issue page cap and a truncated board silently understates the
        # sprint. `updated` is required for the staleness check downstream.
        fields = "summary,status,assignee,issuetype,parent,updated"
        issues, start_at = [], 0
        while True:
            issues_url = (f"https://{domain}/rest/agile/1.0/sprint/{sprint_id}/issue"
                          f"?startAt={start_at}&maxResults=100&fields={fields}")
            issues_resp = requests.get(issues_url, headers=HEADERS, auth=AUTH, timeout=25)
            if issues_resp.status_code != 200:
                return {"error": f"Sprint Issues API Error {issues_resp.status_code}"}
            issues_data = issues_resp.json()
            page = issues_data.get("issues", [])
            issues.extend(page)
            start_at += len(page)
            if not page or start_at >= issues_data.get("total", 0):
                break

        return {
            "sprint_name": sprint_name,
            "end_date": end_date,
            "issues": issues,
            "total_count": len(issues)
        }
    except Exception as e:
        return {"error": f"Connection exception: {str(e)}"}

def sprint_status(portfolio, stale_before=None):
    """Active-sprint snapshot for ONE portfolio, as JSON-able data.

    Consumed by premeeting_cards.py so a sprint-review card carries real ticket
    status instead of only ledger items. Returns counts, the open (not-done)
    issues, assignee concentration, and issues untouched since `stale_before`
    (YYYY-MM-DD) so a reviewer can see what is parked rather than moving.
    """
    board_ids = PORTFOLIO_BOARDS.get(portfolio)
    if not board_ids:
        return {"error": f"unknown portfolio {portfolio!r}", "portfolio": portfolio}

    boards = []
    for bid in board_ids:
        info = BOARDS.get(bid)
        if not info:
            continue
        data = fetch_board_active_sprint_and_issues(bid, info)
        if data.get("error"):
            boards.append({"board_id": bid, "name": info["name"], "error": data["error"]})
            continue

        by_status, by_assignee, open_issues, stale = {}, {}, [], []
        done = 0
        for issue in data.get("issues", []):
            f = issue.get("fields", {})
            status = (f.get("status") or {}).get("name", "Unknown")
            std = standardize_status(status)
            by_status[status] = by_status.get(status, 0) + 1
            if std == "DONE":
                done += 1
                continue
            who = ((f.get("assignee") or {}).get("displayName")) or "UNASSIGNED"
            by_assignee[who] = by_assignee.get(who, 0) + 1
            updated = (f.get("updated") or "")[:10]
            row = {"key": issue.get("key"), "summary": f.get("summary", ""),
                   "status": status, "assignee": who, "updated": updated}
            open_issues.append(row)
            if stale_before and updated and updated < stale_before:
                stale.append(row)

        total = len(data.get("issues", []))
        boards.append({
            "board_id": bid,
            "name": info["name"],
            "project_key": info["project_key"],
            "domain": info["domain"],
            "sprint_name": data.get("sprint_name"),
            "end_date": data.get("end_date"),
            "total": total,
            "done": done,
            "open": len(open_issues),
            "by_status": by_status,
            "by_assignee": dict(sorted(by_assignee.items(), key=lambda kv: -kv[1])),
            "open_issues": open_issues,
            "stale": sorted(stale, key=lambda r: r["updated"]),
        })

    return {"portfolio": portfolio, "boards": boards}

def verify_all_connections():
    """Runs a quick pre-flight connectivity verification for all configured boards."""
    print("=== Jira Connector Connectivity Verification ===")
    for bid, info in BOARDS.items():
        domain = info["domain"]
        url = f"https://{domain}/rest/agile/1.0/board/{bid}"
        try:
            r = requests.get(url, headers=HEADERS, auth=AUTH, timeout=10)
            if r.status_code == 200:
                print(f"✅ Board #{bid} ({info['name']}) connected successfully! Board Name: {r.json().get('name')}")
            elif r.status_code == 404:
                print(f"❌ Board #{bid} ({info['name']}) returned 404 (Access Pending on domain {domain})")
            else:
                print(f"❌ Board #{bid} ({info['name']}) returned error {r.status_code}: {r.text[:150]}")
        except Exception as e:
            print(f"⚠️ Board #{bid} ({info['name']}) failed with exception: {e}")

def generate_daily_digest():
    """Queries all active boards, compiles data, analyzes workload imbalances, and returns Markdown."""
    output = []
    output.append("### 🏃 Sprint Progress & Allocation")
    
    has_active_sprints = False
    
    for bid, info in BOARDS.items():
        output.append(f"#### 📦 {info['name']} (Board #{bid})")
        
        sprint_data = fetch_board_active_sprint_and_issues(bid, info)
        
        if "error" in sprint_data:
            err = sprint_data["error"]
            if "404" in err:
                output.append(f"> [!NOTE]\n> **Status**: Board is currently inaccessible. Access/permissions are pending for your account.\n")
            else:
                output.append(f"> [!WARNING]\n> **Status**: Failed to harvest sprint data: {err}\n")
            continue
            
        has_active_sprints = True
        
        sprint_name = sprint_data["sprint_name"]
        end_date = sprint_data["end_date"]
        issues = sprint_data["issues"]
        total_count = sprint_data["total_count"]
        
        # Summary calculations
        status_counts = {"TO DO": 0, "IN PROGRESS": 0, "UNDER REVIEW": 0, "DONE": 0}
        assignee_counts = {}
        assignee_statuses = {}
        epics = {}
        
        for issue in issues:
            fields = issue.get("fields", {})
            status_obj = fields.get("status", {})
            status_name = status_obj.get("name", "Unknown")
            std_status = standardize_status(status_name)
            status_counts[std_status] += 1
            
            assignee = fields.get("assignee")
            assignee_name = assignee.get("displayName", "Unassigned") if assignee else "Unassigned"
            assignee_counts[assignee_name] = assignee_counts.get(assignee_name, 0) + 1
            
            if assignee_name not in assignee_statuses:
                assignee_statuses[assignee_name] = {}
            assignee_statuses[assignee_name][std_status] = assignee_statuses[assignee_name].get(std_status, 0) + 1
            
            # Epic categorization
            parent = fields.get("parent")
            if parent:
                parent_key = parent.get("key", "No Epic")
                parent_summary = parent.get("fields", {}).get("summary", "No Epic Summary")
                epic_display = f"{parent_key}: {parent_summary}"
            else:
                epic_display = "Independent Tasks / No Epic"
                
            if epic_display not in epics:
                epics[epic_display] = {"total": 0, "done": 0}
            epics[epic_display]["total"] += 1
            if std_status == "DONE":
                epics[epic_display]["done"] += 1

        # Calculate completion rate
        done_review_count = status_counts["DONE"] + status_counts["UNDER REVIEW"]
        completion_pct = (done_review_count / total_count * 100) if total_count > 0 else 0
        
        output.append(f"- **Active Sprint**: `{sprint_name}` (Target End: `{end_date}`)")
        output.append(f"- **Total Sprint Items**: `{total_count}` tickets")
        output.append(f"- **Sprint Status Summary**: `DONE`: {status_counts['DONE']} · `UNDER REVIEW`: {status_counts['UNDER REVIEW']} · `IN PROGRESS`: {status_counts['IN PROGRESS']} · `TO DO`: {status_counts['TO DO']}")
        output.append(f"- **Functional Completion Rate**: `{completion_pct:.1f}%` (Done + Under Review)")
        
        # Check Workload Imbalance (Teammate-type bottleneck guardrail: >40%)
        bottlenecks = []
        for name, count in assignee_counts.items():
            if name == "Unassigned":
                continue
            allocation_pct = (count / total_count * 100) if total_count > 0 else 0
            if allocation_pct > 40.0:
                bottlenecks.append(f"**{name}** holds **{count} out of {total_count} tickets ({allocation_pct:.1f}%)**")
                
        if bottlenecks:
            output.append("\n> [!WARNING]")
            output.append("> **Workload Imbalance / Resource Bottleneck Alert**:")
            for b in bottlenecks:
                output.append(f"> - {b} -- represents an extreme risk for delivery delays.")
                
        # Assignee Breakdown Table
        output.append("\n| Assignee | Active Tickets | Status Distribution |")
        output.append("| :--- | :--- | :--- |")
        for name, count in sorted(assignee_counts.items(), key=lambda x: x[1], reverse=True):
            dist = assignee_statuses.get(name, {})
            dist_str = ", ".join([f"{k}: {v}" for k, v in dist.items()])
            output.append(f"| **{name}** | {count} | {dist_str} |")
            
        # Epic summary
        output.append("\n**Epic Progress Summary**:")
        for epic, meta in sorted(epics.items(), key=lambda x: x[1]["total"], reverse=True)[:5]:
            output.append(f"- *{epic}*: `{meta['done']}/{meta['total']}` tickets complete")
            
        output.append("") # Spacer between boards
        
    if not has_active_sprints:
        output.append("> [!NOTE]\n> No active sprints are currently harvestable. Access is pending or sprints are not started.")
        
    return "\n".join(output)

_INLINE_RE = re.compile(
    r"(\*\*.+?\*\*)"      # bold
    r"|(`[^`]+?`)"        # inline code
    r"|(\[[^\]]+?\]\([^)]+?\))"   # link
    r"|(_[^_]+?_)"        # italic
)

def _adf_inline(text):
    """Convert a markdown inline run into ADF text nodes."""
    nodes = []

    def emit(raw, marks=None, link=None):
        if not raw:
            return
        node = {"type": "text", "text": raw}
        applied = list(marks or [])
        if link:
            applied.append({"type": "link", "attrs": {"href": link}})
        if applied:
            node["marks"] = applied
        nodes.append(node)

    pos = 0
    for m in _INLINE_RE.finditer(text):
        emit(text[pos:m.start()])
        bold, code, link, italic = m.groups()
        if bold:
            emit(bold[2:-2], [{"type": "strong"}])
        elif code:
            emit(code[1:-1], [{"type": "code"}])
        elif link:
            label, href = link[1:].split("](", 1)
            emit(label, link=href[:-1])
        elif italic:
            emit(italic[1:-1], [{"type": "em"}])
        pos = m.end()
    emit(text[pos:])
    return nodes or [{"type": "text", "text": " "}]

def markdown_to_adf(md):
    """Convert a small markdown subset to an ADF document.

    Supported: blank-line paragraphs, `* ` bullet lists with one level of
    two-space nesting, and the inline marks bold, italic, code and link.
    This is the subset the Work ticket house style uses; anything else
    passes through as plain paragraph text.
    """
    content = []
    lines = md.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if re.match(r"^\s*\* ", line):
            top = {"type": "bulletList", "content": []}
            while i < len(lines) and re.match(r"^\s*\* ", lines[i]):
                indent = len(lines[i]) - len(lines[i].lstrip())
                item_text = lines[i].lstrip()[2:].strip()
                item = {
                    "type": "listItem",
                    "content": [{"type": "paragraph", "content": _adf_inline(item_text)}],
                }
                if indent >= 2 and top["content"]:
                    parent = top["content"][-1]
                    nested = next(
                        (c for c in parent["content"] if c["type"] == "bulletList"), None
                    )
                    if nested is None:
                        nested = {"type": "bulletList", "content": []}
                        parent["content"].append(nested)
                    nested["content"].append(item)
                else:
                    top["content"].append(item)
                i += 1
            content.append(top)
            continue
        para = []
        while i < len(lines) and lines[i].strip() and not re.match(r"^\s*\* ", lines[i]):
            para.append(lines[i].strip())
            i += 1
        content.append({"type": "paragraph", "content": _adf_inline(" ".join(para))})
    if not content:
        content = [{"type": "paragraph", "content": [{"type": "text", "text": " "}]}]
    return {"type": "doc", "version": 1, "content": content}

def create_issue(project_key, summary, issue_type="Story", priority="High", description_text="", assignee_account_id=None, domain="examplevendor.atlassian.net", parent=None, labels=None, components=None, epic_link_field="customfield_10014"):
    """Create a Jira issue. Returns (key, url) on success or raises on error."""
    url = f"https://{domain}/rest/api/3/issue"
    auth = HTTPBasicAuth(EMAIL, TOKEN)

    fields = {
        "project": {"key": project_key},
        "summary": summary,
        "issuetype": {"name": issue_type},
        "priority": {"name": priority},
    }
    if description_text:
        fields["description"] = markdown_to_adf(description_text)
    if assignee_account_id:
        fields["assignee"] = {"accountId": assignee_account_id}
    if parent:
        # MP is company-managed: an Epic child needs BOTH the parent link and
        # the Epic Link custom field, otherwise the board renders it orphaned.
        fields["parent"] = {"key": parent}
        if epic_link_field:
            fields[epic_link_field] = parent
    if labels:
        fields["labels"] = list(labels)
    if components:
        fields["components"] = [{"name": c} for c in components]

    resp = requests.post(url, json={"fields": fields}, headers=HEADERS, auth=auth, timeout=15)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Create issue failed ({resp.status_code}): {resp.text[:600]}")
    key = resp.json()["key"]
    return key, f"https://{domain}/browse/{key}"

def main():
    if len(sys.argv) > 1:
        action = sys.argv[1]
    else:
        action = "daily-digest"

    if action == "verify-connections":
        verify_all_connections()
    elif action == "daily-digest":
        digest = generate_daily_digest()
        print(digest)
    elif action == "sprint-status":
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--portfolio", required=True,
                            choices=sorted(PORTFOLIO_BOARDS.keys()))
        parser.add_argument("--stale-before", default=None,
                            help="YYYY-MM-DD; flag open issues not updated since")
        args = parser.parse_args(sys.argv[2:])
        print(json.dumps(sprint_status(args.portfolio, args.stale_before), indent=2))
    elif action == "create-issue":
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--project", required=True)
        parser.add_argument("--summary", required=True)
        parser.add_argument("--type", default="Story")
        parser.add_argument("--priority", default="High")
        parser.add_argument("--description", default="")
        parser.add_argument("--description-file", default=None,
                            help="markdown file; avoids shell escaping on long tickets")
        parser.add_argument("--assignee", default=None)
        parser.add_argument("--domain", default="examplevendor.atlassian.net")
        parser.add_argument("--parent", default=None, help="epic or parent issue key")
        parser.add_argument("--label", action="append", default=[])
        parser.add_argument("--component", action="append", default=[])
        parser.add_argument("--dry-run", action="store_true",
                            help="print the fields payload and exit without creating")
        parser.add_argument("--approved", action="store_true",
                            help="required to actually create; a Jira write is team-facing")
        args = parser.parse_args(sys.argv[2:])
        description = args.description
        if args.description_file:
            with open(args.description_file, encoding="utf-8") as fh:
                description = fh.read()
        if args.dry_run:
            print(json.dumps(markdown_to_adf(description), indent=2))
            sys.exit(0)
        if not args.approved:
            sys.exit("create-issue: refusing to create without --approved "
                     "(the owner must sign off on the specific ticket). Use --dry-run to preview.")
        key, url = create_issue(args.project, args.summary, args.type, args.priority,
                                description, args.assignee, args.domain,
                                parent=args.parent, labels=args.label, components=args.component)
        print(f"Created: {key} -> {url}")
    else:
        print(f"Unknown action: {action}")
        sys.exit(1)

if __name__ == "__main__":
    main()
