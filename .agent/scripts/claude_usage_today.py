#!/usr/bin/env python3
"""Claude token cost for one day, per session and per model.

Reads the Claude Code transcripts (`~/.claude/projects/*/*.jsonl`) on this
machine, and on WSL also the Windows user's transcripts under /mnt/c, so the
ASB desktop app sessions are included. Prices are first-party API rates; a
subscription quota follows the same order, so the ranking is what matters.

Usage:
  python3 .agent/scripts/claude_usage_today.py                # today, WIB
  python3 .agent/scripts/claude_usage_today.py --date 2026-10-18 --top 5
  python3 .agent/scripts/claude_usage_today.py --json
"""
import argparse
import collections
import datetime
import glob
import json
import os

WIB = datetime.timezone(datetime.timedelta(hours=7))

# $ per MTok: input, output. Cache write = 1.25x input (5m) or 2x (1h), cache read = 0.1x input.
PRICES = {
    "fable": (10.0, 50.0),
    "opus-5-5": (4.0, 20.0),
    "opus": (5.0, 25.0),
    "sonnet-5-5": (2.0, 10.0),
    "sonnet-5": (2.0, 10.0),
    "sonnet": (3.0, 15.0),
    "haiku-5-5": (0.10, 0.50),
    "haiku": (1.0, 5.0),
}
HAIKU_LONG = (0.50, 2.50)  # Haiku 5.5 above 100k prompt tokens

def price_for(model):
    if not model.startswith("claude-"):
        return None  # glm, gemini, synthetic: not Claude quota
    for key, p in PRICES.items():
        if key in model:
            return p
    return None

def roots():
    out = [os.path.expanduser("~/.claude/projects")]
    out += glob.glob("/mnt/c/Users/*/.claude/projects")
    return [r for r in dict.fromkeys(out) if os.path.isdir(r)]

def first_prompt(fp):
    try:
        with open(fp, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"type":"user"' not in line.replace(" ", ""):
                    continue
                try:
                    c = json.loads(line).get("message", {}).get("content")
                except Exception:
                    continue
                if isinstance(c, list):
                    c = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
                if isinstance(c, str):
                    c = c.strip().replace("\n", " ")
                    if c and not c.startswith("<"):
                        return c[:90]
    except OSError:
        pass
    return "(no prompt)"

def scan(day):
    start = datetime.datetime.combine(day, datetime.time(0), WIB)
    end = start + datetime.timedelta(days=1)
    sessions = {}
    for root in roots():
        for fp in glob.glob(os.path.join(root, "*", "*.jsonl")):
            try:
                if datetime.datetime.fromtimestamp(os.path.getmtime(fp), datetime.timezone.utc) < start:
                    continue
                f = open(fp, encoding="utf-8", errors="replace")
            except OSError:
                continue
            with f:
                for line in f:
                    if '"usage"' not in line:
                        continue
                    try:
                        e = json.loads(line)
                        t = datetime.datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00"))
                    except Exception:
                        continue
                    if not (start <= t < end):
                        continue
                    msg = e.get("message") or {}
                    u = msg.get("usage") or {}
                    model = msg.get("model") or "unknown"
                    if model == "<synthetic>":
                        continue
                    cc = u.get("cache_creation") or {}
                    w5 = cc.get("ephemeral_5m_input_tokens")
                    w1 = cc.get("ephemeral_1h_input_tokens") or 0
                    if w5 is None:
                        w5 = (u.get("cache_creation_input_tokens") or 0) - w1
                    inp = u.get("input_tokens") or 0
                    cr = u.get("cache_read_input_tokens") or 0
                    out = u.get("output_tokens") or 0
                    p = price_for(model)
                    cost = 0.0
                    if p:
                        if "haiku-5-5" in model and inp + w5 + w1 + cr > 100_000:
                            p = HAIKU_LONG
                        pi, po = p
                        cost = (inp * pi + w5 * pi * 1.25 + w1 * pi * 2 + cr * pi * 0.1 + out * po) / 1e6
                    s = sessions.setdefault(fp, {
                        "file": fp, "session": os.path.basename(fp)[:8], "cwd": e.get("cwd", ""),
                        "models": collections.Counter(), "cost": 0.0, "reqs": 0,
                        "out": 0, "cache_read": 0, "other_reqs": 0, "last": t,
                        "first_ctx": None, "big_cost": 0.0,
                    })
                    if p:
                        s["cost"] += cost
                        s["models"][model] += cost
                        s["reqs"] += 1
                        s["out"] += out
                        s["cache_read"] += cr
                        ctx = inp + w5 + w1 + cr
                        if s["first_ctx"] is None and not e.get("isSidechain"):
                            s["first_ctx"] = ctx
                        if ctx > 200_000:
                            s["big_cost"] += cost
                    else:
                        s["other_reqs"] += 1
                    s["last"] = max(s["last"], t)
    return sessions

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--date", help="YYYY-MM-DD in WIB (default: today)")
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    day = datetime.date.fromisoformat(a.date) if a.date else datetime.datetime.now(WIB).date()

    sessions = scan(day)
    by_model = collections.Counter()
    for s in sessions.values():
        by_model.update(s["models"])
    total = sum(by_model.values())
    other = sum(s["other_reqs"] for s in sessions.values())
    top = sorted((s for s in sessions.values() if s["cost"] > 0), key=lambda s: -s["cost"])[: a.top]
    for s in top:
        s["prompt"] = first_prompt(s["file"])

    if a.json:
        print(json.dumps({
            "date": day.isoformat(), "total_usd": round(total, 2),
            "by_model": {m: round(c, 2) for m, c in by_model.most_common()},
            "non_claude_requests": other,
            "top": [{"session": s["session"], "usd": round(s["cost"], 2),
                     "model": s["models"].most_common(1)[0][0], "requests": s["reqs"],
                     "cwd": s["cwd"], "prompt": s["prompt"]} for s in top],
        }, indent=2))
        return

    models = ", ".join(f"{m.replace('claude-', '')} ${c:.0f}" for m, c in by_model.most_common())
    print(f"Claude cost {day.isoformat()} (API-rate equivalent): ${total:.0f} total ({models}); "
          f"{other} requests on non-Claude models (no Claude quota).")
    # Two health numbers from the 23 Oct 2026 audit. Targets: big-context share under 25%,
    # median first-request context under 45k.
    big = sum(s["big_cost"] for s in sessions.values())
    firsts = sorted(s["first_ctx"] for s in sessions.values() if s["first_ctx"])
    med = firsts[len(firsts) // 2] // 1000 if firsts else 0
    share = (100 * big / total) if total else 0
    print(f"Context health: {share:.0f}% of cost from requests over 200k context (target <25%); "
          f"median first-request context {med}k tokens (target <45k).")
    for i, s in enumerate(top, 1):
        m = s["models"].most_common(1)[0][0].replace("claude-", "")
        print(f"{i}. ${s['cost']:.0f} {m}, {s['reqs']} requests, {s['session']}: {s['prompt']}")

if __name__ == "__main__":
    main()
