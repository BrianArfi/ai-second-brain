---
name: reply-router
description: Auto reply drafts - turns new Slack messages that need the owner's reply into auto-drafted reply sessions via the ASB branching protocol. One session per CONVERSATION (Slack-thread style), not per message. The ASB app runs it on its own ticker: it reads the mention ledger, filters and debounces, then writes branch requests; each sub-session drafts the reply and waits for approval. Toggle with /autodraft. Plan - journal/plans/plan_auto_reply_drafts.md.
---

# Reply Router (auto reply drafts)

Every Slack CONVERSATION that needs the owner's reply becomes one session with a
draft already written, so the owner's job is review-and-approve. Phase 2 scope:
Slack only, one session per conversation, and conversations that close
themselves. Design and phases:
[`journal/plans/plan_auto_reply_drafts.md`](../../../journal/plans/plan_auto_reply_drafts.md).

## How it works

```
slack-push (seconds) + mention sweep (30m)
        -> journal/state/slack_mention_ledger.json      (existing)
        -> reply_router.py run   (ASB app ticker, auto_drafts.rs, every 5 min)
              reap:   close conversations Slack already answered or dismissed,
                      and ones quiet past the TTL -> .asb/branches/status/<id>.json
              filter: status=open, kind in scope, debounce 30m, not muted,
                      not already seen, caps 10/h 30/d, quiet hours 00-06 WIB
              group:  by CONVERSATION, never by message
              route:  conversation already has an open session -> append to its
                      thread file; otherwise -> one new branch
        -> .asb/branches/requests/auto-reply-<ts>.json  (one file per run)
        -> app creates one sub-session per conversation; it claims its session
           id, drafts the 3-part reply and WAITS for "kirim". Nothing sends
           without approval.
```

## A conversation, not a message

The dedupe key is the conversation, so three DMs in a row from Fuad are one
chat to answer, not three chats in the sidebar. The key is:

| Shape | Key |
| :--- | :--- |
| Slack thread | `<channel>:t<thread_ts>` |
| DM | `<channel>:dm` |
| Top-level messages in a channel | `<channel>:u<author>` |

While a conversation's session is open, later messages in it are appended to
`journal/state/reply_threads/<key>.md` and no second session opens. The session
brief points at that file and tells the session to read it before drafting.

Every branch request also carries the key as `conversationKey`. **Nothing in the
app reads it** (checked against 0.13.11: no `conversationKey` anywhere in the
source), so the routing above is the ONLY thing keeping one conversation to one
chat. It travels on the request anyway, as the record of which conversation a
chat belongs to and as the hook an app-side second net would use. A note here
used to claim ASB 0.13.9 deduped on it; it never did.

## Closing, so the sidebar shrinks

Four closers. The first three write `.asb/branches/status/<session-id>.json`,
which is what retires the chat in the app:

1. **The session closes itself** when it is finished:
   `reply_router.py close --conv <key> --summary "<one line>"`.
2. **The ledger closes it** on the next run: once every message in the
   conversation is `answered` or `dismissed` in Slack, `reap` closes it.
3. **The TTL closes it** after `conversation_ttl_hours` (24h) of silence.
4. **the owner closes it in the app**, by marking the chat or its whole day group
   done in the rail. That is the one decision the router cannot see, so the app
   writes `.asb/branches/closed/<session-id>.json` and `reap` reads that
   directory and closes the matching conversation with reason `app`. Without
   it, the record here stayed open forever, every later message in that Slack
   conversation was appended to a thread file nobody would open again, and no
   fresh chat was ever created for them.

A session that never ran `claim` has no session id on record, so the router
cannot retire its chat and says so in the run output. That is why `claim` is
step one of every brief.

## One group per day, and closing the group

From ASB 0.13.11 the app does not hang every reply off one "Auto reply drafts"
chat. That chat is a container, and under it sits one container per DAY; the
replies are children of the day. The rail folds every day but today, so a
sidebar that used to grow by dozens of rows a day grows by one.

Marking a group done in the app is a decision about everything inside it: the
app asks first, marks the children done (a running one is left alone and
named), and archives the container. An archived container is skipped when the
next reply arrives, so a new group is created rather than the closed one being
reopened, and the archived group and its replies are deleted a week later.

None of that is router state. The router learns about it through the `closed/`
signal above, which is why that closer exists.

If the owner replies himself before the debounce ends, the sweep marks the item
`answered` and the router skips it. First activation baselines the whole open
backlog and drafts nothing, so switching it on never floods the sidebar.

## Commands

```bash
RR=.agent/skills/reply-router/scripts/reply_router.py
python3 $RR status              # config, counters, conversations waiting
python3 $RR health              # one-line verdict; exit 2 = off while work waits
python3 $RR run [--dry-run]     # cron entry; dry-run shows what would dispatch
python3 $RR list [--all]        # open reply conversations, age, session id
python3 $RR claim --conv K --session ID     # sub-session registers itself
python3 $RR close --conv K --summary "..."  # finish one; writes the status file
python3 $RR close --all-answered            # finish every one Slack answered
python3 $RR on|off              # master toggle (also via /autodraft)
python3 $RR on|off --source slack|gmail
```

Config lives in `journal/state/automation_config.json` (`auto_reply_drafts`
key): scope kinds, debounce, caps, quiet hours, `conversation_ttl_hours`,
`muted_channels`.

**A toggle reaches the routine only through git.** The config path is resolved
from the script's own location, so secondaryping a switch in one checkout writes that
checkout's file, while the routine runs on whichever machine has the app open. A
toggle written and never pushed is applied here and invisible there. `on`, `off`
and `status` print the resolved path, the machine, and a warning when the file is
uncommitted or unpushed. Being off was silent until 14 Sep 2026: `run` returned
"off (config)" to a log nobody reads, so a toggle that missed its target hid for
eleven days and 88 messages.

**Every limit now publishes what it is holding.** The router writes the shared
`held` block on every run, one entry per limit that held work back: `switch_off`,
`cap_hour`, `cap_day`, `quiet_hours`, `debounce`, `muted`. Each entry says how
many items, whether they are held or dropped, which setting did it, and when the
work comes back. `settings_schema.json` next to this file declares the same
limits for the Settings pane. `health` exits 2 when something needs a human, and
step 0c of the morning update banners it. The contract, the escalation window,
and the CLI: [`docs/harness_reference.md#automation-limits`](../../../docs/harness_reference.md#automation-limits).
Contract test: `python3 tests/test_automation_settings.py`. State (conversations + counters) in `journal/state/reply_router_state.json`. Neither is one
of the four locked ledgers; the router is their single writer, and writes are
atomic.

Kill switch: `/autodraft off`, or `AUTO_REPLY_DRAFTS_DISABLE=1` in the
environment.

## Activation: the app hosts it. There is nothing to schedule.

The router has no scheduler entry and needs none. The ASB app runs it itself,
from `src-tauri/src/auto_drafts.rs`: a ticker every 5 minutes while the app is
open, which runs `reply_router.py run` and then drains the requests it wrote
into that day's group in the rail. The only switch is
`auto_reply_drafts.enabled` in `journal/state/automation_config.json`, which is
the same key `/autodraft on` and the Settings row write.

**Never add this to a crontab, and never give it a `runner: app` row either.**
Both have now been tried and both were the same mistake in different clothes.
It was a WSL cron line until 11 Sep 2026 that was never installed, so the
router sat idle with 23 messages waiting while `/autodraft status` reported ON.
The fix that day added a scheduler row, `*/15 7-23 WIB`, without noticing that
the app had hosted the job natively since 3 Sep. That ran the same script twice:
one model turn every fifteen minutes for work already done, and a stray
top-level chat each day beside the group it belonged in. The row is disabled as
of 17 Sep 2026, and from app 0.16.2 the scheduler refuses it on its own with
`hosted by the app's auto reply drafts ticker` in the Routines pane.

The lesson worth keeping: nothing running is the symptom of a disabled feature
AND of an unscheduled one, and reaching for a scheduler is how the second host
gets built. Check `auto_reply_drafts.enabled` first, and check which host
already exists before adding one.

Two limits to know. The ticker only runs while the app is open, so hours with
every machine shut produce no drafts and there is no catch-up for them. And the
app open on two machines at once means two tickers over one
`reply_router_state.json`, which has no lock: `processed` keys dedupe within a
machine, git dedupes across machines only as fast as it syncs.

Regression test: `python3 tests/test_reply_router_conversations.py`.

## Not in Phase 2

- Waking a running session when its conversation gets a new message. The
  router appends to the thread file; the session reads it when the owner opens it.
- Gmail (Phase 3; `--source gmail` exists in config but the router only reads
  the Slack mention ledger today).
- The ASB app Settings toggle (app-side change; the config file is ready for it).
