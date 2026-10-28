#!/usr/bin/env python3
"""Weekly model quality and cost check: does Sonnet cost the owner more turns than Opus?

Reads the Claude Code transcripts on this machine (and the Windows user's under
/mnt/c when run on WSL), groups every session with 5 or more the owner turns by its
dominant model and task class, and prints four numbers per group:

  correction %        share of the owner's turns that contain a redo marker
                      (ulang, salah, benerin, cek lagi, revisi, wrong, again...)
  bolak-balik / sess  two corrections in a row, per session
  turns / sess        the owner turns per session
  usd / turn          API-rate cost per the owner turn

Decision rule (the owner, 23 Oct 2026): a task class stays on Sonnet only if its
bolak-balik is within 0.2 of Opus and its correction % within 3 points.
Otherwise that class goes back to Opus. The script prints the verdict per class.

Usage:
  python3 .agent/scripts/model_quality_weekly.py              # last 7 days, WIB
  python3 .agent/scripts/model_quality_weekly.py --days 21
  python3 .agent/scripts/model_quality_weekly.py --write      # also journal/reports/model_quality/<date>.md
"""
import argparse
import collections
import datetime
import glob
import json
import os
import re

WIB = datetime.timezone(datetime.timedelta(hours=7))
CORR = re.compile(r"\b(ulang|salah|bukan itu|bukan gitu|kok |harusnya|revisi|benerin|betulin|cek lagi|cek ulang|lagi dong|masih salah|gak bener|nggak bener|ga bener|tolol|bego|kenapa gak|kenapa nggak|udah gw bilang|belum|wrong|redo|again|not what|that's not|fix it|still wrong|you missed|you forgot|incorrect)\b", re.I)
TASK = [
    ("slack/reply/context", re.compile(r"slack|reply|balas|bales|thread|\bdm\b|mention|context|konteks|sweep|follow.?up|chase|email|gmail|whatsapp", re.I)),
    ("docs/pm", re.compile(r"\bprd\b|\bmom\b|weekly|report|ticket|linear|jira|roadmap|\bbrd\b|meeting|decision", re.I)),
    ("video", re.compile(r"video|render|clip|dji|talk.?time|story.?time|tts", re.I)),
    ("coding/app", re.compile(r"\bapp\b|asb|android|tauri|bug|error|build|deploy|script|hook|skill|code|vps|server|cron", re.I)),
]
PRICES = {"fable": (10.0, 50.0), "opus-5-5": (4.0, 20.0), "opus": (5.0, 25.0), "sonnet-5-5": (2.0, 10.0), "sonnet-5": (2.0, 10.0), "sonnet": (3.0, 15.0), "haiku-5-5": (0.10, 0.50), "haiku": (1.0, 5.0)}

def price_for(model):
    if not model.startswith("claude-"):
        return None
    for k, p in PRICES.items():
        if k in model:
            return p
    return None

def roots():
    out = [os.path.expanduser("~/.claude/projects")] + glob.glob("/mnt/c/Users/*/.claude/projects")
    return [r for r in dict.fromkeys(out) if os.path.isdir(r)]

def scan(days):
    start = datetime.datetime.now(WIB) - datetime.timedelta(days=days)
    groups = collections.defaultdict(lambda: collections.Counter())
    worst = []
    for root in roots():
        for fp in glob.glob(os.path.join(root, "*", "*.jsonl")):
            try:
                if datetime.datetime.fromtimestamp(os.path.getmtime(fp), datetime.timezone.utc) < start:
                    continue
                f = open(fp, encoding="utf-8", errors="replace")
            except OSError:
                continue
            models = collections.Counter()
            turns, cost, first_ts = [], 0.0, None
            with f:
                for line in f:
                    try:
                        e = json.loads(line)
                    except Exception:
                        continue
                    if e.get("isSidechain") or e.get("isMeta"):
                        continue
                    m = e.get("message") or {}
                    if e.get("type") == "assistant":
                        mm = m.get("model") or ""
                        if not mm.startswith("claude-"):
                            continue
                        fam = "opus" if "opus" in mm else "sonnet" if "sonnet" in mm else "haiku" if "haiku" in mm else "fable" if "fable" in mm else "other"
                        models[fam] += 1
                        u = m.get("usage") or {}
                        p = price_for(mm)
                        if p and u:
                            cc = u.get("cache_creation") or {}
                            w5 = cc.get("ephemeral_5m_input_tokens")
                            w1 = cc.get("ephemeral_1h_input_tokens") or 0
                            if w5 is None:
                                w5 = (u.get("cache_creation_input_tokens") or 0) - w1
                            cost += ((u.get("input_tokens") or 0) * p[0] + w5 * p[0] * 1.25 + w1 * p[0] * 2 + (u.get("cache_read_input_tokens") or 0) * p[0] * 0.1 + (u.get("output_tokens") or 0) * p[1]) / 1e6
                    elif e.get("type") == "user":
                        c = m.get("content")
                        if isinstance(c, list):
                            if any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c):
                                continue
                            c = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
                        if not isinstance(c, str) or not c.strip() or c.lstrip().startswith("<"):
                            continue
                        if first_ts is None:
                            try:
                                first_ts = datetime.datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00"))
                            except Exception:
                                first_ts = start
                        turns.append(c)
            if len(turns) < 5 or not models or (first_ts and first_ts < start):
                continue
            dom = models.most_common(1)[0][0]
            if dom not in ("opus", "sonnet"):
                continue
            head = " ".join(turns[:3])
            task = next((k for k, p in TASK if p.search(head)), "other")
            flags = [bool(CORR.search(t)) for t in turns]
            streaks, run = 0, 0
            for fl in flags:
                run = run + 1 if fl else 0
                if run == 2:
                    streaks += 1
            g = groups[(task, dom)]
            g["sessions"] += 1
            g["turns"] += len(turns)
            g["corr"] += sum(flags)
            g["streaks"] += streaks
            g["usd"] += cost
            if dom == "sonnet" and streaks:
                worst.append((streaks, len(turns), turns[0].strip().replace("\n", " ")[:80]))
    return groups, worst

def render(groups, worst, days):
    lines = [f"Model quality, last {days} days (sessions with 5+ the owner turns, API-rate cost)", "",
             f"{'task':22} {'model':7} {'sess':>5} {'turns/s':>8} {'corr%':>6} {'bolak/s':>8} {'usd/turn':>9}"]
    tasks = sorted({t for t, _ in groups})
    verdicts = []
    for t in tasks:
        for dom in ("opus", "sonnet"):
            g = groups.get((t, dom))
            if not g:
                continue
            lines.append(f"{t:22} {dom:7} {g['sessions']:5} {g['turns'] / g['sessions']:8.1f} {100 * g['corr'] / g['turns']:6.1f} {g['streaks'] / g['sessions']:8.2f} {g['usd'] / g['turns']:9.2f}")
        o, s = groups.get((t, "opus")), groups.get((t, "sonnet"))
        if o and s and o["sessions"] >= 3 and s["sessions"] >= 3:
            d_streak = s["streaks"] / s["sessions"] - o["streaks"] / o["sessions"]
            d_corr = 100 * (s["corr"] / s["turns"] - o["corr"] / o["turns"])
            ok = d_streak <= 0.2 and d_corr <= 3
            verdicts.append(f"- {t}: Sonnet {'OK, keep' if ok else 'back to Opus'} (bolak-balik {d_streak:+.2f}, correction {d_corr:+.1f} pts)")
    lines.append("")
    lines.append("Verdict (rule: Sonnet stays only if bolak-balik within 0.2 and correction within 3 pts of Opus):")
    lines += verdicts or ["- not enough sessions on both models in any class this week"]
    if worst:
        lines.append("")
        lines.append("Sonnet sessions with the most back-and-forth:")
        for st, n, p in sorted(worst, reverse=True)[:5]:
            lines.append(f"- {st} streaks in {n} turns | {p}")
    return "\n".join(lines)

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--write", action="store_true", help="also write journal/reports/model_quality/<date>.md")
    a = ap.parse_args()
    groups, worst = scan(a.days)
    text = render(groups, worst, a.days)
    print(text)
    if a.write:
        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.abspath(os.path.join(here, "..", ".."))
        out_dir = os.path.join(root, "journal", "reports", "model_quality")
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, datetime.datetime.now(WIB).strftime("%Y-%m-%d") + ".md")
        with open(out, "w", encoding="utf-8") as f:
            f.write("# Model quality check\n\n```\n" + text + "\n```\n")
        print(f"\nwritten: {out}")

if __name__ == "__main__":
    main()
