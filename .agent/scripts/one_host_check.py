#!/usr/bin/env python3
"""One-host dry-run checker: is WSL the only writer, and what stands in the way?

READ-ONLY. It reads git state and one config file, prints a report, and changes nothing:
no commit, no push, no pull, no checkout, no write to journal/state, no edit of
workspace.json. (`--fetch` is the one opt-in: it refreshes remote-tracking refs so the
ahead/behind numbers are current. It never touches a working tree.)

Context. CLAUDE.md names WSL the only automation host and `origin/main` the source of
truth, yet the ASB desktop app keeps its workspace on the Windows checkout, so two
machines write the same `journal/state/*.json`. Grok Bot lessons plan, B5, 15 Oct 2026.
the owner decided on 15 Oct 2026 not to move hosts; this script stays as a read-only health check.

It answers three questions:

  1. Which checkouts hold commits that origin/main has not seen (or lag behind it)?
  2. Which machine last wrote each `journal/state/*.json`? Git records an identity, not a
     hostname, so the machine is inferred from the identity (see IDENTITY_LABELS) and from
     which checkout holds the commit. Override with --identity email=label.
  3. Does the ASB app's active workspace point at a non-canonical checkout? ASB_WORKSPACE
     wins over workspace.json, as in the app (src-tauri/src/workspace.rs).

Usage:
  python3 .agent/scripts/one_host_check.py              # text report
  python3 .agent/scripts/one_host_check.py --json       # machine-readable
  python3 .agent/scripts/one_host_check.py --days 14    # widen the "recent writers" window
  python3 .agent/scripts/one_host_check.py --strict     # exit 1 when any finding is open
  python3 .agent/scripts/one_host_check.py --checkout /some/other/clone

Stdlib only. Works on Windows, WSL and macOS; a checkout that is not reachable from the
machine running it is reported as unreachable, not guessed.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

WSL_CANONICAL = "."
MAC_CHECKOUT = "."
WSL_DISTRO = "Ubuntu"
STATE_GLOB = "journal/state/*.json"

# Git records who, not where. These two identities are what the three checkouts commit as
# today (checked against `git log` on 15 Oct 2026): the Windows checkout is configured as
# "Your Name", the WSL and macOS checkouts as "you". The WSL/macOS pair cannot be
# told apart by identity, so a state file last written by that identity is "WSL or macOS"
# and the per-checkout report says which of them is ahead.
IDENTITY_LABELS = {
    "you@example.com": "windows",
    "you@example.com": "wsl-or-macos",
    "agent@claude.ai": "claude-agent",
    "noreply@anthropic.com": "claude-agent",
}
# Labels that count as the canonical writer. Anything else that wrote state in the window
# is a finding. A macOS checkout is a first-class interactive checkout (CLAUDE.md) that does
# not run cron, so "wsl-or-macos" is accepted and the macOS question is answered by
# checking that checkout's own ahead/behind.
CANONICAL_LABELS = {"wsl-or-macos", "claude-agent"}

GIT_TIMEOUT = 90

# --------------------------------------------------------------------------- paths

def is_windows() -> bool:
    return os.name == "nt"

def classify_root(root: str) -> str:
    """windows-native | wsl-unc | wsl-native | macos | linux-other | unknown."""
    r = (root or "").strip()
    low = r.lower().replace("/", "\\")
    if low.startswith("\\\\wsl.localhost\\") or low.startswith("\\\\wsl$\\"):
        return "wsl-unc"
    if re.match(r"^[a-z]:\\", low):
        return "windows-native"
    if r.startswith("/mnt/") and len(r) > 6 and r[5].isalpha() and r[6:7] == "/":
        return "windows-native"          # a Windows drive seen from inside WSL
    if r.startswith("/Users/"):
        return "macos"
    if r.startswith("/home/") or r.startswith("/root"):
        return "wsl-native"
    return "unknown"

def is_canonical_root(root: str) -> bool:
    kind = classify_root(root)
    if kind not in ("wsl-unc", "wsl-native"):
        return False
    norm = root.replace("\\", "/").rstrip("/").lower()
    return norm.endswith("antigravity-projects/product-second-brain")

def reachable_path(path: str) -> str | None:
    """A filesystem path this machine can open for `path`, or None when it cannot."""
    if path.startswith("/home/") and is_windows():
        unc = "\\\\wsl.localhost\\%s%s" % (WSL_DISTRO, path.replace("/", "\\"))
        return unc if os.path.isdir(unc) else None
    return path if os.path.isdir(path) else None

# --------------------------------------------------------------------------- git

def via_wsl(path: str) -> bool:
    """A WSL checkout seen from Windows is run through wsl.exe, not the UNC share
    under wsl.localhost: git over that share took 5 minutes for this report, native git takes seconds."""
    return is_windows() and path.startswith("/home/")

def git(path: str, *args: str, timeout: int = GIT_TIMEOUT) -> tuple[int, str]:
    """Run git read-only. safe.directory=* because a WSL checkout opened from Windows
    (or the reverse) trips the ownership check, which is about ownership and not about
    what this script does. No shell is involved: arguments are passed as a list."""
    cmd = ["git", "-c", "safe.directory=*", "-C", path, *args]
    if via_wsl(path):
        cmd = ["wsl.exe", "-d", WSL_DISTRO, "--exec", *cmd]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
    except Exception as exc:                     # timeout, git missing
        return 1, "%s: %s" % (type(exc).__name__, exc)
    return p.returncode, (p.stdout or "").strip() if p.returncode == 0 else (p.stderr or p.stdout or "").strip()

def inspect_checkout(label: str, path: str, fetch: bool) -> dict:
    out = {"label": label, "path": path, "kind": classify_root(path), "reachable": False}
    real = reachable_path(path)
    if not real:
        out["note"] = "not reachable from this machine"
        return out
    out["reachable"] = True
    real = path if via_wsl(path) else real      # the path git is run against
    out["opened_as"] = real
    rc, top = git(real, "rev-parse", "--show-toplevel")
    if rc != 0:
        out["reachable"] = False
        out["note"] = "not a git checkout: %s" % top[:120]
        return out

    if fetch:
        rc, msg = git(real, "fetch", "--quiet", "origin")
        out["fetched"] = rc == 0
        if rc != 0:
            out["fetch_error"] = msg[:160]
    fetch_head = os.path.join(reachable_path(path) or real, ".git", "FETCH_HEAD")
    if os.path.exists(fetch_head):
        out["last_fetch_age_hours"] = round((time.time() - os.path.getmtime(fetch_head)) / 3600, 1)

    out["branch"] = git(real, "rev-parse", "--abbrev-ref", "HEAD")[1]
    out["head"] = git(real, "rev-parse", "--short", "HEAD")[1]

    rc, counts = git(real, "rev-list", "--left-right", "--count", "origin/main...HEAD")
    if rc == 0 and counts.split():
        behind, ahead = (int(x) for x in counts.split()[:2])
        out["behind_origin_main"], out["ahead_of_origin_main"] = behind, ahead
        if ahead:
            rc2, subjects = git(real, "log", "--format=%h %s", "-n", "5", "origin/main..HEAD")
            out["unpushed_sample"] = subjects.splitlines() if rc2 == 0 else []
    else:
        out["note"] = "no origin/main ref to compare with"

    rc, porcelain = git(real, "status", "--porcelain", "--untracked-files=normal")
    if rc == 0:
        lines = [l for l in porcelain.splitlines() if l.strip()]
        out["uncommitted_files"] = len(lines)
        out["uncommitted_state_files"] = sum(1 for l in lines if "journal/state/" in l)
    rc, stash = git(real, "stash", "list")
    out["stashes"] = len(stash.splitlines()) if rc == 0 and stash else 0
    return out

def state_writers(real_repo: str, days: int, identities: dict[str, str]) -> dict:
    """Last committer identity of every tracked journal/state/*.json, plus who wrote in the window."""
    rc, listing = git(real_repo, "ls-files", STATE_GLOB)
    files = [f for f in listing.splitlines() if f.endswith(".json")] if rc == 0 else []

    def row(rel, line):
        sha, name, email, when, subject = (line.split("|", 4) + [""] * 5)[:5]
        return {"file": rel, "sha": sha, "author": name, "email": email, "when": when,
                "subject": subject[:90],
                "machine": identities.get(email.lower(), "unknown (%s)" % email)}

    # One log call covers every file written in the last 30 days (newest first, so the first
    # time a file appears is its last writer). Only files quiet for longer need their own call.
    seen: dict[str, dict] = {}
    rc, log = git(real_repo, "log", "--since=30 days ago", "--format=@@%h|%an|%ae|%cI|%s",
                  "--name-only", "--", "journal/state")
    if rc == 0:
        current = None
        for line in log.splitlines():
            if line.startswith("@@"):
                current = line[2:]
            elif line.strip() and current:
                seen.setdefault(line.strip(), row(line.strip(), current))
    rows = []
    for rel in files:
        if rel in seen:
            rows.append(seen[rel])
            continue
        rc, line = git(real_repo, "log", "-1", "--format=%h|%an|%ae|%cI|%s", "--", rel, timeout=30)
        rows.append(row(rel, line) if rc == 0 and "|" in line else
                    {"file": rel, "error": line[:100] or "no history"})

    since = "%d days ago" % days
    rc, log = git(real_repo, "log", "--since=%s" % since, "--format=%ae", "--", "journal/state")
    recent: dict[str, int] = {}
    if rc == 0:
        for email in log.splitlines():
            email = email.strip().lower()
            if email:
                label = identities.get(email, "unknown (%s)" % email)
                recent[label] = recent.get(label, 0) + 1
    return {"files": rows, "recent_commits_by_machine": recent, "window_days": days}

# --------------------------------------------------------------------------- app workspace

def workspace_json_candidates() -> list[str]:
    cands = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        cands.append(os.path.join(appdata, "com.aisecondbrain.desktop", "workspace.json"))
    home = str(Path.home())
    cands.append(os.path.join(home, "Library", "Application Support", "com.aisecondbrain.desktop", "workspace.json"))
    cands.append(os.path.join(home, ".local", "share", "com.aisecondbrain.desktop", "workspace.json"))
    cands.append(os.path.join(home, ".config", "com.aisecondbrain.desktop", "workspace.json"))
    cands += glob.glob("/mnt/c/Users/*/AppData/Roaming/com.aisecondbrain.desktop/workspace.json")
    seen, out = set(), []
    for c in cands:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out

def app_workspace() -> dict:
    """What the ASB desktop app would open: ASB_WORKSPACE wins, then the registry."""
    info: dict = {"env_ASB_WORKSPACE": os.environ.get("ASB_WORKSPACE") or None,
                  "config_file": None, "registry_roots": [], "active_root": None}
    for cand in workspace_json_candidates():
        if not os.path.isfile(cand):
            continue
        info["config_file"] = cand
        try:
            with open(cand, "r", encoding="utf-8-sig") as f:       # tolerate a BOM; the app does not
                data = json.load(f)
        except Exception as exc:
            info["config_error"] = "%s: %s" % (type(exc).__name__, exc)
            break
        raw = open(cand, "rb").read(3)
        if raw == b"\xef\xbb\xbf":
            info["config_has_bom"] = True      # serde_json rejects this; the app falls back to onboarding
        spaces = data.get("workspaces") or []
        info["registry_roots"] = [{"name": w.get("name"), "root": w.get("root"),
                                   "last_opened_ms": w.get("lastOpenedAtMs")} for w in spaces]
        active_id = data.get("activeId")
        chosen = next((w for w in spaces if w.get("id") == active_id), None)
        if chosen is None and spaces:
            chosen = max(spaces, key=lambda w: w.get("lastOpenedAtMs") or 0)
            info["active_guess"] = "no activeId, used the most recently opened"
        info["active_root"] = (chosen or {}).get("root")
        info["active_name"] = (chosen or {}).get("name")
        break
    info["effective_root"] = info["env_ASB_WORKSPACE"] or info["active_root"]
    info["effective_source"] = ("ASB_WORKSPACE" if info["env_ASB_WORKSPACE"]
                                else ("workspace.json" if info["active_root"] else "none found"))
    if info["effective_root"]:
        info["effective_kind"] = classify_root(info["effective_root"])
        info["effective_is_canonical"] = is_canonical_root(info["effective_root"])
    return info

# --------------------------------------------------------------------------- report

def build_report(args) -> dict:
    identities = dict(IDENTITY_LABELS)
    for pair in args.identity or []:
        if "=" in pair:
            email, label = pair.split("=", 1)
            identities[email.strip().lower()] = label.strip()

    checkouts_spec = [("this checkout", str(REPO_ROOT)),
                      ("wsl canonical", WSL_CANONICAL),
                      ("macos checkout", MAC_CHECKOUT)]
    app = app_workspace()
    for entry in app.get("registry_roots", []):
        root = entry.get("root") or ""
        if root and os.path.isfile(os.path.join(root, "journal", "state", "work_tree.json")):
            checkouts_spec.append(("app workspace (%s)" % entry.get("name"), root))
    for extra in args.checkout or []:
        checkouts_spec.append(("extra", extra))

    seen_real, checkouts = set(), []
    for label, path in checkouts_spec:
        real = reachable_path(path) or path
        key = os.path.normcase(os.path.abspath(real)) if os.path.isdir(real) else path
        if key in seen_real:
            continue
        seen_real.add(key)
        checkouts.append(inspect_checkout(label, path, args.fetch))

    # State history is read from the best-informed reachable checkout: the canonical one
    # when reachable, else this one. `origin/main` content is what counts, so read the ref.
    history_src = next((c for c in checkouts if c["label"] == "wsl canonical" and c["reachable"]), None) \
        or next((c for c in checkouts if c["reachable"]), None)
    writers = state_writers(history_src["opened_as"], args.days, identities) if history_src else \
        {"files": [], "recent_commits_by_machine": {}, "window_days": args.days}
    writers["read_from"] = history_src["path"] if history_src else None

    findings = []
    for c in checkouts:
        if not c["reachable"]:
            continue
        if c.get("ahead_of_origin_main"):
            findings.append({"severity": "high", "where": c["label"],
                             "what": "%d commit(s) not on origin/main, so no other machine or cron job can see them"
                                     % c["ahead_of_origin_main"]})
        if c.get("behind_origin_main", 0) > 20:
            findings.append({"severity": "low", "where": c["label"],
                             "what": "%d commits behind origin/main (local refs; run with --fetch for a live number)"
                                     % c["behind_origin_main"]})
        if c.get("uncommitted_state_files"):
            findings.append({"severity": "high", "where": c["label"],
                             "what": "%d uncommitted journal/state file(s): the next sync can overwrite or lose them"
                                     % c["uncommitted_state_files"]})
        if c.get("stashes"):
            findings.append({"severity": "low", "where": c["label"],
                             "what": "%d stash(es) hold work that exists on this machine only" % c["stashes"]})
    for label, n in writers["recent_commits_by_machine"].items():
        if label not in CANONICAL_LABELS:
            findings.append({"severity": "high", "where": "journal/state history",
                             "what": "%d commit(s) in the last %d days touched journal/state as '%s', "
                                     "not the WSL/macOS identity" % (n, args.days, label)})
    if app.get("effective_root") and not app.get("effective_is_canonical"):
        findings.append({"severity": "high", "where": "ASB app workspace",
                         "what": "the app opens %s (%s via %s), not the canonical WSL checkout"
                                 % (app["effective_root"], app.get("effective_kind"), app["effective_source"])})
    if not app.get("effective_root"):
        findings.append({"severity": "low", "where": "ASB app workspace",
                         "what": "no workspace.json found on this machine and ASB_WORKSPACE is unset; "
                                 "run this on the machine that runs the app"})
    if app.get("config_has_bom"):
        findings.append({"severity": "med", "where": "ASB app workspace",
                         "what": "workspace.json starts with a UTF-8 BOM; the app rejects it and drops to onboarding"})

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "machine_platform": "windows" if is_windows() else sys.platform,
        "read_only": True,
        "fetched": bool(args.fetch),
        "checkouts": checkouts,
        "state_writers": writers,
        "app_workspace": app,
        "findings": findings,
        "verdict": "READY" if not any(f["severity"] == "high" for f in findings) else "NOT READY",
    }

def print_text(rep: dict) -> None:
    p = print
    p("ONE-HOST CHECK (read-only)  %s  on %s%s" % (
        rep["generated_at"], rep["machine_platform"], "" if rep["fetched"] else "  [no fetch: ahead/behind uses local refs]"))
    p("")
    p("1. CHECKOUTS")
    for c in rep["checkouts"]:
        if not c["reachable"]:
            p("  - %-26s %s -> %s" % (c["label"], c["path"], c.get("note", "unreachable")))
            continue
        p("  - %-26s %s" % (c["label"], c["path"]))
        p("      branch %s @ %s | ahead %s | behind %s | uncommitted %s (%s in journal/state) | stashes %s%s" % (
            c.get("branch"), c.get("head"), c.get("ahead_of_origin_main", "?"), c.get("behind_origin_main", "?"),
            c.get("uncommitted_files", "?"), c.get("uncommitted_state_files", "?"), c.get("stashes", "?"),
            " | last fetch %sh ago" % c["last_fetch_age_hours"] if "last_fetch_age_hours" in c else ""))
        for s in c.get("unpushed_sample", []):
            p("        unpushed: %s" % s)
    w = rep["state_writers"]
    p("")
    p("2. WHO LAST WROTE journal/state/*.json  (read from %s)" % w.get("read_from"))
    p("   commits touching journal/state in the last %d days, by inferred machine: %s" % (
        w["window_days"], ", ".join("%s=%d" % kv for kv in sorted(w["recent_commits_by_machine"].items())) or "none"))
    by_machine: dict[str, list] = {}
    for r in w["files"]:
        by_machine.setdefault(r.get("machine", "no history"), []).append(r)
    for machine, rows in sorted(by_machine.items(), key=lambda kv: -len(kv[1])):
        p("   %s: %d file(s)" % (machine, len(rows)))
        if machine not in CANONICAL_LABELS:
            for r in sorted(rows, key=lambda r: r.get("when", ""), reverse=True)[:12]:
                p("      %-44s %s  %s" % (os.path.basename(r["file"]), r.get("when", "?")[:16], r.get("subject", "")))
    a = rep["app_workspace"]
    p("")
    p("3. ASB APP WORKSPACE")
    p("   ASB_WORKSPACE env : %s" % (a["env_ASB_WORKSPACE"] or "(unset)"))
    p("   workspace.json    : %s" % (a["config_file"] or "(not found on this machine)"))
    p("   app opens         : %s  [%s, from %s]  canonical: %s" % (
        a.get("effective_root") or "(unknown)", a.get("effective_kind", "?"), a["effective_source"],
        a.get("effective_is_canonical", "?")))
    p("")
    p("4. FINDINGS  (%d)" % len(rep["findings"]))
    for f in sorted(rep["findings"], key=lambda f: {"high": 0, "med": 1, "low": 2}[f["severity"]]):
        p("   [%s] %s: %s" % (f["severity"].upper(), f["where"], f["what"]))
    p("")
    p("VERDICT: %s" % rep["verdict"])

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Read-only one-host (WSL only writer) pre-flight")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    ap.add_argument("--days", type=int, default=7, help="window for 'who wrote state recently' (default 7)")
    ap.add_argument("--fetch", action="store_true", help="git fetch origin first (updates remote-tracking refs only)")
    ap.add_argument("--strict", action="store_true", help="exit 1 when the verdict is NOT READY")
    ap.add_argument("--checkout", action="append", help="extra checkout path to inspect (repeatable)")
    ap.add_argument("--identity", action="append", metavar="EMAIL=LABEL",
                    help="map a git email to a machine label (repeatable)")
    args = ap.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    rep = build_report(args)
    if args.json:
        print(json.dumps(rep, indent=2))
    else:
        print_text(rep)
    return 1 if (args.strict and rep["verdict"] != "READY") else 0

if __name__ == "__main__":
    sys.exit(main())
