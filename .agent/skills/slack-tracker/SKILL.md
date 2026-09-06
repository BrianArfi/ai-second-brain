---
name: slack-tracker
description: Maps Slack channels to clients and runs the stateful Mention Ledger sweep, so no mention, thread reply, or DM goes unanswered. Use for the Slack half of any inbox sweep.
---

# Slack Tracker Skill

This skill manages the mapping of Slack channels to specific clients and teams AND runs the stateful **Mention Ledger** sweep — the system that guarantees no mention of the owner, thread reply, or DM slips through unanswered.

## Mention Ledger (primary tool since Jul 2026)

`scripts/mention_ledger.py` — stateful, 3-layer sweep. Replaces reliance on the old runner's 5-msgs-per-channel skim, which structurally missed old-thread replies, unsearched mentions, and anything beyond the last 5 messages.

**Layer 1 — Collector (pure Python, cron `*/30`, no LLM):**
- `search.messages <@<SLACK_ID>>` catches mentions of the owner ANYWHERE (all channels, thread replies, DMs) — search indexes thread replies, so old-thread activity is caught.
- Watermark sweep of ALL joined conversations (`conversations.history oldest=<last_ts>`) — every message since the last sweep, no cap.
- Every non-the owner DM message becomes a ledger item automatically.
- **Mechanical reply-state**: an item secondarys to `answered` only when the owner actually replied after it (same thread / channel) or ack-reacted. Ack reactions (the owner's decision): ✅ ☑️ 👍 👌 — 👀 does NOT count, item stays open.
- **Noise auto-dismiss** (`is_noise`, conservative): bot/notifier messages (`bot_id`, or a known-app author in `NOISE_AUTHORS` e.g. Google Calendar / Slackbot) and short pure-acknowledgment closers ("thanks", "sure no worries", "yes exactly", "it is done") are auto-dismissed so they never reach the queue. Never fires on a question, a link-only message, or a bare `^` ping — false-dismiss risk is kept near zero (a bit of residual noise beats hiding a real ask). Runs on new items AND re-checks the existing backlog each sweep. Add new notifier app user-IDs to `NOISE_AUTHORS` as they appear.
- **Thread-participation pass** (`sweep_thread_replies`, added 4 Sep 2026): `conversations.history` returns thread **roots only, never replies**. So a reply inside a thread the owner is in, written by somebody who did not type his handle, was invisible to every other layer at once: not a mention (search greps for `<@yourhandle>`), not a DM, and not in history. `state['threads']` already existed and was written in two places, but the ONLY code that read it was `prune()`, which deleted it. Rohit Salaria answered a question of the owner's on 2 Sep 2026 this way and no sweep saw it for two days; Raouf Cherkawi's sign-off on the SAIB journey went the same way. The pass walks the thread registry, fetches replies, and raises anything landing **after the owner's last message** in a thread **he has posted in**. Scope is narrow on purpose: a thread with no the owner message is somebody else's conversation and is marked `participating: false` so it is never fetched again. Three related traps, all now covered by `tests/test_slack_thread_pass.py`:
  - the owner's **own** thread roots were never registered, because `sweep_channels` hits `continue` on his messages before the registration line. That is precisely the case that broke: he asks, they answer, nothing sees it.
  - `prune()` dropped every thread with no OPEN item, which deleted exactly the threads worth watching (a thread the owner answered has no open item, so the NEXT reply would be missed all over again). It now keeps a participating thread until `THREAD_IDLE_DAYS` of silence.
  - The `THREAD_REPLY_MAX_CALLS` cap starves the tail if threads are sorted by activity alone, because a checked thread gets fresh activity and stays on top. Never-checked threads are served first, then the least-recently-checked.
  Backfill: `sweep --reseed-threads` registers every thread the owner posted in over the last 14 days. It runs itself once, when `thread_pass_seeded` is absent.
- **Pointer enrichment** (`enrich_pointers`): a bare `^`, `:point_up:`, or a shared permalink carries no standalone ask — the real request is in the message it points to. For every open pointer/link-only item the collector fetches the substantive predecessor (or resolves the permalink target, chasing the chain up to 2 hops) and stores it as `context`, which `report` prints as `↳ re:`. This is why a bare ping is KEPT not dismissed: e.g. Ali's "@yourhandle ^" resolved to the real ask "scope the OMS↔ExampleProgram Jahez Redemption API (integrate get-card-balance)". Without enrichment that task would have been invisible.
- State: `journal/state/slack_mention_ledger.json` (items persist across days until answered/dismissed). Digest for GLM: `journal/state/slack_sweep_digest.jsonl`.

**Layer 2 — Classifier (GLM via agy-bridge, cheap):** `mention_ledger.py classify` batches the digest + open items through `--task harvest` (GLM/Gemini chain) → needs_reply / action_item / meeting_input / fyi / noise + urgency. GLM never decides answered/open — that stays mechanical.

**Layer 3 — Surface (Claude, morning/evening updates):** `mention_ledger.py report` prints the "🔴 Waiting on your reply" markdown (priority authors first — any YourManager message is 🔥, then newest). The daily updates embed this and the owner can `mention_ledger.py dismiss <item_id>` anything handled offline.

```bash
python3 .agent/skills/slack-tracker/scripts/mention_ledger.py sweep     # cron does this
python3 .agent/skills/slack-tracker/scripts/mention_ledger.py sweep --reseed-threads   # re-register the owner's recent threads
python3 tests/test_slack_thread_pass.py                                 # regression: the thread pass
python3 .agent/skills/slack-tracker/scripts/mention_ledger.py report    # for briefings
python3 .agent/skills/slack-tracker/scripts/mention_ledger.py classify  # GLM triage of digest
python3 .agent/skills/slack-tracker/scripts/mention_ledger.py dismiss <SLACK_ID>:1783574754.502969
```

Cron (installed): `*/30 * * * * flock -n /tmp/mention_ledger.lock python3 .../mention_ledger.py sweep >> .../ledger_cron.log`

Gotchas: needs the **user token** (xoxp — `search.messages` never works with a bot token); a full sweep takes ~3-4 min on ~100 conversations (paced for rate limits); first run looks back 3 days (mentions) / 24h (channels) / 14 days (thread seeding) to avoid flooding. **Never "fix" the sweep by deleting from `state['threads']` on the basis that a thread has no open item** — that is the exact regression the thread pass exists to prevent.

## Usage (channel mapping)
- Call this skill to retrieve the list of channels for a specific client (e.g., Work, Secondary).
- Use the IDs in `channels.json` for all Slack MCP tool calls.

## Configuration
The `channels.json` file is the source of truth for channel mappings.

## Adding/Removing Channels
- To add a channel: Update `channels.json` with the new channel ID and name under the appropriate client/team.
- To remove a channel: Delete the entry from `channels.json` only when explicitly requested by the user.

## Client Mappings
### Work
- **Platform**: Infrastructure, Logistics, Core Services.
- **Marketplace**: Regional programs (ExampleCo, Kantar, MasterCard, ExampleClient).
- **E-Comm**: Seller Portal, B2C Super App.

### Secondary
- **Ops Platform**: Internal tools, automation.
