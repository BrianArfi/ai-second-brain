"""Import the OPEN issues of a Jira archive into one Linear team (own importer).

Why this exists: Linear's issueImportCreateJira + issueImportProcess route finished
with 0 issues (its mapping object is undocumented). This script reads the archive
written by the Jira archiver (issues.jsonl plus files/) and writes through the
public GraphQL API.

    python3 jira_archive_import.py --project MSP --team ZZT \
        --archive /path/to/MSP --state /path/to/state.json --dry-run
    python3 jira_archive_import.py ... --limit 5 --approved
    python3 jira_archive_import.py ... --approved

Rules (from Clients/Work/linear_jira_status_mapping.json and the Linear handbook):
- Only issues whose Jira status category is not done.
- Open epics become projects "[TEST] Ecom Solutions: <summary>" (name prefix is set with
  --project-prefix) together with ALL their children, closed ones too (rule of 18 Oct 2026,
  --open-epics-all-children, default on). Skipped only when empty: no child, no description,
  no comment. Closed children get the mapped done state (Done; Won't Do -> Canceled), completedAt
  = Jira resolution date, no assignee (no notification; footer says who). Epics are not issues.
- Sub-tasks become sub-issues (parents are created first).
- Status by the mapping file (state of the same name in the target team); QA FAILED
  also gets the Flag label "QA failed". Every issue gets the Flag label "Jira import".
- Assignee matched by email, then by exact display name; else unassigned and the
  footer says who it was.
- Comments keep order, author and Jira date. Files are uploaded to Linear storage so
  nothing links back to Jira attachments.
- Jira issue links become Linear relations when both ends are imported, else a footer line.
- Idempotent: the state file maps Jira key -> Linear id; a rerun skips finished items.
- Writes only to the one team given in --team. Every create call carries that team id.
"""
import argparse
import datetime as dt
import json
import mimetypes
import os
import re
import sys
import time

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.abspath(os.path.join(SKILL_DIR, "..", "..", ".."))
API = "https://api.linear.app/graphql"
JIRA_HOST = "https://examplevendor.atlassian.net"
MAPPING_PATH = os.path.join(REPO_ROOT, "Clients", "Work", "linear_jira_status_mapping.json")

TYPE_LABEL = {"Story": "Feature", "Task": "Task", "Bug": "Bug", "Improvement": "Improvement",
              "Subtask": "Task", "Sub-task": "Task", "Spike": "Spike"}
AREA_LABEL = {"frontend": "Web", "fe": "Web", "web": "Web", "backend": "Backend", "be": "Backend",
              "mobile": "Mobile", "design": "Design", "devops": "DevOps", "data": "Data"}
PRIORITY = {"Highest": 2, "High": 2, "Medium": 3, "Low": 4, "Lowest": 4}
FIB = [1, 2, 3, 5, 8]
IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")

def load_key():
    for p in (os.path.join(SKILL_DIR, "token.env"),):
        if os.path.exists(p):
            for line in open(p, encoding="utf-8"):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"'))
    key = os.environ.get("LINEAR_API_KEY")
    if not key:
        sys.exit("LINEAR_API_KEY missing (env or .agent/skills/linear-connector/token.env)")
    return key

class Linear:
    def __init__(self, key):
        self.h = {"Authorization": key, "Content-Type": "application/json"}
        self.calls = 0

    def gql(self, q, v=None):
        for attempt in range(8):
            try:
                r = requests.post(API, headers=self.h, json={"query": q, "variables": v or {}}, timeout=90)
            except requests.RequestException as e:
                time.sleep(2 * (attempt + 1))
                err = str(e)
                continue
            self.calls += 1
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(min(60, 3 * (attempt + 1) ** 2))
                err = "http %s" % r.status_code
                continue
            try:
                d = r.json()
            except ValueError:
                err = "bad json http %s" % r.status_code
                time.sleep(3)
                continue
            if d.get("errors"):
                msg = json.dumps(d["errors"])
                if "RATELIMITED" in msg or "ratelimit" in msg.lower():
                    time.sleep(min(90, 10 * (attempt + 1)))
                    err = "ratelimited"
                    continue
                raise RuntimeError(msg[:600])
            return d["data"]
        raise RuntimeError("gql failed after retries: %s" % err)

# ---------------------------------------------------------------- ADF -> markdown
def adf_md(node, ctx):
    """ctx: {'img': fn(attrs)->markdown or None}"""
    t = node.get("type")
    c = node.get("content") or []
    a = node.get("attrs") or {}
    if t == "doc":
        return blocks(c, ctx).strip()
    if t == "text":
        s = node.get("text", "")
        marks = {m["type"]: m.get("attrs") or {} for m in node.get("marks", [])}
        if "code" in marks:
            s = "`%s`" % s
        if "strong" in marks and s.strip():
            s = "**%s**" % s.strip() + (" " if s.endswith(" ") else "")
        if "em" in marks and s.strip():
            s = "*%s*" % s.strip() + (" " if s.endswith(" ") else "")
        if "link" in marks:
            s = "[%s](%s)" % (s, marks["link"].get("href", ""))
        return s
    if t == "paragraph":
        return inline(c, ctx)
    if t == "heading":
        return "#" * int(a.get("level", 2)) + " " + inline(c, ctx)
    if t in ("bulletList", "orderedList"):
        out = []
        for i, li in enumerate(c):
            bullet = "%d. " % (i + 1) if t == "orderedList" else "- "
            body = blocks(li.get("content") or [], ctx).strip("\n")
            lines = body.split("\n")
            out.append(bullet + lines[0])
            pad = " " * len(bullet)
            out.extend((pad + l) if l.strip() else l for l in lines[1:])
        return "\n".join(out)
    if t == "taskList":
        out = []
        for li in c:
            done = (li.get("attrs") or {}).get("state") == "DONE"
            out.append("- [%s] %s" % ("x" if done else " ", inline(li.get("content") or [], ctx)))
        return "\n".join(out)
    if t == "codeBlock":
        return "```%s\n%s\n```" % (a.get("language", ""), "".join(x.get("text", "") for x in c))
    if t == "blockquote":
        return "\n".join("> " + l for l in blocks(c, ctx).split("\n"))
    if t == "panel":
        return "\n".join("> " + l for l in blocks(c, ctx).split("\n"))
    if t == "rule":
        return "---"
    if t == "table":
        rows = []
        for r in c:
            cells = []
            for cell in r.get("content") or []:
                txt = blocks(cell.get("content") or [], ctx).strip().replace("\n", " ").replace("|", "\\|")
                cells.append(txt)
            rows.append(cells)
        if not rows:
            return ""
        w = max(len(r) for r in rows)
        rows = [r + [""] * (w - len(r)) for r in rows]
        out = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * w) + " |"]
        out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
        return "\n".join(out)
    if t in ("mediaSingle", "mediaGroup"):
        return "\n\n".join(x for x in (adf_md(m, ctx) for m in c) if x)
    if t in ("media", "mediaInline"):
        return ctx["img"](a) or ""
    if t in ("expand", "nestedExpand"):
        return ("**%s**\n\n" % a.get("title", "")) + blocks(c, ctx)
    if t in ("blockCard", "inlineCard"):
        return a.get("url", "")
    if t == "mention":
        return a.get("text") or "@user"
    if t == "emoji":
        return a.get("text") or a.get("shortName", "")
    if t == "status":
        return "[%s]" % a.get("text", "")
    if t == "date":
        try:
            return dt.datetime.fromtimestamp(int(a["timestamp"]) / 1000, dt.timezone.utc).strftime("%Y-%m-%d")
        except Exception:
            return ""
    if t == "hardBreak":
        return "  \n"
    return blocks(c, ctx) if c else (node.get("text") or "")

def inline(nodes, ctx):
    return "".join(adf_md(n, ctx) for n in nodes)

def blocks(nodes, ctx):
    return "\n\n".join(x for x in (adf_md(n, ctx) for n in nodes) if x != "")

def jdate(s):
    # Jira: 2025-12-03T16:30:56.151+0000
    d = dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%f%z")
    return d.astimezone(dt.timezone.utc)

def iso(s):
    return jdate(s).strftime("%Y-%m-%dT%H:%M:%S.000Z")

def human(s):
    return jdate(s).strftime("%Y-%m-%d %H:%M UTC")

def nearest_fib(x):
    return min(FIB, key=lambda f: (abs(f - x), f))

# ---------------------------------------------------------------- importer
class Importer:
    def __init__(self, a):
        self.a = a
        self.dry = a.dry_run
        self.state_path = a.state
        self.state = {"issues": {}, "projects": {}, "comments": {}, "files": {}, "attachments": {},
                      "relations": {}, "project_comments": {}, "errors": [], "skipped": {}}
        if os.path.exists(a.state):
            self.state.update(json.load(open(a.state, encoding="utf-8")))
        self.rows = [json.loads(l) for l in open(os.path.join(a.archive, "issues.jsonl"), encoding="utf-8")]
        self.by = {r["key"]: r for r in self.rows}
        self.L = None if self.dry and a.offline else Linear(load_key())
        self.stats = {}

    def save(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.state_path)), exist_ok=True)
        tmp = self.state_path + ".tmp"
        json.dump(self.state, open(tmp, "w", encoding="utf-8"), indent=1)
        os.replace(tmp, self.state_path)

    def bump(self, k, n=1):
        self.stats[k] = self.stats.get(k, 0) + n

    # ---- lookups
    def lookups(self):
        L = self.L
        d = L.gql('{teams(first:50){nodes{id key name}}}')
        team = [t for t in d["teams"]["nodes"] if t["key"] == self.a.team]
        if not team:
            sys.exit("team %s not found" % self.a.team)
        self.team = team[0]
        st = L.gql('query($id:String!){team(id:$id){states{nodes{id name}}}}', {"id": self.team["id"]})
        self.states = {s["name"].lower(): s["id"] for s in st["team"]["states"]["nodes"]}
        lab = {}
        after = None
        while True:
            d = L.gql('query($a:String){issueLabels(first:100,after:$a){nodes{id name team{key}} pageInfo{hasNextPage endCursor}}}', {"a": after})
            for n in d["issueLabels"]["nodes"]:
                if n["team"] is None or n["team"]["key"] == self.a.team:
                    lab[n["name"].lower()] = n["id"]
            if not d["issueLabels"]["pageInfo"]["hasNextPage"]:
                break
            after = d["issueLabels"]["pageInfo"]["endCursor"]
        self.labels = lab
        u = L.gql('{users(first:250){nodes{id name email active}}}')["users"]["nodes"]
        self.users_email = {x["email"].lower(): x["id"] for x in u if x["active"]}
        self.users_name = {x["name"].lower(): x["id"] for x in u if x["active"] and "@linear.app" not in x["email"]}
        m = json.load(open(MAPPING_PATH, encoding="utf-8"))[self.a.project]
        self.status_map = {x["jira_status"].upper(): x for x in m["mappings"]}
        self.team_id = self.team["id"]

    def state_id(self, jira_status):
        m = self.status_map.get(jira_status.upper())
        if not m:
            return None, None
        sid = self.states.get(m["linear_state"].lower())
        return sid, m.get("add_label")

    # ---- selection
    def select(self):
        rows = self.rows
        op = [r for r in rows if r["fields"]["status"]["statusCategory"]["key"] != "done"]
        epics = {r["key"] for r in rows if r["fields"]["issuetype"]["name"] == "Epic"}
        self.open_all = op
        self.open_epics = [r for r in op if r["key"] in epics]
        children = {}
        for r in op:
            p = r["fields"].get("parent")
            if p and p["key"] in epics:
                children.setdefault(p["key"], []).append(r["key"])
        self.open_children = {k: list(v) for k, v in children.items()}
        extra = []
        if self.a.open_epics_all_children:
            # the owner, 18 Oct 2026: an open epic is a project with ALL its children (closed ones
            # too) and their sub-tasks. It is skipped only when truly empty: no child, no
            # description, no comment.
            open_epic_keys = {r["key"] for r in self.open_epics}
            if self.a.closed_children_of:
                # scope the closed-children pass to some epics (epics with open children stay projects)
                open_epic_keys &= set(self.a.closed_children_of.split(","))
                open_epic_keys |= set(self.open_children)
            allkids = {}
            for r in rows:
                p = r["fields"].get("parent")
                if p and p["key"] in open_epic_keys:
                    allkids.setdefault(p["key"], []).append(r["key"])
            children = {k: list(v) for k, v in allkids.items()}
            self.epic_children = children
            self.project_epics = []
            self.skipped_epics = []
            for r in self.open_epics:
                if r["key"] in self.open_children:
                    has = True
                elif self.a.closed_children_of and r["key"] not in open_epic_keys:
                    has = False
                else:
                    has = bool(r["key"] in children or r["fields"].get("description") or r.get("comments_all"))
                if has:
                    self.project_epics.append(r)
                else:
                    self.skipped_epics.append(r["key"])
            included = {k for v in children.values() for k in v}
            if self.a.closed_children_of:
                # only the scoped epics add closed children
                scoped = set(self.a.closed_children_of.split(","))
                included = {k for e in scoped for k in children.get(e, [])}
            grew = True
            while grew:  # sub-tasks of included issues
                grew = False
                for r in rows:
                    p = r["fields"].get("parent")
                    if p and p["key"] in included and r["key"] not in included and r["key"] not in epics:
                        included.add(r["key"])
                        grew = True
            extra = [r for r in rows if r["key"] in included and r["fields"]["status"]["statusCategory"]["key"] == "done"
                     and r["key"] not in epics]
        else:
            self.epic_children = children
            self.project_epics = [r for r in self.open_epics if r["key"] in children]
            self.skipped_epics = [r["key"] for r in self.open_epics if r["key"] not in children]
        iss = [r for r in op if r["key"] not in epics] + extra
        opkeys = {r["key"] for r in iss}
        self.issue_keys = opkeys
        # parents first
        top = [r for r in iss if not (r["fields"].get("parent") and r["fields"]["parent"]["key"] in opkeys)]
        sub = [r for r in iss if r["fields"].get("parent") and r["fields"]["parent"]["key"] in opkeys]
        keynum = lambda r: int(r["key"].split("-")[1])
        order = sorted(top, key=keynum) + sorted(sub, key=keynum)
        if self.a.keys:
            want = set(self.a.keys.split(","))
            order = [r for r in order if r["key"] in want]
            # pull in parents
            have = {r["key"] for r in order}
            for r in list(order):
                p = r["fields"].get("parent")
                if p and p["key"] in opkeys and p["key"] not in have:
                    order.insert(0, self.by[p["key"]])
                    have.add(p["key"])
        if self.a.limit:
            order = order[: self.a.limit]
        self.order = order

    # ---- files
    def files_of(self, r):
        out = []
        for f in r.get("archived_files") or []:
            if f.get("saved") and not f.get("error"):
                out.append(f)
        return out

    def upload(self, f):
        path = os.path.join(self.a.archive, f["saved"])
        if path in self.state["files"]:
            return self.state["files"][path]
        if self.dry:
            return "dry://" + f["filename"]
        data = open(path, "rb").read()
        ct = mimetypes.guess_type(f["filename"])[0] or "application/octet-stream"
        d = self.L.gql('mutation($ct:String!,$fn:String!,$s:Int!){fileUpload(contentType:$ct,filename:$fn,size:$s,makePublic:false){success uploadFile{uploadUrl assetUrl headers{key value}}}}',
                       {"ct": ct, "fn": f["filename"], "s": len(data)})["fileUpload"]
        uf = d["uploadFile"]
        h = {x["key"]: x["value"] for x in uf["headers"]}
        h["Content-Type"] = ct
        h["Cache-Control"] = "public, max-age=31536000"
        r = requests.put(uf["uploadUrl"], data=data, headers=h, timeout=300)
        if r.status_code >= 300:
            raise RuntimeError("upload put %s %s" % (r.status_code, r.text[:200]))
        self.state["files"][path] = uf["assetUrl"]
        self.save()
        self.bump("files_uploaded")
        return uf["assetUrl"]

    # ---- text builders
    def ctx_for(self, r, used):
        files = self.files_of(r)

        def img(attrs):
            alt = attrs.get("alt")
            mid = attrs.get("id", "")
            hit = None
            for f in files:
                if (alt and f["filename"] == alt) or (mid and mid in f["filename"]):
                    hit = f
                    break
            if not hit:
                return "_(attachment %s)_" % (alt or mid)
            used.add(hit["saved"])
            url = self.upload(hit)
            if hit["filename"].lower().endswith(IMG_EXT):
                return "![%s](%s)" % (hit["filename"], url)
            return "[%s](%s)" % (hit["filename"], url)
        return {"img": img}

    def md_of(self, adf, r, used):
        if not adf:
            return ""
        return adf_md(adf, self.ctx_for(r, used))

    def footer(self, r, assignee_note, extra_labels):
        f = r["fields"]
        key = r["key"]
        lines = ["---", "Imported from Jira %s (%s/browse/%s)" % (key, JIRA_HOST, key)]
        rep = (f.get("reporter") or {}).get("displayName", "unknown")
        lines.append("Created %s by %s. Jira status: %s." % (human(f["created"]), rep, f["status"]["name"]))
        if assignee_note:
            lines.append(assignee_note)
        sp = f.get("customfield_10020")
        if sp:
            lines.append("Jira sprints: " + ", ".join(s.get("name", "?") for s in sp))
        if f.get("labels"):
            lines.append("Jira labels: " + ", ".join(f["labels"]))
        if extra_labels:
            lines.append(extra_labels)
        dev = r.get("development") or {}
        devl = dev_lines(dev)
        if devl:
            lines.append("Development:")
            lines += ["- " + x for x in devl]
        links = []
        for l in f.get("issuelinks") or []:
            if "outwardIssue" in l:
                other, rel = l["outwardIssue"], l["type"]["outward"]
            else:
                other, rel = l["inwardIssue"], l["type"]["inward"]
            ok = other["key"] in self.issue_keys
            links.append("- %s %s (%s)%s" % (rel, other["key"],
                                                 other["fields"]["summary"], "" if ok else " [not imported]"))
        for rl in r.get("remote_links") or []:
            o = rl.get("object") or {}
            links.append("- %s: %s" % (o.get("title", "link"), o.get("url", "")))
        if links:
            lines.append("Links:")
            lines += links
        return "\n".join(lines)

    # ---- run
    def run(self):
        self.select()
        if not self.dry or not self.a.offline:
            self.lookups()
        else:
            self.team_id = "dry"
        t0 = time.time()
        self.plan_counts()
        if self.dry:
            return
        self.ensure_projects()
        for r in self.order:
            try:
                self.do_issue(r)
            except Exception as e:  # keep going, record
                self.state["errors"].append({"key": r["key"], "error": str(e)[:400]})
                self.save()
                print("ERROR", r["key"], str(e)[:200])
        self.do_relations()
        self.do_project_comments()
        self.save()
        print("done in %.0fs; linear calls %d; stats %s" % (time.time() - t0, self.L.calls, self.stats))
        split = self.state.get("needs_split") or {}
        if split:
            print("needs split (open, more than one Area; squad lead splits into one sub-issue per area):")
            for k, a in sorted(split.items()):
                print("  %s -> %s: %s" % (k, (self.state["issues"].get(k) or {}).get("identifier", "?"), ", ".join(a)))

    def plan_counts(self):
        o = self.order
        sub = [r for r in o if r["fields"].get("parent") and r["fields"]["parent"]["key"] in self.issue_keys]
        need_proj = {r["fields"]["parent"]["key"] for r in o if r["fields"].get("parent") and r["fields"]["parent"]["key"] in self.epic_children}
        from collections import Counter
        st = Counter(r["fields"]["status"]["name"] for r in o)
        com = sum(len(r["comments_all"]) for r in o)
        ecom = sum(len(r["comments_all"]) for r in self.project_epics)
        nf = sum(len(self.files_of(r)) for r in o)
        fi = sum(1 for r in o if self.files_of(r))
        li = sum(1 for r in o if r["fields"].get("issuelinks"))
        unm = Counter()
        mt = Counter()
        for r in o:
            a = r["fields"].get("assignee")
            if a:
                (mt if self.match_user(a) else unm)[a["displayName"]] += 1
        print("open total (incl epics):", len(self.open_all))
        print("open epics:", len(self.open_epics), "-> projects (with open child):", len(self.project_epics),
              "; childless epics not imported:", len(self.skipped_epics))
        print("issues to create in this run:", len(o), "(sub-issues %d)" % len(sub))
        print("projects needed in this run:", len(need_proj) if self.a.limit or self.a.keys else len(self.project_epics))
        print("statuses:", dict(st))
        print("comments on issues:", com, "; comments on project epics:", ecom)
        print("files:", nf, "on", fi, "issues; issues with links:", li)
        print("assignee matched:", dict(mt), "unmatched:", dict(unm))

    def match_user(self, a):
        em = (a.get("emailAddress") or "").lower()
        if em and em in self.users_email:
            return self.users_email[em]
        return self.users_name.get((a.get("displayName") or "").lower())

    def ensure_projects(self):
        need = set()
        for r in self.order:
            p = r["fields"].get("parent")
            if p and p["key"] in self.epic_children:
                need.add(p["key"])
        for e in self.project_epics:
            whole = not (self.a.limit or self.a.keys)
            if (e["key"] not in need and not whole) or e["key"] in self.state["projects"]:
                continue
            f = e["fields"]
            used = set()
            body = self.md_of(f.get("description"), e, used)
            content = (body + "\n\n" if body else "") + self.footer(e, None, None)
            name = "%sEcom Solutions: %s" % (self.a.project_prefix, f["summary"])
            d = self.L.gql('mutation($i:ProjectCreateInput!){projectCreate(input:$i){success project{id url name}}}',
                           {"i": {"name": (name if len(name) <= 80 else name[:77] + "..."), "teamIds": [self.team_id], "content": content[:60000],
                                  "description": ("Imported from Jira %s" % e["key"])[:250]}})
            p = d["projectCreate"]["project"]
            self.state["projects"][e["key"]] = {"id": p["id"], "url": p["url"], "name": p["name"]}
            self.save()
            self.bump("projects")
            print("project", e["key"], p["url"])

    def do_issue(self, r):
        key = r["key"]
        st = self.state["issues"].get(key)
        f = r["fields"]
        if not (st and st.get("id")):
            used = set()
            desc = self.md_of(f.get("description"), r, used)
            files = self.files_of(r)
            extra_imgs = []
            for fl in files:
                if fl["saved"] in used:
                    continue
                url = self.upload(fl)
                if fl["filename"].lower().endswith(IMG_EXT):
                    extra_imgs.append("![%s](%s)" % (fl["filename"], url))
            if extra_imgs:
                desc += ("\n\n" if desc else "") + "\n\n".join(extra_imgs)
            a = f.get("assignee")
            closed = f["status"]["statusCategory"]["key"] == "done"
            # closed issues are created unassigned: Linear has no way to skip the assignee
            # notification, so the footer names the Jira assignee instead
            uid = self.match_user(a) if (a and not closed) else None
            note = None
            if a and not uid:
                note = "Jira assignee: %s" % a["displayName"]
            sid, flag = self.state_id(f["status"]["name"])
            if closed and not sid:
                res = ((f.get("resolution") or {}).get("name") or f["status"]["name"]).lower()
                sid = self.states.get("canceled" if ("won" in res and "do" in res) or "cancel" in res else "done")
            labels = []
            tl = TYPE_LABEL.get(f["issuetype"]["name"])
            # Flags are plain workspace labels (no group since 18 Oct 2026), so they combine
            for nm in (tl, flag, "Jira import"):
                if nm and self.labels.get(nm.lower()):
                    labels.append(self.labels[nm.lower()])
            extra = []
            areas = []
            for jl in f.get("labels") or []:
                an = AREA_LABEL.get(jl.lower())
                if an and self.labels.get(an.lower()) and an not in areas:
                    areas.append(an)
            # the Area group is exclusive: one area label only, the rest stays in the footer.
            # An open issue with more areas goes on the needs-split list (LINEAR.md: one sub-issue per area)
            if areas:
                labels.append(self.labels[areas[0].lower()])
            if len(areas) > 1 and not closed:
                self.state.setdefault("needs_split", {})[r["key"]] = areas
            inp = {"teamId": self.team_id, "title": f["summary"][:250],
                   "description": (desc + "\n\n" if desc else "") + self.footer(r, note, None),
                   "labelIds": sorted(set(labels)), "createdAt": iso(f["created"])}
            if sid:
                inp["stateId"] = sid
            if closed and f.get("resolutiondate"):
                inp["completedAt"] = iso(f["resolutiondate"])
            pr = PRIORITY.get((f.get("priority") or {}).get("name"))
            if pr:
                inp["priority"] = pr
            sp = f.get("customfield_10016")
            if sp:
                inp["estimate"] = nearest_fib(float(sp))
            if f.get("duedate"):
                inp["dueDate"] = f["duedate"]
            if uid:
                inp["assigneeId"] = uid
            p = f.get("parent")
            if p:
                if p["key"] in self.state["issues"]:
                    inp["parentId"] = self.state["issues"][p["key"]]["id"]
                    pe = self.by.get(p["key"])
                    pp = (pe["fields"].get("parent") if pe else None)
                    if pp and pp["key"] in self.state["projects"]:
                        inp["projectId"] = self.state["projects"][pp["key"]]["id"]
                elif p["key"] in self.state["projects"]:
                    inp["projectId"] = self.state["projects"][p["key"]]["id"]
            d = self.L.gql('mutation($i:IssueCreateInput!){issueCreate(input:$i){success issue{id identifier url team{key}}}}', {"i": inp})
            iss = d["issueCreate"]["issue"]
            if iss["team"]["key"] != self.a.team:
                raise RuntimeError("SAFETY: issue landed in team %s" % iss["team"]["key"])
            st = {"id": iss["id"], "identifier": iss["identifier"], "url": iss["url"],
                  "assignee_matched": bool(uid), "state": f["status"]["name"]}
            self.state["issues"][key] = st
            self.save()
            self.bump("issues")
            if p and p["key"] in self.issue_keys:
                self.bump("sub_issues")
            print(key, "->", iss["identifier"])
        # comments
        for c in r["comments_all"]:
            cid = "%s:%s" % (key, c["id"])
            if cid in self.state["comments"]:
                continue
            used = set()
            body = self.md_of(c.get("body"), r, used)
            txt = "**%s** (%s):\n\n%s" % (c["author"]["displayName"], human(c["created"]), body or "_(empty comment)_")
            d = self.L.gql('mutation($i:CommentCreateInput!){commentCreate(input:$i){success comment{id}}}',
                           {"i": {"issueId": st["id"], "body": txt[:60000], "createdAt": iso(c["created"]),
                                  "doNotSubscribeToIssue": True}})
            self.state["comments"][cid] = d["commentCreate"]["comment"]["id"]
            self.save()
            self.bump("comments")
        # non-image attachments
        for fl in self.files_of(r):
            if fl["filename"].lower().endswith(IMG_EXT):
                continue
            ak = "%s:%s" % (key, fl["id"])
            if ak in self.state["attachments"]:
                continue
            url = self.upload(fl)
            d = self.L.gql('mutation($i:AttachmentCreateInput!){attachmentCreate(input:$i){success attachment{id}}}',
                           {"i": {"issueId": st["id"], "title": fl["filename"], "url": url,
                                  "subtitle": "%d bytes, from Jira %s" % (fl.get("bytes") or 0, key)}})
            self.state["attachments"][ak] = d["attachmentCreate"]["attachment"]["id"]
            self.save()
            self.bump("attachments")

    def do_relations(self):
        done = set(self.state["issues"])
        for r in self.order:
            for l in r["fields"].get("issuelinks") or []:
                if "outwardIssue" not in l:
                    continue  # each link appears on both ends; use the outward side once
                a, b = r["key"], l["outwardIssue"]["key"]
                if a not in done or b not in done:
                    continue
                rk = "%s>%s:%s" % (a, b, l["id"])
                if rk in self.state["relations"]:
                    continue
                typ = "blocks" if l["type"]["name"] == "Blocks" else "related"
                try:
                    d = self.L.gql('mutation($i:IssueRelationCreateInput!){issueRelationCreate(input:$i){success issueRelation{id}}}',
                                   {"i": {"issueId": self.state["issues"][a]["id"], "relatedIssueId": self.state["issues"][b]["id"], "type": typ}})
                    self.state["relations"][rk] = d["issueRelationCreate"]["issueRelation"]["id"]
                    self.bump("relations")
                except Exception as e:
                    self.state["errors"].append({"key": rk, "error": str(e)[:300]})
                self.save()

    def do_project_comments(self):
        for ek, p in self.state["projects"].items():
            e = self.by[ek]
            for c in e["comments_all"]:
                cid = "%s:%s" % (ek, c["id"])
                if cid in self.state["project_comments"]:
                    continue
                used = set()
                body = self.md_of(c.get("body"), e, used)
                txt = "**%s** (%s):\n\n%s" % (c["author"]["displayName"], human(c["created"]), body or "_(empty comment)_")
                try:
                    d = self.L.gql('mutation($i:CommentCreateInput!){commentCreate(input:$i){success comment{id}}}',
                                   {"i": {"projectId": p["id"], "body": txt[:60000], "createdAt": iso(c["created"])}})
                    self.state["project_comments"][cid] = d["commentCreate"]["comment"]["id"]
                    self.bump("project_comments")
                except Exception as ex:
                    self.state["errors"].append({"key": cid, "error": str(ex)[:300]})
                self.save()

def dev_lines(dev):
    """Best effort: PR / branch lines from a Jira dev-status payload; empty when none."""
    out = []
    if not dev:
        return out
    detail = dev.get("detail") or []
    for d in detail:
        for pr in d.get("pullRequests") or []:
            out.append("PR %s: %s (%s)" % (pr.get("name", ""), pr.get("url", ""), pr.get("status", "")))
        for b in d.get("branches") or []:
            out.append("Branch %s: %s" % (b.get("name", ""), b.get("url", "")))
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--team", required=True)
    ap.add_argument("--archive", required=True)
    ap.add_argument("--state", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--keys", default="", help="comma list of Jira keys (parents are added)")
    ap.add_argument("--open-epics-all-children", dest="open_epics_all_children", action="store_true", default=True,
                    help="open epic -> project with ALL children incl. closed (default on)")
    ap.add_argument("--no-open-epics-all-children", dest="open_epics_all_children", action="store_false")
    ap.add_argument("--closed-children-of", default="", help="comma list of epic keys; limits the closed-children pass to them")
    ap.add_argument("--project-prefix", default="[TEST] ")
    ap.add_argument("--approved", action="store_true", help="required for any write")
    ap.add_argument("--offline", action="store_true", help="dry run without calling Linear")
    a = ap.parse_args()
    if not a.dry_run and not a.approved:
        sys.exit("refusing to write without --approved")
    imp = Importer(a)
    imp.run()

if __name__ == "__main__":
    main()
