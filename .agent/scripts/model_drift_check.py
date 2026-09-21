#!/usr/bin/env python3
"""Say which model ids in the agy-bridge chains no longer exist, and which have a newer sibling.

Model ids move, and a chain entry pointing at a retired one fails in the worst
possible way: quietly. On 16 Sep 2026 `agy models` no longer listed
"Gemini 3.5 Flash (High)", which was the head of bulk-cheap, long-context and
draft. Nothing errored. run.py refused the dead id exactly as designed and fell
through to the next candidate, so every harvest and every draft had silently
been running on Gemini 3.1 Pro, a Pro-tier model doing bulk work, for as long
as the id had been dead. The same file also still pinned `gemini-2.5-flash`,
two generations behind, on a backend that had never run.

Neither was found by a check. Both were found because somebody happened to ask.

This is that check. It compares every model id used in a chain against what the
backend actually offers right now, and reports two different problems:

  DEAD   the id is not in the backend's list at all -> the chain entry is a no-op
  STALE  a higher version of the same family and tier is offered -> you are
         paying for, or waiting on, an older model than you could have

A backend that is unreachable is reported as UNCHECKED, never as clean. "I could
not look" and "I looked and it was fine" are different answers, and collapsing
them is how a check starts lying.

Usage:
    python3 .agent/scripts/model_drift_check.py              # full report, exit 1 on drift
    python3 .agent/scripts/model_drift_check.py --quiet      # one line, for a briefing
    python3 .agent/scripts/model_drift_check.py --heartbeat  # also self-report to the Routines panel
    python3 .agent/scripts/model_drift_check.py --json       # machine-readable

Exit codes: 0 clean, 1 drift found, 2 could not run.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BRIDGE = REPO_ROOT / ".agent" / "skills" / "agy-bridge" / "run.py"
STATE = REPO_ROOT / "journal" / "state" / "model_drift.json"

def load_bridge():
    """Import run.py as a module so this check reuses its config loading, its
    env-aware base_url resolution and its /models caller, rather than growing a
    second, subtly different copy of all three."""
    spec = importlib.util.spec_from_file_location("agy_bridge_run", BRIDGE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

# ---------- version parsing ----------

# A version is a number sitting between a family name and a variant word, e.g.
# "Gemini 3.8 Flash (High)" or "gemini-3.8-flash". Deliberately narrow: the
# "120b" in "gpt-oss:120b" is a parameter count, not a version, and treating it
# as one would report gpt-oss:20b as a stale gpt-oss:120b.
_AGY = re.compile(r"^(?P<fam>[A-Za-z][A-Za-z\- ]*?)\s+(?P<ver>\d+(?:\.\d+)?)\s+(?P<var>Flash|Pro)\b\s*(?:\((?P<tier>[A-Za-z]+)\))?\s*$")
_API = re.compile(r"^(?P<fam>[a-z][a-z\-/]*?)-(?P<ver>\d+(?:\.\d+)?)-(?P<var>flash|pro)\b(?P<tier>.*)$")

def parse_model(mid: str):
    """Return (key, version) or (None, None) when the id carries no version.

    `key` collapses everything EXCEPT the version, so two ids share a key only
    when swapping one for the other is a like-for-like upgrade. Tier is part of
    the key on purpose: "Gemini 3.8 Flash (Low)" is not an upgrade path for
    "Gemini 3.1 Pro (High)"."""
    m = _AGY.match(mid)
    if not m:
        m = _API.match(mid)
    if not m:
        return None, None
    g = m.groupdict()
    key = f"{g['fam'].strip().lower()}|{g['var'].lower()}|{(g.get('tier') or '').strip().lower()}"
    try:
        return key, float(g["ver"])
    except (TypeError, ValueError):
        return None, None

# ---------- live model lists ----------

def agy_live_models(mod):
    """Display names from `agy models`, which is what chains reference."""
    binary = getattr(mod, "AGY_BIN", "agy")
    try:
        out = subprocess.run([binary, "models"], capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    names = []
    for line in (out.stdout or "").splitlines():
        if "\t" not in line:
            continue
        _id, _, display = line.partition("\t")
        display = display.strip()
        if display:
            names.append(display)
    return names or None

def live_models(mod, cfg):
    """backend -> list of ids, or None when the backend could not be reached."""
    live = {"agy": agy_live_models(mod)}
    for name, spec in (cfg.get("backends") or {}).items():
        if name == "agy" or spec.get("retired"):
            continue
        if spec.get("type") != "openai-compatible":
            # Anthropic-compatible backends expose no /models list, so there is
            # nothing to compare against. Unchecked, not clean.
            live[name] = None
            continue
        if spec.get("no_auth") and not mod.local_router_up(spec):
            live[name] = None
            continue
        try:
            ids = mod.list_remote_models(name, spec, cfg)
        except Exception:
            ids = None
        live[name] = ids if isinstance(ids, list) and ids else None
    return live

# ---------- the check ----------

def chain_entries(cfg):
    """(capability, backend, model) for every candidate in every chain."""
    for cap, lst in (cfg.get("capabilities") or {}).items():
        if not isinstance(lst, list):
            continue
        for e in lst:
            if isinstance(e, str):
                yield cap, "agy", e
            elif isinstance(e, dict) and e.get("model"):
                yield cap, e.get("backend", "agy"), e["model"]

def check(mod, cfg):
    live = live_models(mod, cfg)
    dead, stale, unchecked = [], [], set()
    seen = set()

    for cap, backend, model in chain_entries(cfg):
        offered = live.get(backend)
        if offered is None:
            unchecked.add(backend)
            continue
        if model not in offered:
            dead.append({"capability": cap, "backend": backend, "model": model})
            continue
        if (backend, model) in seen:
            continue
        seen.add((backend, model))
        key, ver = parse_model(model)
        if key is None:
            continue
        best, best_ver = None, ver
        for cand in offered:
            ckey, cver = parse_model(cand)
            if ckey == key and cver is not None and cver > best_ver:
                best, best_ver = cand, cver
        if best:
            stale.append({"capability": cap, "backend": backend,
                          "model": model, "newer": best})

    return {"dead": dead, "stale": stale, "unchecked": sorted(unchecked),
            "backends_checked": sorted(b for b, v in live.items() if v is not None)}

def summarize(res) -> str:
    bits = []
    if res["dead"]:
        bits.append(f"{len(res['dead'])} dead id" + ("s" if len(res["dead"]) != 1 else ""))
    if res["stale"]:
        bits.append(f"{len(res['stale'])} newer available")
    if not bits:
        bits.append("model ids current")
    if res["unchecked"]:
        bits.append(f"unchecked: {', '.join(res['unchecked'])}")
    return "; ".join(bits)

def write_state(res, summary):
    """Leave the result where the dashboard and the morning update can read it
    without re-running the network calls."""
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(res)
        payload["summary"] = summary
        payload["drift"] = bool(res["dead"] or res["stale"])
        with open(STATE, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
            fh.write("\n")
    except OSError:
        pass  # a report that cannot cache itself is still a valid report

def main():
    ap = argparse.ArgumentParser(description="Report dead or outdated model ids in the agy-bridge chains")
    ap.add_argument("--quiet", action="store_true", help="one line, for a briefing")
    ap.add_argument("--json", action="store_true", help="machine-readable")
    ap.add_argument("--heartbeat", action="store_true", help="self-report to the Routines panel")
    args = ap.parse_args()

    if not BRIDGE.exists():
        print(f"[model-drift] agy-bridge not found at {BRIDGE}", file=sys.stderr)
        return 2
    try:
        mod = load_bridge()
        cfg = mod.load_config()
    except Exception as e:
        print(f"[model-drift] could not load agy-bridge config: {e}", file=sys.stderr)
        return 2

    res = check(mod, cfg)
    summary = summarize(res)
    write_state(res, summary)
    drift = bool(res["dead"] or res["stale"])

    if args.heartbeat:
        # A DEAD id is a real failure: that chain entry does nothing and the work
        # is silently landing on whatever sits behind it. A merely newer sibling
        # is not, and reporting it as one would turn the panel red on every
        # vendor release until nobody reads it. So only DEAD fails.
        status = "fail" if res["dead"] else "ok"
        try:
            subprocess.run(
                [sys.executable, str(REPO_ROOT / ".agent" / "scripts" / "heartbeat.py"),
                 "--job", "model-drift",
                 "--status", status,
                 "--summary", summary],
                capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            pass

    if args.json:
        print(json.dumps(res, indent=2))
    elif args.quiet:
        print(f"[model-drift] {summary}")
    else:
        print(f"backends checked: {', '.join(res['backends_checked']) or 'none'}")
        if res["unchecked"]:
            print(f"UNCHECKED (unreachable or no /models): {', '.join(res['unchecked'])}")
        for d in res["dead"]:
            print(f"  DEAD   {d['capability']:14s} {d['backend']}: {d['model']}  <- not offered any more")
        for s in res["stale"]:
            print(f"  STALE  {s['capability']:14s} {s['backend']}: {s['model']}  ->  {s['newer']}")
        if not drift:
            print("  no dead or outdated ids in any chain")
        print(f"\n{summary}")
        if res["dead"]:
            print("Fix: python3 .agent/skills/agy-bridge/run.py --setup --write, then repoint the chain in models.json")

    return 1 if drift else 0

if __name__ == "__main__":
    sys.exit(main())
