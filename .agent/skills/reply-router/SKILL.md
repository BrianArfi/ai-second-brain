---
name: reply-router
description: Auto reply drafts - turns new Slack messages that need the owner's reply into auto-drafted reply sessions via the ASB branching protocol. Cron on WSL reads the mention ledger, filters and debounces, then writes branch requests; each sub-session drafts the reply and waits for approval. Toggle with /autodraft. Plan - journal/plans/plan_auto_reply_drafts.md.
---

# Reply Router (auto reply drafts)

Every new Slack message that needs the owner's reply becomes its own session with a
draft already written, so the owner's job is review-and-approve. Phase 1 scope:
Slack only, new sessions only. Design and phases:
[`journal/plans/plan_auto_reply_drafts.md`](../../../journal/plans/plan_auto_reply_drafts.md).

## How it works

```
slack-push (seconds) + mention sweep (30m)
        -> journal/state/slack_mention_ledger.json      (existing)
        -> reply_router.py run   (cron */5, WSL only)
              filter: status=open, kind in scope, debounce 5m, not muted,
                      not already dispatched (thread_key), caps 10/h 30/d,
                      quiet hours 00-06 WIB held
        -> .asb/branches/requests/auto-reply-<ts>.json  (one file per run)
        -> app creates one sub-session per message; it drafts the 3-part
           reply and WAITS for "kirim". Nothing sends without approval.
```

If the owner replies himself before the debounce ends, the sweep marks the item
`answered` and the router skips it. First activation baselines the whole open
backlog and drafts nothing, so switching it on never floods the sidebar.

## Commands

```bash
RR=.agent/skills/reply-router/scripts/reply_router.py
python3 $RR status              # config, counters, candidates waiting
python3 $RR run [--dry-run]     # cron entry; dry-run shows what would dispatch
python3 $RR on|off              # master toggle (also via /autodraft)
python3 $RR on|off --source slack|gmail
```

Config lives in `journal/state/automation_config.json` (`auto_reply_drafts`
key): scope kinds, debounce, caps, quiet hours, `muted_channels`. State
(dedupe + counters) in `journal/state/reply_router_state.json`. Neither is one
of the four locked ledgers; the router is their single writer (WSL cron), and
writes are atomic.

Kill switch: `AUTO_REPLY_DRAFTS_DISABLE=1` in the crontab environment.

## Activation (once, on the WSL automation host only)

```cron
*/5 * * * * cd . && python3 .agent/skills/reply-router/scripts/reply_router.py run >> /tmp/reply_router.log 2>&1
```

Never install this on a second machine (CLAUDE.md: one automation host).

## Not in Phase 1

- Re-trigger of an existing session when a thread gets a new message (Phase 2,
  `reply_sessions.json`).
- Gmail (Phase 3; `--source gmail` exists in config but the router only reads
  the Slack mention ledger today).
- The ASB app Settings toggle (app-side change; the config file is ready for it).
