"""Dump every issue across Work's Linear teams into one JSON file.

Sibling of the Jira dump, and deliberately the SAME top-level shape --
`{TEAM_KEY: {"domain", "count", "issues": [...]}}` -- so `work_tree_link.py`
merges the two dumps into one dict and the node-assignment pass downstream
stays single-path instead of growing a Linear branch.

    python3 .agent/skills/linear-connector/scripts/dump_all_issues.py \
        --out _temp/linear_all_issues.json [--include-done]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from linear_client import (  # noqa: E402
    ISSUES_Q, WORKSPACE, flatten, list_teams, paginate,
)

DOMAIN = f"linear.app/{WORKSPACE}"

def dump(include_done=False):
    result = {}
    for team in sorted(list_teams(), key=lambda t: t["key"]):
        f = {"team": {"key": {"eq": team["key"]}}}
        if not include_done:
            f["state"] = {"type": {"nin": ["completed", "canceled"]}}
        issues = [flatten(i) for i in paginate(ISSUES_Q, {"filter": f}, ["issues"])]

        # Linear has no epic issue type: a parent issue IS the epic. Anything
        # another issue in this dump points at gets promoted, which is what
        # work_tree_link keys off when it collects a node's epic refs.
        parents = {i["parent"] for i in issues if i.get("parent")}
        for i in issues:
            if i["key"] in parents:
                i["type"] = "Epic"

        result[team["key"]] = {
            "domain": DOMAIN,
            "source": "linear",
            "team_name": team["name"],
            "count": len(issues),
            "issues": issues,
        }
        print(f"{team['key']}: {len(issues)} issues", file=sys.stderr)
    return result

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="_temp/linear_all_issues.json")
    ap.add_argument("--include-done", action="store_true",
                    help="also pull completed/canceled issues (much larger)")
    args = ap.parse_args()

    result = dump(args.include_done)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump(result, fh, indent=1)
    print(f"wrote {args.out}")

if __name__ == "__main__":
    main()
