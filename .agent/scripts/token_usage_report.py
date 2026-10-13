#!/usr/bin/env python3
"""token_usage_report.py - Which Claude sessions spent the most, and on which model.

Reads the Claude Code transcripts under ~/.claude/projects (parent session
files plus their subagent sidecars), counts each assistant message once (by
message id, so streamed duplicates are not double-counted), and ranks
sessions over a window.

Built 13 Oct 2026 after one quiz QA session spent 377M tokens on 81
nested Opus subagents. Pairs with the global hook
~/.claude/hooks/agent_spend_guard.py and CLAUDE_CODE_SUBAGENT_MODEL=sonnet.
The question it answers each week: did subagent spend move off Opus?

"Weighted" tokens price every class relative to plain input:
input 1, cache write 1.25, cache read 0.1, output 5. These are price ratios,
not dollars; they let a cache-read-heavy session and an output-heavy one be
compared fairly. The ranking uses weighted tokens.

Usage:
  python3 .agent/scripts/token_usage_report.py                 # last 7 days
  python3 .agent/scripts/token_usage_report.py --days 2 --top 10
  python3 .agent/scripts/token_usage_report.py --root /mnt/c/Users/you/.claude/projects \
      --out journal/reports/token_usage/2026-10-18.md
"""
import argparse
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone

WEIGHTS = {"input": 1.0, "cache_write": 1.25, "cache_read": 0.1, "output": 5.0}

def short_model(m):
    for k in ("opus", "sonnet", "haiku", "fable"):
        if k in (m or ""):
            return k
    return m or "?"

def scan(root, days):
    cutoff = time.time() - days * 86400
    cut_iso = datetime.fromtimestamp(cutoff, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    sessions = {}
    for proj in os.listdir(root):
        pdir = os.path.join(root, proj)
        if not os.path.isdir(pdir):
            continue
        for dirpath, dirnames, files in os.walk(pdir):
            dirnames[:] = [d for d in dirnames if d != "memory"]
            for f in files:
                if not f.endswith(".jsonl"):
                    continue
                path = os.path.join(dirpath, f)
                try:
                    if os.path.getmtime(path) < cutoff:
                        continue
                except OSError:
                    continue
                rel = os.path.relpath(path, pdir).replace("\\", "/")
                sid = rel.split("/")[0].replace(".jsonl", "")
                is_sub = "/" in rel
                s = sessions.setdefault((proj, sid), {
                    "tok": defaultdict(int), "weighted": 0.0, "sub_weighted": 0.0,
                    "by_model": defaultdict(float), "sub_by_model": defaultdict(float),
                    "agents": 0, "title": None, "start": None, "end": None})
                if is_sub:
                    s["agents"] += 1
                _read(path, s, is_sub, cut_iso)
    return sessions

def _read(path, s, is_sub, cut_iso):
    seen = set()
    try:
        fh = open(path, encoding="utf-8", errors="ignore")
    except OSError:
        return
    with fh:
        for line in fh:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            ts = e.get("timestamp")
            if not is_sub and s["title"] is None and e.get("type") == "user":
                c = (e.get("message") or {}).get("content")
                if isinstance(c, list):
                    c = " ".join(x.get("text", "") for x in c
                                 if isinstance(x, dict) and x.get("type") == "text")
                if isinstance(c, str) and c.strip() and not c.startswith("<"):
                    s["title"] = " ".join(c.split())[:80]
            m = e.get("message") or {}
            u = m.get("usage")
            if e.get("type") != "assistant" or not u or (ts and ts < cut_iso):
                continue
            mid = m.get("id") or e.get("uuid")
            if mid in seen:
                continue
            seen.add(mid)
            parts = {"input": u.get("input_tokens", 0),
                     "cache_write": u.get("cache_creation_input_tokens", 0),
                     "cache_read": u.get("cache_read_input_tokens", 0),
                     "output": u.get("output_tokens", 0)}
            w = sum(parts[k] * WEIGHTS[k] for k in parts)
            for k, v in parts.items():
                s["tok"][k] += v
            s["weighted"] += w
            model = short_model(m.get("model"))
            s["by_model"][model] += w
            if is_sub:
                s["sub_weighted"] += w
                s["sub_by_model"][model] += w
            if ts:
                s["start"] = min(s["start"] or ts, ts)
                s["end"] = max(s["end"] or ts, ts)

def fmt(n):
    return f"{n / 1e6:,.1f}M"

def report(sessions, days, top):
    rows = [(k, s) for k, s in sessions.items() if s["weighted"] > 0]
    rows.sort(key=lambda r: -r[1]["weighted"])
    total_w = sum(s["weighted"] for _, s in rows) or 1
    total_raw = sum(sum(s["tok"].values()) for _, s in rows)
    sub_models, all_models = defaultdict(float), defaultdict(float)
    for _, s in rows:
        for m, v in s["sub_by_model"].items():
            sub_models[m] += v
        for m, v in s["by_model"].items():
            all_models[m] += v
    sub_total = sum(sub_models.values()) or 1

    out = [f"# Claude token usage, last {days} day(s)",
           "",
           f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}. "
           f"{len(rows)} sessions, {fmt(total_raw)} raw tokens, {fmt(total_w)} weighted.",
           "",
           "## Spend by model (weighted)",
           "",
           "| Model | All sessions | Subagents only |",
           "| :--- | ---: | ---: |"]
    for m in sorted(all_models, key=lambda x: -all_models[x]):
        out.append(f"| {m} | {fmt(all_models[m])} ({100 * all_models[m] / total_w:.0f}%) "
                   f"| {fmt(sub_models.get(m, 0))} ({100 * sub_models.get(m, 0) / sub_total:.0f}%) |")
    out += ["",
            "Target after 13 Oct 2026: subagent spend on opus falls toward zero, "
            "except planning and design agents.",
            "",
            f"## Top {top} sessions (weighted)",
            "",
            "| Session | Project | Weighted | Share | Subagents | Agents | Opus share | First prompt |",
            "| :--- | :--- | ---: | ---: | ---: | ---: | ---: | :--- |"]
    for (proj, sid), s in rows[:top]:
        opus = s["by_model"].get("opus", 0) / s["weighted"]
        title = (s["title"] or "").replace("|", "/")
        out.append(f"| `{sid[:8]}` | {proj[-28:]} | {fmt(s['weighted'])} | "
                   f"{100 * s['weighted'] / total_w:.1f}% | {fmt(s['sub_weighted'])} | "
                   f"{s['agents']} | {100 * opus:.0f}% | {title} |")
    out.append("")
    return "\n".join(out)

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--days", type=float, default=7)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--out", help="write the markdown here instead of stdout")
    a = ap.parse_args()
    text = report(scan(a.root, a.days), a.days, a.top)
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(a.out)
    else:
        print(text)

if __name__ == "__main__":
    main()
