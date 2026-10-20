"""Full archive of Work's Jira boards before the move to Linear (archive #1 and #2).

Keeps what the Linear importer drops: closed tickets, full history, every
comment with its author, PR/branch links (Development field), and every file.

    python3 .agent/skills/jira-connector/scripts/archive_all.py \
        --out ~/jira_archive_2026-10-16 [--projects AFM,MSP] [--no-files]
        [--since 2026-10-16]   # archive #2: only issues updated since that date

Layout:  <out>/<PROJECT>/issues.jsonl   one full issue per line
         <out>/<PROJECT>/files/<KEY>/<attachment id>_<filename>
         <out>/manifest.json            counts per project, compared with the audit
Resumable: a finished project has <out>/<PROJECT>/DONE and is skipped; a file
already on disk with the right size is not downloaded again.
"""
import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jira_client import AUTH, HEADERS  # noqa: E402

DOMAIN = "examplevendor.atlassian.net"
PROJECTS = ["AFM", "MSP", "STOR", "MBA", "TRAV", "SP", "ML", "MIM", "RP"]
# Live audit, 15 Oct 2026 (Linear_Migration_No_Data_Loss_Plan section 2)
AUDIT = {"AFM": 345, "MSP": 501, "STOR": 194, "MBA": 430, "TRAV": 1355,
         "SP": 287, "ML": 409, "MIM": 331, "RP": 612}

session = requests.Session()
session.auth = AUTH
session.headers.update(HEADERS)

def call(method, url, **kw):
    """One request with retry on 429 and 5xx."""
    for attempt in range(6):
        try:
            r = session.request(method, url, timeout=kw.pop("timeout", 60), **kw)
        except requests.RequestException:
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(int(r.headers.get("Retry-After", 2 ** attempt)))
            continue
        return r
    raise RuntimeError(f"{method} {url} failed after retries")

def get_json(path, params=None):
    r = call("GET", f"https://{DOMAIN}{path}", params=params)
    if r.status_code != 200:
        return {"_error": r.status_code, "_body": r.text[:300]}
    return r.json()

def search_keys(project, since=None):
    jql = f"project = {project}" + (f" AND updated >= '{since}'" if since else "") + " ORDER BY key ASC"
    out, token = [], None
    while True:
        payload = {"jql": jql, "maxResults": 100, "fields": ["summary"]}
        if token:
            payload["nextPageToken"] = token
        r = call("POST", f"https://{DOMAIN}/rest/api/3/search/jql", json=payload,
                 headers={"Content-Type": "application/json"})
        if r.status_code != 200:
            raise RuntimeError(f"{project} search failed ({r.status_code}): {r.text[:300]}")
        data = r.json()
        out.extend((i["id"], i["key"]) for i in data.get("issues", []))
        token = data.get("nextPageToken")
        if data.get("isLast") or not token:
            return out

def paged(path, key):
    """Collect every page of a startAt/maxResults list endpoint."""
    items, start = [], 0
    while True:
        data = get_json(path, {"startAt": start, "maxResults": 100})
        if "_error" in data:
            return items, data
        page = data.get(key) or data.get("values") or []
        items.extend(page)
        start += len(page)
        if not page or start >= data.get("total", 0):
            return items, None

def dev_links(issue_id):
    """PRs and branches from the Development panel, per connected tool."""
    summary = get_json("/rest/dev-status/latest/issue/summary", {"issueId": issue_id})
    if "_error" in summary:
        return {"_error": summary["_error"]}
    out = {}
    for data_type in ("pullrequest", "branch", "repository"):
        part = (summary.get("summary") or {}).get(data_type) or {}
        if not (part.get("overall") or {}).get("count"):
            continue
        for app in (part.get("byInstanceType") or {}).keys():
            d = get_json("/rest/dev-status/latest/issue/detail",
                         {"issueId": issue_id, "applicationType": app, "dataType": data_type})
            out.setdefault(data_type, []).extend(d.get("detail", []) if "_error" not in d else [d])
    return out

def safe(name):
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name)[:150]

def download(att, folder):
    path = os.path.join(folder, f"{att['id']}_{safe(att.get('filename', 'file'))}")
    size = att.get("size")
    if os.path.exists(path) and (size is None or os.path.getsize(path) == size):
        return path, os.path.getsize(path), None
    r = call("GET", att["content"], stream=True, timeout=300)
    if r.status_code != 200:
        return None, 0, r.status_code
    os.makedirs(folder, exist_ok=True)
    with open(path + ".part", "wb") as fh:
        for chunk in r.iter_content(1 << 20):
            fh.write(chunk)
    os.replace(path + ".part", path)
    return path, os.path.getsize(path), None

def archive_issue(issue_id, key, pdir, with_files):
    issue = get_json(f"/rest/api/3/issue/{key}", {"fields": "*all", "expand": "renderedFields,names"})
    if "_error" in issue:
        return {"key": key, "_error": issue}
    f = issue.get("fields") or {}
    issue["comments_all"], err_c = paged(f"/rest/api/3/issue/{key}/comment", "comments")
    issue["changelog_all"], err_h = paged(f"/rest/api/3/issue/{key}/changelog", "values")
    issue["remote_links"] = get_json(f"/rest/api/3/issue/{key}/remotelink")
    issue["development"] = dev_links(issue_id)
    files = []
    if with_files:
        for att in f.get("attachment") or []:
            path, size, err = download(att, os.path.join(pdir, "files", key))
            files.append({"id": att["id"], "filename": att.get("filename"), "jira_size": att.get("size"),
                          "saved": os.path.relpath(path, pdir) if path else None, "bytes": size, "error": err})
    issue["archived_files"] = files
    issue["_errors"] = [e for e in (err_c, err_h) if e]
    return issue

def archive_project(project, out, with_files, since, workers):
    pdir = os.path.join(out, project)
    if os.path.exists(os.path.join(pdir, "DONE")):
        return json.load(open(os.path.join(pdir, "DONE")))
    os.makedirs(pdir, exist_ok=True)
    keys = search_keys(project, since)
    print(f"[{project}] {len(keys)} issues", flush=True)
    stats = {"issues": 0, "errors": 0, "comments": 0, "history": 0, "files": 0, "file_bytes": 0,
             "file_errors": 0, "with_dev_links": 0}
    with open(os.path.join(pdir, "issues.jsonl"), "w", encoding="utf-8") as fh, \
            ThreadPoolExecutor(workers) as pool:
        futures = [pool.submit(archive_issue, i, k, pdir, with_files) for i, k in keys]
        for n, fut in enumerate(futures, 1):
            issue = fut.result()
            fh.write(json.dumps(issue, ensure_ascii=False) + "\n")
            if "_error" in issue:
                stats["errors"] += 1
                continue
            stats["issues"] += 1
            stats["comments"] += len(issue["comments_all"])
            stats["history"] += len(issue["changelog_all"])
            dev = issue["development"]
            if dev and "_error" not in dev:
                stats["with_dev_links"] += 1
            for a in issue["archived_files"]:
                stats["files"] += 1 if a["saved"] else 0
                stats["file_errors"] += 1 if a["error"] else 0
                stats["file_bytes"] += a["bytes"]
            if n % 100 == 0:
                print(f"[{project}] {n}/{len(keys)}", flush=True)
    stats["expected"] = len(keys)
    json.dump(stats, open(os.path.join(pdir, "DONE"), "w"))
    return stats

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--projects", default=",".join(PROJECTS))
    ap.add_argument("--no-files", action="store_true")
    ap.add_argument("--since", help="only issues updated on or after YYYY-MM-DD (archive #2)")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    manifest = {"domain": DOMAIN, "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "since": a.since, "projects": {}}
    for p in a.projects.split(","):
        s = archive_project(p, a.out, not a.no_files, a.since, a.workers)
        s["audit_15_oct"] = AUDIT.get(p)
        manifest["projects"][p] = s
        json.dump(manifest, open(os.path.join(a.out, "manifest.json"), "w"), indent=1)
        print(f"[{p}] done: {s}", flush=True)
    manifest["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    tot = {k: sum(v.get(k, 0) for v in manifest["projects"].values())
           for k in ("issues", "errors", "comments", "files", "file_bytes", "file_errors", "with_dev_links")}
    manifest["totals"] = tot
    json.dump(manifest, open(os.path.join(a.out, "manifest.json"), "w"), indent=1)
    print("TOTALS", json.dumps(tot), flush=True)

if __name__ == "__main__":
    main()
