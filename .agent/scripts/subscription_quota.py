#!/usr/bin/env python3
"""How much of each subscription plan's limit windows is used, and when each resets.

Read-only. Two plans publish their remaining quota, both checked live on 23 Oct 2026:

  * Claude (Max / Pro): GET api.anthropic.com/api/oauth/usage with the Claude Code
    OAuth token. The token is only read, never refreshed: a refresh here would rotate
    the CLI's own refresh token. An expired token is reported, not repaired.
  * z.ai GLM Coding Plan: GET api.z.ai/api/monitor/usage/quota/limit with the raw key.

Not measurable: Kimi (no published quota endpoint) and Antigravity / agy (the CLI
keeps its quota internal). They are named in the output, without numbers.

Usage:
  python3 .agent/scripts/subscription_quota.py           # table, WIB times
  python3 .agent/scripts/subscription_quota.py --json
"""
import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_secrets  # noqa: E402

WIB = datetime.timezone(datetime.timedelta(hours=7))
TIMEOUT = 15
ZAI_UNITS = {3: "hour", 6: "week"}  # verified from reset times; others are not guessed
CLAUDE_KINDS = {"session": "5-hour window", "weekly_all": "This week, all models"}

def _home_candidates(rel):
    paths = [os.path.expanduser(os.path.join("~", rel))]
    if os.path.isdir("/mnt/c/Users"):  # WSL: the Windows ASB app user too
        paths.append(os.path.join("/mnt/c/Users", os.environ.get("WIN_USER", "the owner"), rel))
    return [p for p in paths if os.path.isfile(p)]

def _get(url, headers):
    req = urllib.request.Request(url, headers={"Accept": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.load(r)

def _window(label, percent, reset):
    return {"label": label, "percent_used": percent, "resets_at": reset.isoformat() if reset else None}

def claude():
    files = _home_candidates(".claude/.credentials.json")
    if not files:
        return {"plan": "Claude", "state": "not_connected"}
    oauth = json.load(open(files[0], encoding="utf-8")).get("claudeAiOauth") or {}
    tier = oauth.get("rateLimitTier") or ""
    plan = "Claude " + (oauth.get("subscriptionType") or "").capitalize()
    if "20x" in tier or "5x" in tier:
        plan += " " + ("20x" if "20x" in tier else "5x")
    if not oauth.get("accessToken"):
        return {"plan": plan, "state": "not_connected"}
    if (oauth.get("expiresAt") or 0) / 1000 < datetime.datetime.now().timestamp():
        return {"plan": plan, "state": "expired", "note": "refreshes the next time a chat runs"}
    try:
        data = _get("https://api.anthropic.com/api/oauth/usage", {
            "Authorization": "Bearer " + oauth["accessToken"], "anthropic-beta": "oauth-2025-04-20"})
    except urllib.error.HTTPError as e:
        return {"plan": plan, "state": "expired" if e.code == 401 else "error", "note": f"HTTP {e.code}"}
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        return {"plan": plan, "state": "error", "note": str(e)[:120]}
    windows = []
    for lim in data.get("limits") or []:
        kind = lim.get("kind") or "limit"
        label = CLAUDE_KINDS.get(kind)
        if label is None:
            model = (((lim.get("scope") or {}).get("model")) or {}).get("display_name")
            label = f"This week, {model}" if kind == "weekly_scoped" and model else kind
        reset = lim.get("resets_at")
        windows.append(_window(label, lim.get("percent"), datetime.datetime.fromisoformat(reset) if reset else None))
    if not windows:  # older shape
        for key, label in (("five_hour", "5-hour window"), ("seven_day", "This week, all models")):
            w = data.get(key) or {}
            if w.get("utilization") is not None:
                reset = w.get("resets_at")
                windows.append(_window(label, w["utilization"], datetime.datetime.fromisoformat(reset) if reset else None))
    return {"plan": plan, "state": "ok", "windows": windows}

def _zai_key():
    key = harness_secrets.load_secret("ZAI_API_TOKEN", extra_files=[".agent/skills/agy-bridge/token.env"])
    if key:
        return key
    for path in _home_candidates("AppData/Roaming/com.aisecondbrain.desktop/providers.json"):
        store = json.load(open(path, encoding="utf-8"))
        items = store.get("providers", store) if isinstance(store, dict) else store
        for p in items if isinstance(items, list) else items.values():
            if p.get("kind") == "glm" and "api.z.ai" in (p.get("base_url") or p.get("baseUrl") or ""):
                return p.get("api_key") or p.get("apiKey")
    return None

def zai():
    key = _zai_key()
    if not key:
        return {"plan": "GLM Coding Plan", "state": "not_connected"}
    try:
        data = (_get("https://api.z.ai/api/monitor/usage/quota/limit", {"Authorization": key}) or {}).get("data") or {}
    except urllib.error.HTTPError as e:
        return {"plan": "GLM Coding Plan", "state": "error", "note": f"HTTP {e.code}"}
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        return {"plan": "GLM Coding Plan", "state": "error", "note": str(e)[:120]}
    windows = []
    for lim in data.get("limits") or []:
        unit, n = ZAI_UNITS.get(lim.get("unit")), lim.get("number") or 1
        if unit == "hour":
            label = f"{n}-hour window"
        elif unit == "week":
            label = "This week" if n == 1 else f"{n}-week window"
        else:
            label = "Limit window"
        ms = lim.get("nextResetTime")
        reset = datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc) if ms else None
        windows.append(_window(label, lim.get("percentage"), reset))
    return {"plan": "GLM " + (data.get("level") or "").capitalize(), "state": "ok", "windows": windows}

def _in(reset_iso):
    if not reset_iso:
        return ""
    reset = datetime.datetime.fromisoformat(reset_iso).astimezone(WIB)
    mins = max(0, int((reset - datetime.datetime.now(WIB)).total_seconds() // 60))
    span = f"{mins // 60}h {mins % 60:02d}m" if mins < 1440 else f"{mins // 1440}d {mins % 1440 // 60}h"
    return f"resets in {span} ({reset:%a %d %b %H:%M} WIB)"

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    plans = [claude(), zai()]
    unmeasured = ["Kimi (no published quota)", "Antigravity / agy (quota kept inside the CLI)"]
    if args.json:
        print(json.dumps({"plans": plans, "not_measurable": unmeasured}, indent=1))
        return
    for p in plans:
        if p["state"] != "ok":
            print(f"{p['plan']}: {p['state'].replace('_', ' ')}" + (f" ({p['note']})" if p.get("note") else ""))
            continue
        print(p["plan"])
        for w in p["windows"]:
            pct = w["percent_used"]
            flag = "  << nearly full" if (pct or 0) >= 90 else ("  < high" if (pct or 0) >= 75 else "")
            print(f"  {w['label']:<24} {pct:>5}% used, {100 - (pct or 0):>3}% left  {_in(w['resets_at'])}{flag}")
    print("Not measurable: " + "; ".join(unmeasured))

if __name__ == "__main__":
    main()
