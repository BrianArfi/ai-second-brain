---
name: premeeting-cards
description: Generates auto-brief "pre-meeting cards" for upcoming Work calendar events — a pure mechanical join across the calendar, people roster, MTG-* tickets, Fathom registry, and the open-items ledgers (mention ledger, decisions, commitments, waiting-on watchdog). No LLM calls.
---

# Pre-meeting Cards

Auto-brief card per upcoming meeting so the owner walks in knowing: who's in the room, when he last met them, what he owes them, what they owe him, any open decisions tied to them, unanswered Slack pings from them, and any related MTG-* prep ticket.

**Portfolio discipline (added 27 Jul 2026).** Cards used to join open items on ATTENDEE alone. Because Work invites are wide, a Marketplace sprint review inherited every open item its attendees carried, including Platform and E-Commerce Solution work. Cards now gate on **both** attendee membership and the item's `portfolio` field (written by `.agent/scripts/portfolio_tagger.py`). Run the tagger before the cards whenever ledger items were added, or new items land as `unknown` and fall into the "Other portfolios" section.

This is a **pure mechanical join** — no LLM calls, no network writes apart from the optional read-only Jira sprint fetch. It reads six local/connector sources and joins them by simple rules (token overlap, name/email match). All source files except the calendar connector are read-with-fallback: if a sibling ledger doesn't exist yet (or fails to parse), that section of the card just renders empty — never crashes.

## Capabilities

- `generate [--date YYYY-MM-DD]` — pulls Work calendar events for that WIB date (default: today) via `gcal_manager.py list --days-back 0 --days-forward 1 --profile work --json`, and for each event writes a card to `journal/premeeting/<date>/<HHMM>_<slug>.md` joining:
  - **Attendees** — resolved against `journal/state/people.json` (owned by the `stakeholders` component). See Gotchas below — resolution is best-effort.
  - **Last time we met** — `journal/fathom_registry.json`, participant-name overlap (preferred) or event-title token overlap (fallback), highest-scoring match with score >= 2.
  - **Portfolio** — "Storefront" is ambiguous by design: Marketplace owns the storefront **instances** (ExampleCo/Example Program, ExampleClient, MCM...), E-Commerce Solution owns the storefront **product** (Builder, API, Analytics). `resolve_storefront()` decides from context and yields nothing when the title says only "Storefront", which lands the card in `unclassified` rather than a wrong portfolio. Inferred from the event TITLE only (never the attendee list) against `PORTFOLIO_TITLE_HINTS` then the topic aliases in `.agent/scripts/portfolio_tagger.py`. May resolve to several portfolios ("B2C + SP + PIM" -> `b2c, ecom-solution`). If nothing matches, the card says `unclassified` and **no portfolio filtering is applied**, so an unrecognised meeting never renders an empty card.
  - **You owe them** — open items in `journal/state/commitments.json` where `to_slug` matches an attendee **and** `portfolio` matches the meeting's portfolio.
  - **They owe you** — open/breached items in `journal/state/waiting_on.json` where `owner_slug` matches an attendee **and** `portfolio` matches the meeting's portfolio.
  - **Sprint board** — only for sprint/backlog/refinement/grooming meetings that resolve to exactly ONE portfolio. Calls `jira_client.py sprint-status --portfolio <id>` and renders the active sprint: issue counts by status, the heaviest assignee load, and open issues untouched for over a week (linked). Degrades to a one-line "Unavailable" on a missing token or a slow board; never fails the run.
  - **Other portfolios** — a collapsed section listing the attendee-matched items that were filtered OUT, with their portfolio tag. Items are separated, never deleted, so nothing silently disappears.
  - **Open decisions** — open items in `journal/state/decisions.json` where an attendee slug is in `stakeholder_slugs`.
  - **Unanswered pings** — open items in `journal/state/slack_mention_ledger.json` authored by an attendee's `slack_id`.
  - **Related tickets** — `journal/state/tickets.json` MTG-* tickets sharing >=2 significant tokens with the event title.
  - Idempotent: rerunning `generate` for the same date clears and rewrites that date's card files and its `journal/state/premeeting.json` entry — no duplicates.
  - Prunes card date-directories older than 14 days on every run.
- `report [--date YYYY-MM-DD]` — briefing-ready markdown index of that date's cards (one line per meeting with flag counts), meant to be embedded verbatim into the morning update.

## Usage

```bash
# generate cards for today (WIB) — cron does this
python3 .agent/skills/premeeting-cards/scripts/premeeting_cards.py generate

# generate for a specific date
python3 .agent/skills/premeeting-cards/scripts/premeeting_cards.py generate --date 2026-07-14

# briefing-ready index for the morning update
python3 .agent/skills/premeeting-cards/scripts/premeeting_cards.py report
```

State: `journal/state/premeeting.json` (`dates.<YYYY-MM-DD>.cards[]` = metadata per generated card, plus `last_run`).
Cards: `journal/premeeting/<YYYY-MM-DD>/<HHMM>_<slug>.md`.

### Enrichment companion (agy-bridge / GLM script)

`generate` only produces the mechanical join, which renders empty whenever the calendar payload has no resolvable attendees (a bare "Placeholder" event joins to nothing). To turn those shells into walk-in briefs, run the companion script after the final `generate` of the day:

```bash
python3 .agent/skills/premeeting-cards/scripts/enrich_cards_agy.py --date YYYY-MM-DD
```

It selects substantive Work meetings (skips Home/Prayer/Focus and attendee-less self-blocks), runs the live-status check, hunts + verifies the driving docs, and has GLM write each card as `## 🎯 Goal · ## 📌 What this is · ## ✅ Drive in the room · ## ⚠️ Watch · ## 🔗 Sources · ## 🧾 Open items` (em-dash free, every source linked). The mechanical join is preserved underneath in a `<details>` block as the audit trail. Flags: `--dry-run`, `--regenerate`, `--force-glm`. Wired into `.agent/workflows/morning-update.md` step "New Ledgers & Cards" item 1. Re-running `generate` wipes enriched cards, so always enrich AFTER the last generate.

**Engine (2026-07-17):** enrichment ALWAYS runs on agy-bridge, never Claude (the owner's standing rule). Python gathers, GLM writes, and that split is load-bearing: GLM has no tool access, so links/Sources are assembled and verified on disk in Python and only verified paths are passed in. Handing the model a topic and asking for URLs is where hallucinated paths land. Honors the `fallback_to_claude` sentinel (exit 3) and fails closed, keeping the mechanical card rather than shipping a half-written one. Note agy-bridge `time_routing` demotes glm-5.2 in peak WIB hours and answers with Gemini; use `--force-glm` if GLM specifically is required.

**`enrich_meeting_cards.workflow.js` is RETIRED** (now `_retired_enrich_meeting_cards.workflow.js`). It fanned out Claude subagents and required per-run Workflow opt-in, so non-interactive runs silently skipped it and left cards mentah.

**Live-status:** the calendar is checked FIRST and is authoritative (wired in 28 Jul 2026, `calendar_status()`). It reads the event's own `status` plus each attendee's `responseStatus`, so it reports `CANCELLED on the calendar` when the organiser kills the event and `on, but attendee declined` when someone RSVPs no. the owner's own decline is ignored, since that is his choice rather than a warning about the room. This is what caught Teammate Chennupati declining the 18:15 ABC-123 scoping on 28 Jul, which the Slack check had reported as `unknown`.

**Remaining caveat:** when the calendar says nothing, the Slack heuristic is the fallback and `unknown` still means UNCHECKED, not "on". It only fires when a message names the meeting or lands in a channel whose name does. It still misses a key attendee DM-ing "I won't be able to join today" while leaving the invite accepted (how YourManager cancelled the 16 Jul Weekly PMO; verified 17 Jul, still `unknown` at a 48h window).

**Auto-run (cron, no manual trigger):** a headless twin of this workflow runs daily via the dashboard `/api/ai-task` kind `premeeting-enrich` (`dashboard/server.py`), same as `inbox-digest`. Crontab: `45 12 * * 1-5` POSTs `{kind:"premeeting-enrich"}` to `localhost:3737` (after the `32 12` mechanical `generate`), which spawns a **haiku** `claude -p` run that does the same harvest + GLM-prose enrichment inline (inline, not the Workflow tool, so it works headless). Requires the dashboard server up (kept alive by `dashboard_keepalive.sh`). To make it sharper at the cost of a few more tokens, bump that kind's model from `haiku` to `sonnet` in `_ai_task_spec`.

### Cron (design only — not installed by this component)

```
32 12 * * 1-5 flock -n /tmp/premeeting_cards.lock python3 <repo>/.agent/skills/premeeting-cards/scripts/premeeting_cards.py generate >> <repo>/.agent/skills/premeeting-cards/premeeting_cron.log 2>&1
```

Actual installed crontab runs `generate` at `32 12 * * 1-5` (12:32 WIB) and the `premeeting-enrich` dashboard task at `45 12 * * 1-5` (12:45 WIB) — both inside the machine-on window, not the 07:45 WIB slot this section originally described. End the cron line with a `heartbeat.py --job premeeting-cards --status ok|fail` call once wired into the SOP (this component does not call heartbeat itself since generation is meant to run inside the morning-update workflow, but a standalone cron invocation should append one — see integration notes).

## Notes / Gotchas

- **Calendar attendees + the real dependency (people.json emails)**: `gcal_manager.py list --json` DOES return a fully-populated native `attendees` field (`[{email, responseStatus}]`; `displayName` is usually empty), so the calendar payload is not the gap. The actual dependency is that attendee resolution joins each attendee `email` to a person via `people.json` records' `emails[]` — so **keep `people.json` emails current**. If those `emails[]` are stale or empty, the email→slug join produces nothing: attendee resolution and every ledger join keyed on attendees (you owe them / they owe you / open decisions / unanswered pings) silently falls back to empty. Resolution order: (a) native `attendees` emails, (b) emails regex-matched out of the event `description`, (c) known-person name/alias substring matches against `summary + description`.
- `journal/state/people.json` is owned by the `stakeholders` component; this script only reads it (empty dict if missing).
- `tickets.json`, `slack_mention_ledger.json`, `fathom_registry.json` are read-only inputs, never modified.
- `decisions.json` / `commitments.json` / `waiting_on.json` are owned by components 1/2/3 respectively; read-with-fallback (empty dict) if not yet present or unparsable.
- No Slack/email/calendar writes of any kind. No LLM/agy-bridge calls — this is intentionally pure-mechanical per the harness-upgrade plan (cards must be cheap and always available even if agy-bridge is down).
- MTG-ticket / last-meeting matching is fuzzy (token overlap) — it degrades gracefully to "None matched" / "No prior meeting matched" rather than guessing.
