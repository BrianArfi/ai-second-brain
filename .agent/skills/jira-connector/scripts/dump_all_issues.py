"""Dump every issue across the five Work/ExampleVendor boards into one JSON file.

Feeds the work-tree linking pass: the work tree needs to know, per project, what
tickets exist, what epic they hang off, and where they are, so a node can carry
its real ticket list instead of a hand-typed handful of keys.

    python3 .agent/skills/jira-connector/scripts/dump_all_issues.py \
        --out _temp/jira_all_issues.json [--include-done]
"""
import argparse
import json
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jira_client import AUTH, HEADERS, BOARDS  # noqa: E402

# project_key -> domain, derived from the board map so there is one source of truth
PROJECTS = {}
for _info in BOARDS.values():
    PROJECTS.setdefault(_info["project_key"], _info["domain"])

FIELDS = [
    "summary", "status", "assignee", "issuetype", "parent", "labels",
    "components", "priority", "updated", "created", "resolutiondate",
    "fixVersions", "duedate",
]

def search(domain, jql, fields):
    """Page through the enhanced JQL search endpoint."""
    url = f"https://{domain}/rest/api/3/search/jql"
    out, token = [], None
    while True:
        payload = {"jql": jql, "maxResults": 100, "fields": fields}
        if token:
            payload["nextPageToken"] = token
        resp = requests.post(url, json=payload, headers={**HEADERS, "Content-Type": "application/json"},
                             auth=AUTH, timeout=60)
        if resp.status_code != 200:
            raise RuntimeError(f"{domain} search failed ({resp.status_code}): {resp.text[:300]}")
        data = resp.json()
        out.extend(data.get("issues", []))
        token = data.get("nextPageToken")
        if data.get("isLast") or not token:
            break
    return out

def flatten(issue, domain):
    f = issue.get("fields", {}) or {}
    parent = f.get("parent") or {}
    return {
        "key": issue["key"],
        "url": f"https://{domain}/browse/{issue['key']}",
        "summary": f.get("summary"),
        "type": (f.get("issuetype") or {}).get("name"),
        "status": (f.get("status") or {}).get("name"),
        "category": ((f.get("status") or {}).get("statusCategory") or {}).get("name"),
        "assignee": (f.get("assignee") or {}).get("displayName"),
        "priority": (f.get("priority") or {}).get("name"),
        "parent": parent.get("key"),
        "parent_summary": (parent.get("fields") or {}).get("summary"),
        "labels": f.get("labels") or [],
        "components": [c.get("name") for c in (f.get("components") or [])],
        "fix_versions": [v.get("name") for v in (f.get("fixVersions") or [])],
        "duedate": f.get("duedate"),
        "created": f.get("created"),
        "updated": f.get("updated"),
        "resolved": f.get("resolutiondate"),
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="_temp/jira_all_issues.json")
    ap.add_argument("--include-done", action="store_true",
                    help="also pull resolved issues (much larger)")
    args = ap.parse_args()

    result = {}
    for key, domain in sorted(PROJECTS.items()):
        jql = f"project = {key}"
        if not args.include_done:
            jql += " AND statusCategory != Done"
        jql += " ORDER BY created ASC"
        issues = [flatten(i, domain) for i in search(domain, jql, FIELDS)]
        result[key] = {"domain": domain, "count": len(issues), "issues": issues}
        print(f"{key}: {len(issues)} issues", file=sys.stderr)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump(result, fh, indent=1)
    print(f"wrote {args.out}")

if __name__ == "__main__":
    main()
