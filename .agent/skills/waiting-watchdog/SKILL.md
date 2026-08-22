---
name: waiting-watchdog
description: Stateful SLA watchdog for things the owner is waiting on from other people (owner + what + SLA hours). Local age-vs-SLA sweep flags breaches; an optional --check-slack pass reads the source Slack thread for an owner reply. Surfaces a briefing-ready report with escalation lines first.
---

# Waiting-on Watchdog

Tracks everything the owner is **waiting on someone else for** (a decision, a doc, an answer,
an approval) with an SLA in hours. Unlike the mention ledger (which tracks things others
are waiting on the owner for), this is the mirror: things the owner is waiting on others for.

## Capabilities

- `add` a waiting-on item with an owner, what's expected, and an SLA in hours.
- `sweep` (cron, hourly, **pure local, zero-network**): compares `since` age against
  `sla_hours`. Anything over SLA and still open secondarys to `status=breached`.
- `sweep --check-slack` (**SOP-only, not cron'd** — Claude runs this during morning/evening
  update): for items with a Slack source permalink, reads `conversations.replies` on the
  source thread and auto-closes (`status=answered`) if anyone other than the owner replied
  after `since`.
- `report [--all]`: briefing-ready markdown. Breached items first (🚨 ESCALATE lines),
  then open items with an SLA countdown. `--all` also includes answered/dropped.
- `close <id> [--note "..."]` / `drop <id> [--note "..."]`: manual close (owner delivered /
  no longer needed). **Always pass `--note`.** It stores the reason on the record as
  `close_note`. Without it the record keeps only a status and a timestamp, so a later
  session cannot tell whether the thing was delivered, reassigned, or abandoned, and the
  only way back is to re-derive it from Slack. `--note` is optional so the cron sweeps that
  call `close` unattended keep working.
- `touch <id>`: record a nudge — resets `since` to now (SLA clock restarts) and stamps
  `last_nudge_at`.

## Usage

```bash
# add a new waiting-on item
python3 .agent/skills/waiting-watchdog/scripts/waiting_watchdog.py add \
  --owner "Teammate" --what "ExampleRetailer contract redline sign-off" \
  --sla-hours 24 --escalate-to "YourManager" --escalation-path "Slack DM" \
  --source "https://yourcompany.slack.com/archives/C0XXXX/p1234567890123456"

# hourly cron sweep (local only, no network)
python3 .agent/skills/waiting-watchdog/scripts/waiting_watchdog.py sweep

# SOP-run sweep that also checks Slack threads for owner replies
python3 .agent/skills/waiting-watchdog/scripts/waiting_watchdog.py sweep --check-slack

# briefing report (embed verbatim in morning/evening updates)
python3 .agent/skills/waiting-watchdog/scripts/waiting_watchdog.py report

# full history including closed items
python3 .agent/skills/waiting-watchdog/scripts/waiting_watchdog.py report --all

# owner delivered -> close; no longer needed -> drop; sent a nudge -> touch
# always say why, with the evidence
python3 .agent/skills/waiting-watchdog/scripts/waiting_watchdog.py close WAIT-0001 \
  --note "Delivered 30 Jul 12:41 UTC: Teammate posted the filtered sheet to Teammate (slack ts 1785415269.452499)"
python3 .agent/skills/waiting-watchdog/scripts/waiting_watchdog.py drop  WAIT-0002
python3 .agent/skills/waiting-watchdog/scripts/waiting_watchdog.py touch WAIT-0001
```

## Notes

- State: `journal/state/waiting_on.json`. Item schema (`WAIT-NNNN`):
  `{id, owner, owner_slug, what, since, sla_hours, escalate_to, escalation_path,
  source{type, permalink}, status: open|breached|answered|dropped, breached_at,
  last_nudge_at, first_seen, closed_at, close_note, notes, needs_escalation}`.
  `close_note` is written by `close --note` / `drop --note` and is the only place the
  reason for a close survives. Records closed before 11 Aug 2026 do not have one.
- `needs_escalation` (bool): set by `sweep` on every run for `status=breached` items
  where `last_nudge_at` is `None` or older than `breached_at` -- i.e. breached with no
  nudge sent since the breach. Cleared (`false`) by `touch`/`close`/`drop`/`reopen`, or
  once the item leaves breached status. This is the field briefings/dashboard filter on
  to catch silent breaches; it is recomputed every sweep, not just on the transition into
  `breached`, so an old breach that's still un-nudged keeps surfacing. **No auto-send is
  triggered by this flag** -- it only marks the item for a human (the owner, via
  morning/evening update) to decide on and nudge manually via `touch`.
  `sweep` also prints one `🚨 BREACH NEEDS ESCALATION:` line per such item to stdout/cron
  log, in addition to the existing summary line, so breaches are visible in
  `waiting_watchdog_cron.log` even between SOP-run `report` reads.
- Retention: `answered`/`dropped` items are pruned 14 days after `closed_at`.
- Cron design (not installed by this skill — install is an integration-stage step):
  `7 * * * * flock -n /tmp/waiting_watchdog.lock python3 <abs>/waiting_watchdog.py sweep >> .agent/skills/waiting-watchdog/waiting_watchdog_cron.log 2>&1`.
  Hourly, local-only sweep has zero Slack rate-limit impact. `--check-slack` is
  intentionally **excluded from cron** — it's SOP-run only (morning/evening update),
  per the plan's "LLM/network out of mechanical cron" rule.
- `--check-slack` reuses the `SLACK_USER_TOKEN` from `.agent/skills/slack-connector/token.env`
  (same auth as the mention ledger). It parses the source `permalink` to get channel + thread
  ts, then treats any non-the owner message in that thread posted after `since` as a reply —
  conservative by design (doesn't try to match the specific owner's Slack user ID, since
  that mapping isn't guaranteed at add-time).
- `touch` is for a manual nudge you've sent the owner: it resets the SLA clock (`since=now`)
  rather than closing the item, since the ask still isn't delivered.
- Heartbeat: `sweep` appends one row to `dashboard-data/agent_heartbeat.jsonl` via
  `.agent/scripts/heartbeat.py` (best-effort; missing heartbeat script does not fail sweep).
- This skill's `tickets.json`/mention-ledger/fathom-registry access is **read-only never**
  in this version — it only reads its own state file and (optionally) Slack. Integration
  wiring (dashboard route, workflow embeds, cron install, seed data) happens in a later
  serialized integration stage, not here.
