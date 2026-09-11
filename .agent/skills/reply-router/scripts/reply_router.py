#!/usr/bin/env python3
"""Reply router: turns new Slack messages that need the owner's reply into
auto-drafted reply sessions, via the ASB branching protocol.

Reads the slack mention ledger (read-only), filters items that still need a
reply after a debounce window, and writes ONE branch request file per run to
.asb/branches/requests/. The app then creates one sub-session per CONVERSATION;
each sub-session drafts the reply and waits for the owner's approval. Nothing is
ever sent by this script or by the branch itself without approval.

Phase 2: one session per conversation, Slack-thread style. A conversation is a
Slack thread, a DM, or one person's top-level messages in one channel. While
that session is open, new messages in the same conversation are appended to its
thread file instead of opening a second session. The session closes when the
ledger says every message in it is answered or dismissed, or by hand with
`close`, which also writes the ASB status file so the app can retire the chat.

Cron entry (WSL automation host only):
  */5 * * * * cd <REPO> && python3 .agent/skills/reply-router/scripts/reply_router.py run >> /tmp/reply_router.log 2>&1
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
CONFIG_PATH = os.path.join(BASE_DIR, "journal", "state", "automation_config.json")
STATE_PATH = os.path.join(BASE_DIR, "journal", "state", "reply_router_state.json")
LEDGER_PATH = os.path.join(BASE_DIR, "journal", "state", "slack_mention_ledger.json")
REQUESTS_DIR = os.path.join(BASE_DIR, ".asb", "branches", "requests")
STATUS_DIR = os.path.join(BASE_DIR, ".asb", "branches", "status")
# The app's own "the user closed this chat" signal, written when a session (or a whole group) is
# marked done from the rail. It is the reverse of STATUS_DIR, which carries a session's report
# about itself, and it is the only way the router learns about a decision taken in the app: without
# it a conversation stays open here forever, later messages are appended to a thread file nobody
# will read again, and no new chat is ever opened for them.
CLOSED_DIR = os.path.join(BASE_DIR, ".asb", "branches", "closed")
THREADS_DIR = os.path.join(BASE_DIR, "journal", "state", "reply_threads")

WIB = timezone(timedelta(hours=7))

DEFAULT_CONFIG = {
    "auto_reply_drafts": {
        "enabled": True,
        "sources": {"slack": True, "gmail": False},
        "scope_kinds": ["dm", "mention", "thread_followup"],
        "debounce_minutes": 30,
        "max_per_hour": 10,
        "max_per_day": 30,
        "quiet_hours_wib": [0, 6],
        "muted_channels": [],
        "conversation_ttl_hours": 24,
    }
}

KIND_ORDER = {"dm": 0, "mention": 1, "thread_followup": 2}
MAX_BRANCHES_PER_REQUEST = 12
MAX_MESSAGES_PER_BRANCH = 8

def _load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

def _atomic_write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
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

def load_state():
    state = _load_json(STATE_PATH, None)
    if state is None:
        return None
    state.setdefault("processed", {})
    state.setdefault("conversations", {})
    return state

def conversation_key(item):
    """One key per conversation, not per message.

    A Slack thread is one conversation. A DM is one conversation. Top-level
    messages from one person in one channel are one conversation. This is the
    whole point of Phase 2: Fuad sending three DMs in a row is one chat to
    answer, not three sessions in the sidebar.
    """
    channel = item["channel"]
    if item.get("thread_ts"):
        return "{}:t{}".format(channel, item["thread_ts"])
    if item.get("kind") == "dm" or channel.startswith("D") or channel.startswith("U"):
        return "{}:dm".format(channel)
    return "{}:u{}".format(channel, item.get("author") or "unknown")

def legacy_thread_key(item):
    """Pre-Phase-2 per-message key. Still read so the old `processed` map keeps
    suppressing messages that already got a session before the upgrade."""
    return "{}:{}".format(item["channel"], item.get("thread_ts") or item["ts"])

def thread_file(conv_key):
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", conv_key)
    return os.path.join(THREADS_DIR, safe + ".md")

def wib_now():
    return datetime.now(WIB)

def fmt_wib(epoch):
    return datetime.fromtimestamp(float(epoch), WIB).strftime("%a %d %b %Y %H:%M WIB")

def _names(ledger):
    return ledger.get("channel_names", {}), ledger.get("user_names", {})

def describe(item, ledger):
    chan_names, user_names = _names(ledger)
    author = user_names.get(item.get("author"), item.get("author") or "unknown")
    cname = chan_names.get(item["channel"], item.get("channel_name") or item["channel"])
    is_dm = item.get("kind") == "dm" or item["channel"].startswith("D")
    where = "DM" if is_dm else "#" + cname
    return author, where

def candidates(ledger, cfg, state, now):
    items = ledger.get("items", {})
    if isinstance(items, dict):
        items = list(items.values())
    chan_names = ledger.get("channel_names", {})
    processed = state.get("processed", {})
    seen_ts = set()
    for conv in state.get("conversations", {}).values():
        for c, t in conv.get("messages", []):
            seen_ts.add((c, t))
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
        if legacy_thread_key(it) in processed:
            continue
        if (it["channel"], it["ts"]) in seen_ts:
            continue
        out.append(it)
    out.sort(key=lambda i: (not i.get("priority", False), KIND_ORDER.get(i["kind"], 9), float(i["ts"])))
    return out

def group_by_conversation(items):
    """Preserve the candidate ordering of the first message in each group."""
    groups = {}
    for it in items:
        groups.setdefault(conversation_key(it), []).append(it)
    for msgs in groups.values():
        msgs.sort(key=lambda i: float(i["ts"]))
    return groups

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
    for conv in state.get("conversations", {}).values():
        age = now - conv.get("opened_at", 0)
        if age < 3600:
            hour += 1
        if age < 86400:
            day += 1
    return hour, day

def quote(item):
    text = (item.get("text") or "").strip()
    body = text.replace("\n", "\n> ")[:1500] or "(empty message)"
    return "> " + body

def message_block(item, ledger):
    author, where = describe(item, ledger)
    return "- **{}**, {}{}\n  Permalink: {}\n\n{}\n".format(
        author, fmt_wib(item["ts"]),
        " (priority)" if item.get("priority") else "",
        item.get("permalink") or "(no permalink)",
        quote(item))

def append_to_thread_file(conv_key, items, ledger):
    path = thread_file(conv_key)
    os.makedirs(THREADS_DIR, exist_ok=True)
    new = not os.path.exists(path)
    with open(path, "a") as f:
        if new:
            author, where = describe(items[0], ledger)
            f.write("# Reply thread: {} ({})\n\n".format(author, where))
            f.write("Conversation key `{}`. The router appends every new message ".format(conv_key))
            f.write("in this conversation here. Read the whole file before drafting.\n\n")
        for it in items:
            f.write(message_block(it, ledger) + "\n")
    return path

def build_branch(conv_key, items, ledger, path):
    first = items[0]
    author, where = describe(first, ledger)
    snippet = (first.get("text") or "").strip()[:60].replace("\n", " ")
    title = "Reply: {} ({})".format(author, where) + (" - " + snippet if snippet else "")
    if len(items) > 1:
        title += " (+{} more)".format(len(items) - 1)

    messages = "\n".join(message_block(it, ledger) for it in items[:MAX_MESSAGES_PER_BRANCH])
    rel_path = os.path.relpath(path, BASE_DIR)

    brief = """Auto-drafted reply queue item (reply-router, Phase 2). One session per CONVERSATION. Draft a Slack reply for the owner's approval.

## Conversation
- With: {author}
- Where: {where} (channel id `{channel}`)
- Conversation key: `{conv}`
- Thread file (every message, including ones the router adds later): [{rel_path}]({abs_path})
- {n} message(s) waiting

{messages}

## First thing, before anything else
Register this session so the router can retire it when the conversation is done:

```bash
python3 .agent/skills/reply-router/scripts/reply_router.py claim --conv '{conv}' --session <your-session-id>
```

Your session id is in the message that opened this session. Without this the chat never leaves the owner's sidebar.

## What to do
1. Read the thread file above first: the router appends any newer message from this same conversation there instead of opening a second session. Then read thread context via the permalink (`slack_client.py --action history` or MCP Slack read tools) and the People page in `Clients/Work/People/`.
2. Draft ONE reply covering every waiting message, following `.agent/protocols/slack_send.md`: the owner's voice, English for Work, no-ai-slop pass, ceiling 80 words, handles resolved via `.agent/scripts/slack_mentions.py`.
3. Present it in the 3-part reply-draft format: (1) original message(s), (2) the draft, (3) plain-language pointers on what happened and what the owner has to do.
4. WAIT for the owner's explicit approval ("kirim"). Send only via `python3 .agent/skills/slack-connector/scripts/slack_client.py --action post --approved` (thread reply: `--thread-ts {thread_ts}`). NEVER send unapproved.
5. If the owner says no reply is needed, dismiss the mention: `python3 .agent/skills/slack-tracker/scripts/mention_ledger.py dismiss` (see its --help for args).
6. When finished, close the conversation. This writes the ASB status file for you, so do NOT hand-write it:

```bash
python3 .agent/skills/reply-router/scripts/reply_router.py close --conv '{conv}' --summary "<one line on what you did>"
```
""".format(
        author=author,
        where=where,
        channel=first["channel"],
        conv=conv_key,
        rel_path=rel_path,
        abs_path=path,
        n=len(items),
        messages=messages,
        thread_ts=first.get("thread_ts") or first["ts"],
    )
    return {
        "title": title[:90],
        "objective": "Review and approve a drafted reply to {} in {}".format(author, where),
        "brief": brief,
        "sourceRefs": ["slack:{}/p{}".format(it["channel"], it["ts"].replace(".", ""))
                       for it in items[:MAX_MESSAGES_PER_BRANCH]],
        # The app dedupes on this too, from 0.13.9: a request naming a conversation an open
        # session already handles becomes a follow-up into that session rather than a second
        # chat. Belt and braces with the routing above, which does the same thing one step
        # earlier, and the only guard on an older build or a hand-written request.
        "conversationKey": conv_key,
        "priority": "high" if any(it.get("priority") for it in items) else "normal",
    }

def write_status_file(session_id, summary):
    if not session_id:
        return None
    os.makedirs(STATUS_DIR, exist_ok=True)
    path = os.path.join(STATUS_DIR, "{}.json".format(session_id))
    _atomic_write(path, {"status": "done", "summary": summary})
    return path

def close_conversation(state, conv_key, summary, reason):
    conv = state["conversations"].get(conv_key)
    if not conv:
        return None, False
    conv["status"] = "done"
    conv["closed_at"] = time.time()
    conv["closed_reason"] = reason
    conv["summary"] = summary
    path = write_status_file(conv.get("session_id"), summary)
    return conv, bool(path)

def ledger_status_map(ledger):
    items = ledger.get("items", {})
    if isinstance(items, dict):
        items = list(items.values())
    return {(it["channel"], it["ts"]): it.get("status") for it in items}

def closed_by_app():
    """Session ids the app says the user closed, with the files that said so.

    Read once per run and consumed at the end, so a crash between reading and writing state
    replays the signal instead of dropping it."""
    out = {}
    try:
        names = os.listdir(CLOSED_DIR)
    except OSError:
        return out
    for name in names:
        if not name.endswith(".json"):
            continue
        path = os.path.join(CLOSED_DIR, name)
        data = _load_json(path, {})
        session_id = data.get("sessionId") or name[:-5]
        out[session_id] = path
    return out

def reap(state, ledger, now, cfg):
    """Close conversations the ledger says are finished, the ones the app closed, plus dead ones."""
    statuses = ledger_status_map(ledger)
    app_closed = closed_by_app()
    ttl = cfg.get("conversation_ttl_hours", 24) * 3600
    closed, orphaned = [], []
    for key, conv in state.get("conversations", {}).items():
        if conv.get("status") != "open":
            continue
        session_id = conv.get("session_id")
        if session_id and session_id in app_closed:
            _, wrote = close_conversation(
                state, key, "Closed from the app.", "app")
            closed.append((key, wrote))
            continue
        msgs = conv.get("messages", [])
        known = [statuses.get(tuple(m)) for m in msgs]
        if msgs and all(s in ("answered", "dismissed") for s in known if s is not None) \
                and any(s is not None for s in known):
            _, wrote = close_conversation(
                state, key, "All messages answered or dismissed in Slack.", "ledger")
            closed.append((key, wrote))
            continue
        if now - conv.get("last_activity_at", conv.get("opened_at", now)) > ttl:
            _, wrote = close_conversation(
                state, key, "Conversation went quiet past its TTL.", "ttl")
            closed.append((key, wrote))
    for key, conv in state.get("conversations", {}).items():
        if conv.get("status") != "open" and not conv.get("session_id"):
            orphaned.append(key)
    # Consumed only now, and only once the signal has nothing left to do: an id that still names
    # an OPEN conversation is one this run could not close (state was not saved, say), so its file
    # stays and the next run tries again.
    still_open = {
        c.get("session_id")
        for c in state.get("conversations", {}).values()
        if c.get("status") == "open"
    }
    for session_id, path in app_closed.items():
        if session_id in still_open:
            continue
        try:
            os.remove(path)
        except OSError:
            pass
    return closed, orphaned

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
    state = load_state()

    if state is None:
        # First activation: baseline every currently-open item so the backlog
        # does not explode into dozens of sessions. Only messages arriving
        # after this moment get drafted.
        state = {"baselined_at": now, "processed": {}, "conversations": {}}
        base_cfg = {**cfg, "muted_channels": [], "debounce_minutes": 0}
        for it in candidates(ledger, base_cfg, state, now):
            state["processed"][legacy_thread_key(it)] = {
                "reason": "baselined", "created_at": now, "ts": it["ts"]}
        if not args.dry_run:
            _atomic_write(STATE_PATH, state)
        print("[reply-router] first run: baselined {} open item(s), drafting nothing".format(
            len(state["processed"])))
        return 0

    closed, _ = reap(state, ledger, now, cfg)
    for key, wrote in closed:
        print("[reply-router] closed {}{}".format(
            key, "" if wrote else " (no session id claimed, chat stays in the sidebar)"))
    if closed and not args.dry_run:
        # persist now: a run with nothing new to dispatch returns early below
        _atomic_write(STATE_PATH, state)

    cands = candidates(ledger, cfg, state, now)
    groups = group_by_conversation(cands)

    # Messages that belong to a conversation whose session is still open go to
    # that session's thread file. No second chat in the sidebar.
    appended, fresh = [], []
    for key, msgs in groups.items():
        conv = state["conversations"].get(key)
        if conv and conv.get("status") == "open":
            appended.append((key, msgs))
        else:
            fresh.append((key, msgs))

    hour_n, day_n = dispatched_counts(state, now)
    room = min(MAX_BRANCHES_PER_REQUEST,
               max(0, cfg["max_per_hour"] - hour_n),
               max(0, cfg["max_per_day"] - day_n))
    take = fresh[:room]

    if not take and not appended:
        print("[reply-router] nothing to do")
        return 0

    if args.dry_run:
        for key, msgs in appended:
            print("[reply-router] DRY RUN: would append {} msg(s) to open conversation {}".format(
                len(msgs), key))
        print("[reply-router] DRY RUN: would open {} session(s):".format(len(take)))
        for key, msgs in take:
            author, where = describe(msgs[0], ledger)
            print("  - {} ({}) x{}".format(author, where, len(msgs)))
        if len(fresh) > len(take):
            print("  ...{} conversation(s) capped (hour {}/{}, day {}/{})".format(
                len(fresh) - len(take), hour_n, cfg["max_per_hour"], day_n, cfg["max_per_day"]))
        return 0

    for key, msgs in appended:
        path = append_to_thread_file(key, msgs, ledger)
        conv = state["conversations"][key]
        conv["messages"].extend([[m["channel"], m["ts"]] for m in msgs])
        conv["last_activity_at"] = now
        conv["pending_followups"] = conv.get("pending_followups", 0) + len(msgs)
        print("[reply-router] appended {} msg(s) to open conversation {} -> {}".format(
            len(msgs), key, os.path.relpath(path, BASE_DIR)))

    branches = []
    for key, msgs in take:
        path = append_to_thread_file(key, msgs, ledger)
        branches.append(build_branch(key, msgs, ledger, path))
        state["conversations"][key] = {
            "status": "open",
            "session_id": None,
            "opened_at": now,
            "last_activity_at": now,
            "messages": [[m["channel"], m["ts"]] for m in msgs],
            "thread_file": os.path.relpath(path, BASE_DIR),
            "pending_followups": 0,
        }

    if branches:
        request = {
            "version": 1,
            "reason": "Auto reply drafts: {} conversation(s) waiting on the owner (reply-router)".format(
                len(branches)),
            "branches": branches,
        }
        fname = "auto-reply-{}.json".format(int(now))
        os.makedirs(REQUESTS_DIR, exist_ok=True)
        _atomic_write(os.path.join(REQUESTS_DIR, fname), request)
        for key, _ in take:
            state["conversations"][key]["branch_file"] = fname
        print("[reply-router] wrote {} ({} session(s); hour {}/{}, day {}/{})".format(
            fname, len(branches), hour_n + len(branches), cfg["max_per_hour"],
            day_n + len(branches), cfg["max_per_day"]))
        for b in branches:
            print("  - " + b["title"])
    if len(fresh) > len(take):
        print("[reply-router] {} conversation(s) capped, next run picks them up".format(
            len(fresh) - len(take)))

    _atomic_write(STATE_PATH, state)
    return 0

def cmd_claim(args):
    state = load_state()
    if not state or args.conv not in state.get("conversations", {}):
        print("[reply-router] unknown conversation {}".format(args.conv))
        return 1
    state["conversations"][args.conv]["session_id"] = args.session
    state["conversations"][args.conv]["last_activity_at"] = time.time()
    _atomic_write(STATE_PATH, state)
    print("claimed {} -> session {}".format(args.conv, args.session))
    return 0

def cmd_close(args):
    state = load_state()
    if not state:
        print("[reply-router] no state file")
        return 1
    keys = []
    if args.all_answered:
        ledger = _load_json(LEDGER_PATH, {})
        closed, _ = reap(state, ledger, time.time(), load_config())
        keys = [k for k, _ in closed]
        if not keys:
            print("nothing to close")
    elif args.conv:
        if args.conv not in state["conversations"]:
            print("[reply-router] unknown conversation {}".format(args.conv))
            return 1
        conv, wrote = close_conversation(
            state, args.conv, args.summary or "Closed by hand.", "manual")
        keys = [args.conv]
        if not wrote:
            print("note: no session id claimed for {}, so the chat stays in the sidebar".format(
                args.conv))
    else:
        print("[reply-router] pass --conv <key> or --all-answered")
        return 1
    _atomic_write(STATE_PATH, state)
    for k in keys:
        print("closed {}".format(k))
    return 0

def cmd_list(args):
    state = load_state() or {"conversations": {}}
    ledger = _load_json(LEDGER_PATH, {})
    now = time.time()
    convs = state.get("conversations", {})
    rows = [(k, v) for k, v in convs.items()
            if args.all or v.get("status") == "open"]
    rows.sort(key=lambda kv: kv[1].get("opened_at", 0))
    if not rows:
        print("no open reply conversations")
        return 0
    for key, conv in rows:
        age = (now - conv.get("opened_at", now)) / 3600.0
        print("{:<8} {:<44} {:>5.1f}h  msgs={} followups={} session={}".format(
            conv.get("status", "?"), key, age, len(conv.get("messages", [])),
            conv.get("pending_followups", 0), conv.get("session_id") or "-"))
    unclaimed = sum(1 for _, c in rows if c.get("status") == "open" and not c.get("session_id"))
    if unclaimed:
        print("\n{} open conversation(s) never claimed a session id; those chats "
              "cannot be retired automatically.".format(unclaimed))
    return 0

def cmd_status(args):
    cfg = load_config()
    state = load_state() or {"processed": {}, "conversations": {}}
    now = time.time()
    hour_n, day_n = dispatched_counts(state, now)
    convs = state.get("conversations", {})
    open_convs = [c for c in convs.values() if c.get("status") == "open"]
    print("auto_reply_drafts: {}".format("ON" if cfg["enabled"] else "OFF"))
    print("  sources: slack={} gmail={}".format(cfg["sources"].get("slack"), cfg["sources"].get("gmail")))
    print("  scope: {}  debounce: {}m  caps: {}/h {}/d  quiet: {}".format(
        ",".join(cfg["scope_kinds"]), cfg["debounce_minutes"],
        cfg["max_per_hour"], cfg["max_per_day"], cfg.get("quiet_hours_wib")))
    print("  conversation ttl: {}h".format(cfg.get("conversation_ttl_hours")))
    print("  muted: {}".format(cfg["muted_channels"] or "(none)"))
    print("  dispatched: {} last hour, {} last 24h".format(hour_n, day_n))
    print("  conversations: {} open, {} closed, {} pending follow-up msg(s)".format(
        len(open_convs), len(convs) - len(open_convs),
        sum(c.get("pending_followups", 0) for c in open_convs)))
    if state.get("baselined_at"):
        print("  baselined: {} item(s) at {}".format(
            sum(1 for r in state["processed"].values() if r.get("reason") == "baselined"),
            fmt_wib(state["baselined_at"])))
    ledger = _load_json(LEDGER_PATH, {})
    if ledger:
        cands = candidates(ledger, cfg, state, now)
        groups = group_by_conversation(cands)
        print("  waiting now: {} message(s) in {} conversation(s)".format(
            len(cands), len(groups)))
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
    ls = sub.add_parser("list", help="open reply conversations")
    ls.add_argument("--all", action="store_true", help="include closed ones")
    cl = sub.add_parser("claim", help="record the session id for a conversation")
    cl.add_argument("--conv", required=True)
    cl.add_argument("--session", required=True)
    cc = sub.add_parser("close", help="finish a conversation and retire its chat")
    cc.add_argument("--conv")
    cc.add_argument("--summary")
    cc.add_argument("--all-answered", action="store_true",
                    help="close every conversation Slack already marked answered")
    for name in ("on", "off"):
        t = sub.add_parser(name, help="toggle master or one source")
        t.add_argument("--source", choices=["slack", "gmail"])
    args = p.parse_args()
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "list":
        return cmd_list(args)
    if args.cmd == "claim":
        return cmd_claim(args)
    if args.cmd == "close":
        return cmd_close(args)
    return cmd_toggle(args, args.cmd == "on")

if __name__ == "__main__":
    sys.exit(main())
