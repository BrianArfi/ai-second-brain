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
import contextlib
import json
import os
import re
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
CONFIG_PATH = os.path.join(BASE_DIR, "journal", "state", "automation_config.json")
STATE_PATH = os.path.join(BASE_DIR, "journal", "state", "reply_router_state.json")
LEDGER_PATH = os.path.join(BASE_DIR, "journal", "state", "slack_mention_ledger.json")
REQUESTS_DIR = os.path.join(BASE_DIR, ".asb", "branches", "requests")
STATUS_DIR = os.path.join(BASE_DIR, ".asb", "branches", "status")

# `reply_router_state.json` is the only thing that remembers a conversation was
# already dispatched, and until 18 Sep 2026 nothing serialised the runs that
# write it. cmd_run persisted at the END of the run, so a run slow enough to
# still be working when the next 5-minute tick fired handed that tick a state
# file with none of its work in it. The tick then re-dispatched the same
# conversations, and its own write landed on top. That is how 21:54 and 21:59
# WIB on 18 Sep produced two identical 7-conversation batches
# (auto-reply-1789744476.json and ...777.json): fourteen sessions in the owner's
# sidebar for seven replies. Same read-modify-write race as the 3 Aug ledger
# loss, same fix.
#
# The lock is per checkout, because LOCK_DIR sits under the repo root. Two
# checkouts running their own cron would still duplicate; nothing local can
# stop that, and the guard for it is the state file itself travelling through
# git. What this closes is the overlap within one checkout, which is what
# actually happened.
RUN_LOCK = "reply_router"
RUN_LOCK_TIMEOUT = 30.0

_SCRIPTS_DIR = os.path.join(BASE_DIR, ".agent", "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
try:
    from ledger_lock import ledger_lock
except Exception:                                   # pragma: no cover
    ledger_lock = None

@contextlib.contextmanager
def router_lock(skip_if_busy=False, timeout=RUN_LOCK_TIMEOUT):
    """Serialise every command that writes the router state.

    `skip_if_busy` is for `run`, which cron fires every five minutes: a tick
    that finds the lock held has nothing to add, so it says so and exits 0
    rather than queueing behind a run whose work it would only repeat.
    Interactive commands (`claim`, `close`) wait instead, because their caller
    is a session that has already done the work and needs the write to land.
    """
    if ledger_lock is None:
        yield True
        return
    # Entered by hand rather than with `with`, so a TimeoutError raised by the
    # body cannot be mistaken for the lock being busy.
    cm = ledger_lock(RUN_LOCK, timeout=0.0 if skip_if_busy else timeout)
    try:
        cm.__enter__()
    except TimeoutError:
        if not skip_if_busy:
            raise
        print("[reply-router] another run still holds the lock, skipping this tick")
        yield False
        return
    try:
        yield True
    finally:
        cm.__exit__(None, None, None)

# The app creates sub-sessions from the checkout IT runs in, and `.asb/` is gitignored, so a
# request written here never reaches a different checkout. The router runs on the WSL automation
# host while the app is usually open on Windows, which left 33 branch requests unread from 14 to
# 15 Sep 2026 and no drafts for the owner to open. So mirror every request into the other checkouts of
# this repo that exist on this machine. Override or extend with REPLY_ROUTER_REQUEST_MIRRORS
# (os.pathsep separated repo roots).
SIBLING_CHECKOUTS = [
    ".",
    "/mnt/c/Users/you/.gemini/antigravity/scratch/product-second-brain",
    ".",
    "C:/Users/you/.gemini/antigravity/scratch/product-second-brain",
]

def request_dirs():
    """Every .asb/branches/requests dir a request should land in, deduped, this checkout first.

    REQUESTS_DIR pointed anywhere other than this checkout's own is an explicit
    target, so honour it alone and mirror nowhere. A test redirects it to a temp
    dir to keep its fixtures out of the live queue, and mirroring defeated that:
    running `tests/test_reply_router_conversations.py` put a real request in front
    of the app, which opens sub-sessions for conversations nobody asked about.
    """
    default = os.path.join(BASE_DIR, ".asb", "branches", "requests")
    if os.path.realpath(REQUESTS_DIR) != os.path.realpath(default):
        os.makedirs(REQUESTS_DIR, exist_ok=True)
        return [REQUESTS_DIR]

    roots = [BASE_DIR]
    roots += [p for p in os.environ.get("REPLY_ROUTER_REQUEST_MIRRORS", "").split(os.pathsep) if p]
    roots += SIBLING_CHECKOUTS
    dirs, seen = [], set()
    for root in roots:
        # A real checkout, not a stale path: CLAUDE.md is the cheapest proof.
        if not os.path.isfile(os.path.join(root, "CLAUDE.md")):
            continue
        d = os.path.join(root, ".asb", "branches", "requests")
        key = os.path.realpath(d)
        if key in seen:
            continue
        seen.add(key)
        dirs.append(d)
    return dirs
# The app's own "the user closed this chat" signal, written when a session (or a whole group) is
# marked done from the rail. It is the reverse of STATUS_DIR, which carries a session's report
# about itself, and it is the only way the router learns about a decision taken in the app: without
# it a conversation stays open here forever, later messages are appended to a thread file nobody
# will read again, and no new chat is ever opened for them.
CLOSED_DIR = os.path.join(BASE_DIR, ".asb", "branches", "closed")
THREADS_DIR = os.path.join(BASE_DIR, "journal", "state", "reply_threads")

sys.path.insert(0, os.path.join(BASE_DIR, ".agent", "scripts"))
from automation_settings import (  # noqa: E402
    describe_held, held_record, is_escalated, load_schemas, merge_held,
    next_hour_boundary, next_wib_midnight, parent_held, quiet_hours_clear_at)

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
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

def _atomic_write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
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

def _git(*args):
    try:
        out = subprocess.run(["git", "-C", BASE_DIR] + list(args),
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None

def config_provenance():
    """Where this toggle landed, and whether the machine running the routine can see it.

    The config path is resolved from this script's own location, and the ASB
    routine runs on whichever machine has the app open. So a toggle secondaryped in
    one checkout only reaches the routine through git. Written and never pushed,
    it reads as applied here and stays invisible everywhere else, which is a
    switch that looks on while nothing runs. Print the path and the git state
    instead of leaving that to be discovered days later.
    """
    rel = os.path.relpath(CONFIG_PATH, BASE_DIR)
    lines = ["  config: {}".format(CONFIG_PATH),
             "  machine: {}".format(socket.gethostname())]
    if _git("rev-parse", "--git-dir") is None:
        lines.append("  NOT A GIT CHECKOUT: no way to reach the machine running the routine.")
        return lines
    if _git("status", "--porcelain", "--", rel):
        lines.append("  NOT COMMITTED YET: this setting is local to this checkout. "
                     "Other machines keep the old one until it is committed and pushed.")
    elif _git("log", "--oneline", "origin/main..HEAD", "--", rel):
        lines.append("  NOT PUSHED YET: committed here but not on origin/main. "
                     "Other machines keep the old one until it is pushed.")
    return lines

def waiting_now(cfg):
    """Messages the router would be acting on, whether or not it is switched on."""
    ledger = _load_json(LEDGER_PATH, {})
    state = load_state()
    if not ledger or state is None:
        return 0, 0
    cands = candidates(ledger, cfg, state, time.time())
    return len(cands), len(group_by_conversation(cands))

def off_switch(cfg):
    """Which switch is holding the router back, or None when it is fully on."""
    if not cfg["enabled"]:
        return "master"
    if not cfg["sources"].get("slack"):
        return "source slack"
    return None

def record_held(records, usage=None, state=None, persist=True):
    """Publish what this run is holding, in the shape every reader understands.

    Printing it in the cron log is not surfacing it. The log is only read once
    somebody already suspects a problem, and the whole failure mode here is that
    nobody suspects anything.

    Written on every run, including runs holding nothing, so a stale block can
    never report a problem that ended days ago.
    """
    own = state if state is not None else load_state()
    if own is None:
        return None
    own["held"] = merge_held(own.get("held"), records)
    if usage is not None:
        own["usage"] = usage
    if persist:
        _atomic_write(STATE_PATH, own)
    return own

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

def candidates(ledger, cfg, state, now, stats=None):
    """Items due for a draft. `stats` collects what was filtered out and why.

    The filters below are limits the user set, so what they remove is not noise,
    it is work the user is entitled to know about: muted drops it for good,
    debounce only delays it.
    """
    if stats is not None:
        stats.setdefault("muted", 0)
        stats.setdefault("debounce", 0)
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
            if stats is not None:
                stats["muted"] += 1
            continue
        if now - float(it["ts"]) < debounce:
            if stats is not None:
                stats["debounce"] += 1
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

# The drafting session quotes this back to the owner as part 1 of the reply draft,
# and it is the only copy of the original it sees. Cut it and the reply answers
# half the ask, with nothing on the page to show that half is missing. So the
# limit is generous, and a cut says so out loud.
QUOTE_LIMIT = 6000

def name_mentions(text):
    """`<@<SLACK_ID>>` means nothing on the page. Show who it is.

    the owner reads the quoted original away from Slack, where nothing renders the id
    for him, so a thread of bare ids hides who was addressed and who was talked
    about. An id with no name in the index is left alone rather than guessed at.
    """
    try:
        sys.path.insert(0, os.path.join(BASE_DIR, ".agent", "scripts"))
        from slack_mentions import expand_mentions
        return expand_mentions(text, plain=True)[0]
    except Exception:
        return text            # a missing index must never cost us the message

def quote(item):
    text = (item.get("text") or "").strip()
    if not text:
        return "> (empty message)"
    if len(text) > QUOTE_LIMIT:
        text = text[:QUOTE_LIMIT] + "\n[... cut here, open the permalink for the rest]"
    return "> " + name_mentions(text).replace("\n", "\n> ")

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
    with open(path, "a", encoding="utf-8") as f:
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
- Thread file (every message, including ones the router adds later): `{rel_path}`, relative to the repo root of the session reading this. Read it with that relative path, never with an absolute one: the router and the session can sit in different checkouts of this repo, and an absolute path from the router's machine is dead in the session's.
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
2. Draft ONE reply covering every waiting message, following `.agent/protocols/slack_send.md`: the owner's voice, English for Work, no-ai-slop pass, ceiling 80 words. Save it as `journal/drafts/<name>_<YYYY-MM-DD>.md`, never `.txt`. Resolve handles with `.agent/scripts/slack_mentions.py check`, then `expand --file <draft> --in-place` so every mention reads `<@<SLACK_ID>|Teammate Dev Singh>` and the owner can see who the message addresses.
3. Present it in the 3-part reply-draft format: (1) the original message(s) quoted with sender, time and permalink, (2) the draft as it would be sent, (3) plain-language pointers on what happened and what the owner has to do. Quote the original in full. A quote that ends mid-sentence came from an old truncated ledger record, so pull the real text with `slack_client.py --action history` before you draft, and never show the owner a cut original.
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
    """Mirrored into every checkout, for the same reason requests are.

    `.asb/` is gitignored, and the app reads the checkout IT runs in. A close
    run from the WSL host wrote the status only there, so the chat it was
    retiring stayed in the sidebar on Windows where the owner was looking at it.
    """
    if not session_id:
        return None
    written = []
    for d in status_dirs():
        try:
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, "{}.json".format(session_id))
            _atomic_write(path, {"status": "done", "summary": summary})
            written.append(path)
        except OSError as exc:
            print("[reply-router] could not write status to {}: {}".format(d, exc))
    return written[0] if written else None

def status_dirs():
    """`request_dirs()` for status files: this checkout first, then the others."""
    default = os.path.join(BASE_DIR, ".asb", "branches", "status")
    if os.path.realpath(STATUS_DIR) != os.path.realpath(default):
        return [STATUS_DIR]
    return [os.path.join(os.path.dirname(os.path.dirname(d)), "status")
            for d in request_dirs()]

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
    """Serialised entry point. The work is in `_run`; this only holds the lock."""
    with router_lock(skip_if_busy=True) as acquired:
        if not acquired:
            return 0
        return _run(args)

def _run(args):
    if os.environ.get("AUTO_REPLY_DRAFTS_DISABLE") == "1":
        print("[reply-router] disabled via AUTO_REPLY_DRAFTS_DISABLE=1")
        return 0
    cfg = load_config()
    which = off_switch(cfg)
    if which:
        # Off with nobody waiting and off with a backlog are the same silence
        # from the outside, and that is exactly what hid a failed toggle for
        # eleven days. Say which one this is, every run.
        msgs, convs = waiting_now(cfg)
        if msgs:
            print("[reply-router] OFF ({}) while {} message(s) in {} conversation(s) "
                  "wait. No drafts are being written. Switch it back on with: "
                  "python3 .agent/skills/reply-router/scripts/reply_router.py on".format(
                      which, msgs, convs))
        else:
            print("[reply-router] off ({}), nothing waiting".format(which))
        if not args.dry_run:
            record_held([held_record("switch_off", convs, "conversation",
                                     setting="enabled" if which == "master" else "sources")])
        return 0
    qh = cfg.get("quiet_hours_wib") or []
    now_wib = wib_now()
    if len(qh) == 2 and qh[0] <= now_wib.hour < qh[1]:
        msgs, convs = waiting_now(cfg)
        print("[reply-router] quiet hours ({}:00-{}:00 WIB), holding {} conversation(s)".format(
            qh[0], qh[1], convs))
        if not args.dry_run:
            record_held([held_record("quiet_hours", convs, "conversation",
                                     setting="quiet_hours_wib",
                                     clears_at=quiet_hours_clear_at(qh))])
        return 0

    ledger = _load_json(LEDGER_PATH, {})
    if not ledger:
        print("[reply-router] no mention ledger at {}".format(LEDGER_PATH))
        return 1
    now = time.time()
    state = load_state()

    # Superseded by the shared `held` block on 14 Sep 2026. Drop it on sight so
    # an old state file cannot keep reporting a condition that already ended.
    if state is not None:
        state.pop("off_with_backlog", None)

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

    filtered = {}
    cands = candidates(ledger, cfg, state, now, stats=filtered)
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

    # What this run is holding, and which setting holds it. Built even when
    # nothing is capped, because a block written only on bad runs goes stale the
    # moment the run is good again and then reports a problem that has ended.
    held = []
    capped = len(fresh) - len(take)
    hour_room = max(0, cfg["max_per_hour"] - hour_n)
    day_room = max(0, cfg["max_per_day"] - day_n)
    if capped:
        if day_room <= hour_room and day_room <= MAX_BRANCHES_PER_REQUEST:
            held.append(held_record("cap_day", capped, "conversation",
                                    setting="max_per_day",
                                    clears_at=next_wib_midnight(now)))
        elif hour_room <= MAX_BRANCHES_PER_REQUEST:
            held.append(held_record("cap_hour", capped, "conversation",
                                    setting="max_per_hour",
                                    clears_at=next_hour_boundary(now)))
        # else the per-request batch size binds, which the next tick clears by
        # itself within minutes. It is printed below; a held block would only
        # add noise to a condition that never lasts.
    if filtered.get("debounce"):
        held.append(held_record("debounce", filtered["debounce"], "message",
                                setting="debounce_minutes",
                                clears_at=now + cfg["debounce_minutes"] * 60))
    if filtered.get("muted"):
        held.append(held_record("muted", filtered["muted"], "message",
                                setting="muted_channels"))
    usage = {"dispatched_this_hour": hour_n, "dispatched_today": day_n}

    if not take and not appended:
        # "nothing to do" and "everything is held" are different facts, and
        # printing the first when the second is true is the same lie that hid
        # the switch being off.
        if held:
            for rec in held:
                print("[reply-router] nothing dispatched: " + describe_held(rec, now=now))
        else:
            print("[reply-router] nothing to do")
        if not args.dry_run:
            record_held(held, usage, state=state)
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
        for rec in held:
            print("  would hold: " + describe_held(rec, now=now))
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

    # Persist the dispatch BEFORE the request files exist. The lock already
    # keeps two runs apart; this keeps a run that dies while writing the
    # request (or mirroring it into a checkout that is not mounted) from
    # leaving the conversation looking untouched to the next tick.
    if appended or take:
        _atomic_write(STATE_PATH, state)

    if branches:
        request = {
            "version": 1,
            "reason": "Auto reply drafts: {} conversation(s) waiting on the owner (reply-router)".format(
                len(branches)),
            "branches": branches,
        }
        fname = "auto-reply-{}.json".format(int(now))
        written = []
        for d in request_dirs():
            try:
                os.makedirs(d, exist_ok=True)
                _atomic_write(os.path.join(d, fname), request)
                written.append(d)
            except OSError as exc:
                print("[reply-router] could not write to {}: {}".format(d, exc))
        if len(written) > 1:
            print("[reply-router] mirrored to {} checkout(s): {}".format(
                len(written), ", ".join(written[1:])))
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
    for rec in held:
        print("[reply-router] " + describe_held(rec, now=now))

    # Recount AFTER dispatching. The figure taken before the run is the room
    # this run had, not the room the next one gets, and publishing it would show
    # "0 of 10 used" on the very run that consumed all ten.
    used_hour, used_day = dispatched_counts(state, now)
    record_held(held, {"dispatched_this_hour": used_hour,
                       "dispatched_today": used_day}, state=state)
    return 0

def cmd_claim(args):
    with router_lock():
        return _claim(args)

def _claim(args):
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
    with router_lock():
        return _close(args)

def _close(args):
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
    # Immediately under the switch it contradicts. An ON that cannot fire is
    # worse than an OFF, because it stops the reader looking any further.
    schema = load_schemas().get("reply-router")
    gate = parent_held(schema) if schema else None
    if gate:
        print("  CANNOT RUN: {}".format(describe_held(gate)))
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
    waiting = 0
    if ledger:
        cands = candidates(ledger, cfg, state, now)
        groups = group_by_conversation(cands)
        waiting = len(cands)
        print("  waiting now: {} message(s) in {} conversation(s)".format(
            len(cands), len(groups)))
    usage = state.get("usage") or {}
    if usage:
        print("  used: {} of {} this hour, {} of {} today".format(
            usage.get("dispatched_this_hour", 0), cfg["max_per_hour"],
            usage.get("dispatched_today", 0), cfg["max_per_day"]))
    for rec in state.get("held", []):
        if rec.get("items"):
            print("  {}{}".format(describe_held(rec, now=now),
                                  "   [NEEDS ATTENTION]" if is_escalated(rec, now) else ""))
    for line in config_provenance():
        print(line)
    which = off_switch(cfg)
    if which and waiting:
        print("\nOFF ({}) with {} message(s) waiting. Nothing is being drafted.".format(
            which, waiting))
    return 0

def cmd_health(args):
    """One line for the morning update. Exit 2 means off while work is waiting.

    Separate from `status` because a caller needs a verdict it can branch on,
    not ten lines it has to parse.
    """
    cfg = load_config()
    # Before anything about this automation's own switches: is anything running
    # it at all? Its own switch reading ON under a stopped scheduler is the most
    # misleading answer available, so it can never be the first one given.
    schema = load_schemas().get("reply-router")
    gate = parent_held(schema) if schema else None
    if gate:
        print("auto reply drafts cannot run: " + describe_held(gate))
        return 2
    which = off_switch(cfg)
    msgs, convs = waiting_now(cfg)
    if which and msgs:
        print("auto reply drafts are OFF ({}) while {} message(s) in {} "
              "conversation(s) wait".format(which, msgs, convs))
        return 2
    if which:
        print("auto reply drafts are off ({}), nothing waiting".format(which))
        return 0
    # On, so the question becomes what the limits are holding. A cap that has
    # held the same work past the escalation window is a backlog that never
    # drains, not a burst being smoothed.
    now = time.time()
    holding = [r for r in (load_state() or {}).get("held", []) if r.get("items")]
    escalated = [r for r in holding if is_escalated(r, now)]
    if escalated:
        print("auto reply drafts on, but " + "; ".join(
            describe_held(r, now=now) for r in escalated))
        return 2
    tail = ("; " + "; ".join(describe_held(r, now=now) for r in holding)) if holding else ""
    print("auto reply drafts on, {} message(s) waiting{}".format(msgs, tail))
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
    for line in config_provenance():
        print(line)
    return 0

def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="cron entry: dispatch new reply-draft sessions")
    r.add_argument("--dry-run", action="store_true")
    sub.add_parser("status", help="show config + counters")
    sub.add_parser("health", help="one-line verdict; exit 2 = off while work waits")
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
    if args.cmd == "health":
        return cmd_health(args)
    if args.cmd == "list":
        return cmd_list(args)
    if args.cmd == "claim":
        return cmd_claim(args)
    if args.cmd == "close":
        return cmd_close(args)
    return cmd_toggle(args, args.cmd == "on")

if __name__ == "__main__":
    sys.exit(main())
