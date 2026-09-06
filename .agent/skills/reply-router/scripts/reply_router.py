#!/usr/bin/env python3
"""Reply router: turns new Slack messages that need the owner's reply into
auto-drafted reply sessions, via the ASB branching protocol.

Reads the slack mention ledger (read-only), filters items that still need a
reply after a debounce window, and writes ONE branch request file per run to
.asb/branches/requests/. The app then creates one sub-session per item; each
sub-session drafts the reply (3-part format) and waits for the owner's approval.
Nothing is ever sent by this script or by the branch itself without approval.

Phase 1: Slack only, new sessions only (no re-trigger of old sessions yet).
Cron entry (WSL automation host only):
  */5 * * * * cd <REPO> && python3 .agent/skills/reply-router/scripts/reply_router.py run >> /tmp/reply_router.log 2>&1
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
CONFIG_PATH = os.path.join(BASE_DIR, "journal", "state", "automation_config.json")
STATE_PATH = os.path.join(BASE_DIR, "journal", "state", "reply_router_state.json")
LEDGER_PATH = os.path.join(BASE_DIR, "journal", "state", "slack_mention_ledger.json")
REQUESTS_DIR = os.path.join(BASE_DIR, ".asb", "branches", "requests")

WIB = timezone(timedelta(hours=7))

DEFAULT_CONFIG = {
    "auto_reply_drafts": {
        "enabled": True,
        "sources": {"slack": True, "gmail": False},
        "scope_kinds": ["dm", "mention", "thread_followup"],
        "debounce_minutes": 5,
        "max_per_hour": 10,
        "max_per_day": 30,
        "quiet_hours_wib": [0, 6],
        "muted_channels": [],
    }
}

KIND_ORDER = {"dm": 0, "mention": 1, "thread_followup": 2}
MAX_BRANCHES_PER_REQUEST = 12

def _load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

def _atomic_write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)

def load_config():
    cfg = _load_json(CONFIG_PATH, None)
    if cfg is None:
        cfg = DEFAULT_CONFIG
        _atomic_write(CONFIG_PATH, cfg)
    # fill missing keys from defaults without clobbering user values
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    merged["auto_reply_drafts"].update(cfg.get("auto_reply_drafts", {}))
    return merged["auto_reply_drafts"]

def save_config(section):
    cfg = _load_json(CONFIG_PATH, {})
    cfg["auto_reply_drafts"] = section
    _atomic_write(CONFIG_PATH, cfg)

def thread_key(item):
    return "{}:{}".format(item["channel"], item.get("thread_ts") or item["ts"])

def wib_now():
    return datetime.now(WIB)

def fmt_wib(epoch):
    return datetime.fromtimestamp(float(epoch), WIB).strftime("%a %d %b %Y %H:%M WIB")

def candidates(ledger, cfg, state, now):
    items = ledger.get("items", {})
    if isinstance(items, dict):
        items = list(items.values())
    chan_names = ledger.get("channel_names", {})
    processed = state.get("processed", {})
    debounce = cfg["debounce_minutes"] * 60
    out = []
    for it in items:
        if it.get("status") != "open":
            continue
        if it.get("kind") not in cfg["scope_kinds"]:
            continue
        cname = chan_names.get(it["channel"], it.get("channel_name") or it["channel"])
        if cname in cfg["muted_channels"] or ("#" + cname) in cfg["muted_channels"]:
            continue
        if now - float(it["ts"]) < debounce:
            continue
        if thread_key(it) in processed:
            continue
        out.append(it)
    out.sort(key=lambda i: (not i.get("priority", False), KIND_ORDER.get(i["kind"], 9), float(i["ts"])))
    return out

def dispatched_counts(state, now):
    hour = day = 0
    for rec in state.get("processed", {}).values():
        if rec.get("reason") == "baselined":
            continue
        age = now - rec.get("created_at", 0)
        if age < 3600:
            hour += 1
        if age < 86400:
            day += 1
    return hour, day

def build_branch(item, ledger):
    chan_names = ledger.get("channel_names", {})
    user_names = ledger.get("user_names", {})
    author = user_names.get(item.get("author"), item.get("author") or "unknown")
    cname = chan_names.get(item["channel"], item.get("channel_name") or item["channel"])
    is_dm = item.get("kind") == "dm" or item["channel"].startswith("D")
    where = "DM" if is_dm else "#" + cname
    text = (item.get("text") or "").strip()
    snippet = text[:60].replace("\n", " ")
    title = "Reply: {} ({})".format(author, where) + (" - " + snippet if snippet else "")

    brief = """Auto-drafted reply queue item (reply-router, Phase 1). Draft a Slack reply for the owner's approval.

## Original message
- From: {author}
- Where: {where} (channel id `{channel}`)
- When: {when}
- Kind: {kind}{prio}
- Permalink: {permalink}

> {quoted}

## What to do
1. Read the thread context first via the permalink history if needed (`slack_client.py --action history` or MCP Slack read tools). Check People page in `Clients/Work/People/` for who {author} is.
2. Draft the reply following `.agent/protocols/slack_send.md`: the owner's voice, English for Work, no-ai-slop pass, ceiling 80 words, handles resolved via `.agent/scripts/slack_mentions.py`.
3. Present it in the 3-part reply-draft format: (1) original message, (2) the draft, (3) plain-language pointers on what happened and what the owner has to do.
4. WAIT for the owner's explicit approval ("kirim"). Send only via `python3 .agent/skills/slack-connector/scripts/slack_client.py --action post --approved` (thread reply: `--thread-ts {thread_ts}`). NEVER send unapproved.
5. If the owner says no reply is needed, dismiss the mention: `python3 .agent/skills/slack-tracker/scripts/mention_ledger.py dismiss` (see its --help for args). If the reply is sent, the next sweep marks it answered automatically; no ledger edit needed.
6. When finished, write `.asb/branches/status/<your-session-id>.json` with {{"status": "done", "summary": "<one line>"}}.
""".format(
        author=author,
        where=where,
        channel=item["channel"],
        when=fmt_wib(item["ts"]),
        kind=item.get("kind"),
        prio=" (priority)" if item.get("priority") else "",
        permalink=item.get("permalink") or "(no permalink)",
        quoted=text.replace("\n", "\n> ")[:1500] or "(empty message)",
        thread_ts=item.get("thread_ts") or item["ts"],
    )
    return {
        "title": title[:90],
        "objective": "Review and approve a drafted reply to {} in {}".format(author, where),
        "brief": brief,
        "sourceRefs": ["slack:{}/p{}".format(item["channel"], item["ts"].replace(".", ""))],
        "priority": "high" if item.get("priority") else "normal",
    }

def cmd_run(args):
    if os.environ.get("AUTO_REPLY_DRAFTS_DISABLE") == "1":
        print("[reply-router] disabled via AUTO_REPLY_DRAFTS_DISABLE=1")
        return 0
    cfg = load_config()
    if not cfg["enabled"] or not cfg["sources"].get("slack"):
        print("[reply-router] off (config)")
        return 0
    qh = cfg.get("quiet_hours_wib") or []
    now_wib = wib_now()
    if len(qh) == 2 and qh[0] <= now_wib.hour < qh[1]:
        print("[reply-router] quiet hours ({}:00-{}:00 WIB), holding".format(qh[0], qh[1]))
        return 0

    ledger = _load_json(LEDGER_PATH, {})
    if not ledger:
        print("[reply-router] no mention ledger at {}".format(LEDGER_PATH))
        return 1
    now = time.time()
    state = _load_json(STATE_PATH, None)

    if state is None:
        # First activation: baseline every currently-open item so the backlog
        # does not explode into dozens of sessions. Only messages arriving
        # after this moment get drafted.
        state = {"baselined_at": now, "processed": {}}
        base_cfg = {"scope_kinds": cfg["scope_kinds"], "muted_channels": [],
                    "debounce_minutes": 0}
        base_cfg = {**cfg, **base_cfg}
        for it in candidates(ledger, base_cfg, state, now):
            state["processed"][thread_key(it)] = {
                "reason": "baselined", "created_at": now, "ts": it["ts"]}
        if not args.dry_run:
            _atomic_write(STATE_PATH, state)
        print("[reply-router] first run: baselined {} open item(s), drafting nothing".format(
            len(state["processed"])))
        return 0

    cands = candidates(ledger, cfg, state, now)
    hour_n, day_n = dispatched_counts(state, now)
    room = min(MAX_BRANCHES_PER_REQUEST,
               max(0, cfg["max_per_hour"] - hour_n),
               max(0, cfg["max_per_day"] - day_n))
    take = cands[:room]
    if not take:
        if cands:
            print("[reply-router] {} candidate(s) waiting, capped (hour {}/{}, day {}/{})".format(
                len(cands), hour_n, cfg["max_per_hour"], day_n, cfg["max_per_day"]))
        else:
            print("[reply-router] nothing to do")
        return 0

    branches = [build_branch(it, ledger) for it in take]
    request = {
        "version": 1,
        "reason": "Auto reply drafts: {} new Slack message(s) waiting on the owner (reply-router)".format(len(branches)),
        "branches": branches,
    }
    fname = "auto-reply-{}.json".format(int(now))
    fpath = os.path.join(REQUESTS_DIR, fname)

    if args.dry_run:
        print("[reply-router] DRY RUN: would write {} with {} branch(es):".format(fpath, len(branches)))
        for b in branches:
            print("  - " + b["title"])
        return 0

    os.makedirs(REQUESTS_DIR, exist_ok=True)
    _atomic_write(fpath, request)
    for it in take:
        state["processed"][thread_key(it)] = {
            "reason": "dispatched", "created_at": now, "ts": it["ts"],
            "branch_file": fname}
    _atomic_write(STATE_PATH, state)
    print("[reply-router] wrote {} ({} branch(es); hour {}/{}, day {}/{})".format(
        fpath, len(branches), hour_n + len(branches), cfg["max_per_hour"],
        day_n + len(branches), cfg["max_per_day"]))
    for b in branches:
        print("  - " + b["title"])
    return 0

def cmd_status(args):
    cfg = load_config()
    state = _load_json(STATE_PATH, {"processed": {}})
    now = time.time()
    hour_n, day_n = dispatched_counts(state, now)
    print("auto_reply_drafts: {}".format("ON" if cfg["enabled"] else "OFF"))
    print("  sources: slack={} gmail={}".format(cfg["sources"].get("slack"), cfg["sources"].get("gmail")))
    print("  scope: {}  debounce: {}m  caps: {}/h {}/d  quiet: {}".format(
        ",".join(cfg["scope_kinds"]), cfg["debounce_minutes"],
        cfg["max_per_hour"], cfg["max_per_day"], cfg.get("quiet_hours_wib")))
    print("  muted: {}".format(cfg["muted_channels"] or "(none)"))
    print("  dispatched: {} last hour, {} last 24h, {} total".format(
        hour_n, day_n,
        sum(1 for r in state.get("processed", {}).values() if r.get("reason") == "dispatched")))
    if state.get("baselined_at"):
        print("  baselined: {} item(s) at {}".format(
            sum(1 for r in state["processed"].values() if r.get("reason") == "baselined"),
            fmt_wib(state["baselined_at"])))
    ledger = _load_json(LEDGER_PATH, {})
    if ledger:
        cands = candidates(ledger, cfg, state, now)
        print("  waiting now: {} candidate(s)".format(len(cands)))
    return 0

def cmd_toggle(args, enabled):
    cfg = load_config()
    if args.source:
        cfg["sources"][args.source] = enabled
    else:
        cfg["enabled"] = enabled
    save_config(cfg)
    print("auto_reply_drafts {} {}".format(
        "source " + args.source if args.source else "master",
        "ON" if enabled else "OFF"))
    return 0

def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="cron entry: dispatch new reply-draft sessions")
    r.add_argument("--dry-run", action="store_true")
    sub.add_parser("status", help="show config + counters")
    for name in ("on", "off"):
        t = sub.add_parser(name, help="toggle master or one source")
        t.add_argument("--source", choices=["slack", "gmail"])
    args = p.parse_args()
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "status":
        return cmd_status(args)
    return cmd_toggle(args, args.cmd == "on")

if __name__ == "__main__":
    sys.exit(main())
