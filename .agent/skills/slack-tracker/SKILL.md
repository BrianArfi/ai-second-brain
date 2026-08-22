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
- **Pointer enrichment** (`enrich_pointers`): a bare `^`, `:point_up:`, or a shared permalink carries no standalone ask — the real request is in the message it points to. For every open pointer/link-only item the collector fetches the substantive predecessor (or resolves the permalink target, chasing the chain up to 2 hops) and stores it as `context`, which `report` prints as `↳ re:`. This is why a bare ping is KEPT not dismissed: e.g. Ali's "@yourhandle ^" resolved to the real ask "scope the OMS↔ExampleProgram Jahez Redemption API (integrate get-card-balance)". Without enrichment that task would have been invisible.
- State: `journal/state/slack_mention_ledger.json` (items persist across days until answered/dismissed). Digest for GLM: `journal/state/slack_sweep_digest.jsonl`.

**Layer 2 — Classifier (GLM via agy-bridge, cheap):** `mention_ledger.py classify` batches the digest + open items through `--task harvest` (GLM/Gemini chain) → needs_reply / action_item / meeting_input / fyi / noise + urgency. GLM never decides answered/open — that stays mechanical.

**Layer 3 — Surface (Claude, morning/evening updates):** `mention_ledger.py report` prints the "🔴 Waiting on your reply" markdown (priority authors first — any YourManager message is 🔥, then newest). The daily updates embed this and the owner can `mention_ledger.py dismiss <item_id>` anything handled offline.

```bash
python3 .agent/skills/slack-tracker/scripts/mention_ledger.py sweep     # cron does this
python3 .agent/skills/slack-tracker/scripts/mention_ledger.py report    # for briefings
python3 .agent/skills/slack-tracker/scripts/mention_ledger.py classify  # GLM triage of digest
python3 .agent/skills/slack-tracker/scripts/mention_ledger.py dismiss <SLACK_ID>:1783574754.502969
```

Cron (installed): `*/30 * * * * flock -n /tmp/mention_ledger.lock python3 .../mention_ledger.py sweep >> .../ledger_cron.log`

Gotchas: needs the **user token** (xoxp — `search.messages` never works with a bot token); a full sweep takes ~3-4 min on ~100 conversations (paced for rate limits); first run looks back 3 days (mentions) / 24h (channels) to avoid flooding.

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
