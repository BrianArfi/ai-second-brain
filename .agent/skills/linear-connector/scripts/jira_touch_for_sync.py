"""Touch OPEN Jira issues once so Linear's Jira sync picks them up.

Linear's bi-directional Jira sync only takes an existing Jira issue after the issue gets one update in
Jira. This script adds and removes a throwaway label `linear-sync` on each open issue (two PUTs,
notifyUsers=false), so the webhook fires and nothing stays behind.

    python3 jira_touch_for_sync.py --project MSP --limit 5                 # dry run (default): prints the plan
    python3 jira_touch_for_sync.py --project MSP --keys ABC-123 --approved   # really writes
    python3 jira_touch_for_sync.py --project MSP --verify --team ZZT         # which touched keys have no Linear issue yet

Rules: dry run is the default; any write needs --approved. About 3 issues per second, retry on 429.
State (resumable): _temp/linmig/touch_<PROJECT>.json. A touched key is skipped on a rerun.
"""
import argparse
import json
import os
import sys
import time

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, ".agent", "skills", "jira-connector", "scripts"))
DOMAIN = "examplevendor.atlassian.net"
LABEL = "linear-sync"
INTERVAL = 0.34  # about 3 issues per second

def jira_call(method, path, **kw):
    from jira_client import HEADERS, AUTH
    for attempt in range(8):
        r = requests.request(method, "https://%s%s" % (DOMAIN, path), headers=HEADERS,
                             auth=AUTH, timeout=60, **kw)
        if r.status_code == 429:
            time.sleep(float(r.headers.get("Retry-After") or min(60, 3 * (attempt + 1))))
            continue
        if r.status_code >= 500:
            time.sleep(2 * (attempt + 1))
            continue
        return r
    raise RuntimeError("Jira %s %s failed after retries" % (method, path))

def open_issues(project):
    out, token = [], None
    jql = "project = %s AND statusCategory != Done ORDER BY key ASC" % project
    while True:
        params = {"jql": jql, "maxResults": 100, "fields": "summary,status"}
        if token:
            params["nextPageToken"] = token
        r = jira_call("GET", "/rest/api/3/search/jql", params=params)
        if r.status_code != 200:
            sys.exit("Jira search failed %s: %s" % (r.status_code, r.text[:200]))
        d = r.json()
        out.extend(i["key"] for i in d.get("issues", []))
        token = d.get("nextPageToken")
        if not token or not d.get("issues"):
            return out

def touch(key):
    for body in ({"update": {"labels": [{"add": LABEL}]}}, {"update": {"labels": [{"remove": LABEL}]}}):
        r = jira_call("PUT", "/rest/api/3/issue/%s" % key, params={"notifyUsers": "false"}, json=body)
        if r.status_code not in (200, 204):
            raise RuntimeError("PUT %s %s: %s" % (key, r.status_code, r.text[:200]))
        time.sleep(0.15)

def linear_synced(team):
    """Jira key -> Linear identifier for every issue in the team that carries a Jira sync attachment."""
    sys.path.insert(0, HERE)
    import linear_client as lc
    q = ('query($after:String){attachments(first:50,after:$after,filter:{url:{startsWith:"https://%s/browse/"}})'
         '{nodes{url sourceType issue{identifier team{key}}} pageInfo{hasNextPage endCursor}}}' % DOMAIN)
    out = {}
    for n in lc.paginate(q, {}, ["attachments"]):
        i = n.get("issue")
        if i and i["team"]["key"] == team and n.get("sourceType") == "jira":
            out[n["url"].rsplit("/browse/", 1)[1].split("?")[0].strip("/")] = i["identifier"]
    return out

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--keys", default="", help="comma list of Jira keys (overrides the JQL list)")
    ap.add_argument("--dry-run", dest="dry_run", action="store_true", default=True, help="default: print only")
    ap.add_argument("--approved", action="store_true", help="required to write to Jira")
    ap.add_argument("--verify", action="store_true", help="check touched keys have a synced Linear issue")
    ap.add_argument("--team", default="", help="Linear team key for --verify")
    ap.add_argument("--state", default="")
    a = ap.parse_args()

    state_path = a.state or os.path.join(REPO_ROOT, "_temp", "linmig", "touch_%s.json" % a.project)
    state = {"project": a.project, "touched": {}, "errors": {}}
    if os.path.exists(state_path):
        state.update(json.load(open(state_path, encoding="utf-8")))

    def save():
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        json.dump(state, open(state_path + ".tmp", "w", encoding="utf-8"), indent=1)
        os.replace(state_path + ".tmp", state_path)

    if a.verify:
        if not a.team:
            sys.exit("--verify needs --team")
        keys = [k for k in a.keys.split(",") if k] or sorted(state["touched"], key=lambda k: int(k.split("-")[1]))
        syn = linear_synced(a.team)
        missing = [k for k in keys if k not in syn]
        print("verify: touched %d, synced in %s %d, missing %d" % (len(keys), a.team, len(keys) - len(missing), len(missing)))
        for k in missing:
            print("  missing:", k)
        return

    keys = [k for k in a.keys.split(",") if k] or open_issues(a.project)
    if a.limit:
        keys = keys[: a.limit]
    todo = [k for k in keys if k not in state["touched"]]
    print("project %s: open issues selected %d, already touched %d, to touch %d" % (a.project, len(keys), len(keys) - len(todo), len(todo)))
    if not a.approved:
        print("DRY RUN (no --approved): would add then remove label '%s' with notifyUsers=false on:" % LABEL)
        for k in todo:
            print("  would touch", k)
        if a.limit == 0 and len(todo) > 20:
            print("  (%d keys)" % len(todo))
        return
    ok = err = 0
    for k in todo:
        t = time.time()
        try:
            touch(k)
            state["touched"][k] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            state["errors"].pop(k, None)
            ok += 1
        except Exception as e:
            state["errors"][k] = str(e)[:300]
            err += 1
            print("ERROR", k, str(e)[:150])
        save()
        time.sleep(max(0, INTERVAL - (time.time() - t)))
    print("touched %d, errors %d, total touched in state %d" % (ok, err, len(state["touched"])))

if __name__ == "__main__":
    main()
