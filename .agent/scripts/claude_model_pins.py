#!/usr/bin/env python3
"""Keep every pinned Claude model id on the newest model of its family.

Model names are pinned as full ids (`claude-sonnet-5-5`), because an older
Claude Code binary maps a short name like "opus" to whatever was current when
it was built: on 18 Oct 2026 that sent a Telegram bot to Opus 5 and its
background calls to Haiku 4.5. A full id never drifts on its own, so this
check moves it forward instead.

It asks the Models API (with the Claude Code login, no API key needed) which
models exist, finds the newest haiku, sonnet, opus and fable, and compares
every pin in the files below. A pin stays in its family: sonnet moves to a newer
sonnet, never to opus. Short names ("opus") in a model field are reported, not
rewritten.

Files checked (those that exist):
  ~/.claude/settings.json                      (this machine, WSL)
  /mnt/c/Users/*/.claude/settings.json         (Windows)
  <repo>/.claude/settings.json                 (committed and pushed on change)
  Telegram bridge .env files (MODEL=...)       (bridge restarted on change)

Usage:
  python3 .agent/scripts/claude_model_pins.py              # report, exit 1 if behind
  python3 .agent/scripts/claude_model_pins.py --apply      # also update the pins
  python3 .agent/scripts/claude_model_pins.py --apply --heartbeat

Exit codes: 0 all current, 1 behind (or updated, with --apply), 2 could not run.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FAMILIES = ("haiku", "sonnet", "opus", "fable")
PIN_RE = re.compile(r"claude-(haiku|sonnet|opus|fable)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?(?![\d-])")
UNDATED_RE = re.compile(r"claude-(haiku|sonnet|opus|fable)-(\d+)(?:-(\d{1,2}))?")
ALIAS_RE = re.compile(r'"model"\s*:\s*"(opus|sonnet|haiku|fable)(\[1m\])?"')
BRIDGES = {
    # .env path -> node command line fragment of the running bridge
    "/mnt/c/Users/you/bulan-bridge/.env": r"bulan-bridge\\index.js",
    "/mnt/c/Users/you/AppData/Roaming/com.aisecondbrain.desktop/workspace/telegram-bridge/.env": r"bridge.js$",
}

def files():
    out = [Path.home() / ".claude/settings.json", REPO_ROOT / ".claude/settings.json"]
    out += [Path(p) for p in glob.glob("/mnt/c/Users/*/.claude/settings.json")]
    out += [Path(p) for p in BRIDGES]
    seen, res = set(), []
    for p in out:
        if p.is_file() and p.resolve() not in seen:
            seen.add(p.resolve())
            res.append(p)
    return res

def latest_models():
    # The WSL login lapses when nobody uses it; the Windows one is used daily.
    # Take whichever access token expires last.
    creds = [Path.home() / ".claude/.credentials.json"]
    creds += [Path(p) for p in glob.glob("/mnt/c/Users/*/.claude/.credentials.json")]
    tokens = []
    for c in creds:
        try:
            o = json.loads(c.read_text())["claudeAiOauth"]
            tokens.append((o.get("expiresAt") or 0, o["accessToken"]))
        except (OSError, KeyError, ValueError):
            continue
    if not tokens:
        raise RuntimeError("no Claude Code login found")
    token = max(tokens)[1]
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/models?limit=100",
        headers={"Authorization": f"Bearer {token}", "anthropic-version": "2023-06-01",
                 "anthropic-beta": "oauth-2025-04-20"})
    ids = [m["id"] for m in json.load(urllib.request.urlopen(req, timeout=30))["data"]]
    best = {}
    for mid in ids:
        m = UNDATED_RE.fullmatch(mid)
        if not m:
            continue  # dated snapshots and other shapes are never a target
        fam, major, minor = m.group(1), int(m.group(2)), int(m.group(3) or 0)
        if fam not in best or (major, minor) > best[fam][0]:
            best[fam] = ((major, minor), mid)
    return {f: v[1] for f, v in best.items()}, ids

def version(pin):
    m = PIN_RE.fullmatch(pin)
    return (int(m.group(2)), int(m.group(3) or 0)) if m else (0, 0)

def scan(newest):
    findings = []
    for p in files():
        text = p.read_text(encoding="utf-8", errors="replace")
        for m in sorted(set(x.group(0) for x in PIN_RE.finditer(text))):
            fam = PIN_RE.fullmatch(m).group(1)
            target = newest.get(fam)
            if target and version(target) > version(m):
                findings.append({"file": str(p), "kind": "behind", "pin": m, "newest": target})
        for a in ALIAS_RE.finditer(text):
            findings.append({"file": str(p), "kind": "alias", "pin": a.group(0), "newest": None})
    return findings

def restart_bridge(env_path):
    frag = BRIDGES.get(env_path)
    if not frag or not shutil.which("powershell.exe"):
        return "restart by hand"
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | "
          f"Where-Object {{ $_.CommandLine -match '{frag}' }} | "
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -Confirm:$false }")
    r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    return "restarted" if r.returncode == 0 else "restart failed"

def apply(findings):
    stamp = datetime.now().strftime("%Y%m%d")
    notes, repo_changed = [], False
    by_file = {}
    for f in findings:
        if f["kind"] == "behind":
            by_file.setdefault(f["file"], []).append(f)
    for path, items in by_file.items():
        p = Path(path)
        shutil.copy2(p, f"{p}.bak-{stamp}")
        text = p.read_text(encoding="utf-8")
        for f in items:
            text = re.sub(re.escape(f["pin"]) + r"(?![\d-])", f["newest"], text)
        p.write_text(text, encoding="utf-8")
        note = f"{path}: " + ", ".join(f"{f['pin']} -> {f['newest']}" for f in items)
        if path in BRIDGES:
            note += f" ({restart_bridge(path)})"
        if p.resolve() == (REPO_ROOT / ".claude/settings.json").resolve():
            repo_changed = True
        notes.append(note)
    if repo_changed:
        msg = "settings: move pinned Claude models to newest (claude_model_pins.py)"
        subprocess.run(["git", "-C", str(REPO_ROOT), "add", ".claude/settings.json"], check=False)
        subprocess.run(["git", "-C", str(REPO_ROOT), "commit", "-q", "-m", msg], check=False)
        subprocess.run(["git", "-C", str(REPO_ROOT), "push", "-q"], check=False)
    return notes

def heartbeat(status, summary):
    hb = REPO_ROOT / ".agent/scripts/heartbeat.py"
    if hb.exists():
        subprocess.run([sys.executable, str(hb), "--job", "claude_model_pins",
                        "--status", status, "--summary", summary], check=False)

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--heartbeat", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    try:
        newest, _ = latest_models()
    except Exception as e:  # unreachable is UNCHECKED, never clean
        print(f"UNCHECKED: could not list Claude models ({e})")
        if a.heartbeat:
            heartbeat("fail", f"could not list Claude models: {e}")
        return 2
    findings = scan(newest)
    notes = apply(findings) if a.apply else []
    if a.json:
        print(json.dumps({"newest": newest, "findings": findings, "applied": notes}, indent=2))
    else:
        print("newest: " + ", ".join(f"{k} {v}" for k, v in sorted(newest.items())))
        if not findings:
            print("all pins current")
        for f in findings:
            if f["kind"] == "behind":
                print(f"BEHIND {f['pin']} -> {f['newest']}  {f['file']}")
            else:
                print(f"ALIAS  {f['pin']}  {f['file']} (pin a full id by hand)")
        for n in notes:
            print(f"UPDATED {n}")
    if a.heartbeat:
        behind = sum(1 for f in findings if f["kind"] == "behind")
        aliases = len(findings) - behind
        summary = (f"{len(notes)} file(s) moved to newest" if notes else
                   f"{behind} pin(s) behind" if behind else "all Claude pins current")
        if aliases:
            summary += f"; {aliases} short alias(es)"
        heartbeat("ok" if not findings or notes else "fail", summary)
    return 1 if findings else 0

if __name__ == "__main__":
    sys.exit(main())
