---
description: Morning prep update - sweep overnight signals, set today's priorities, and prepare the workday focus
---
// turbo-all

# Morning Update Workflow

This workflow should be triggered each morning at ~09:30 WIB (or on-demand with `/daily-update morning`) to prepare the owner's daily priorities and review overnight signals.

## Run Automated Script (Morning Mode)

Execute the daily runner in morning mode for a fast, priority-focused sweep.

```bash
// turbo
python .agent/scripts/daily_update_runner.py --mode morning
```

## Calendar-Driven Meeting Prep (mandatory)

The day is built around meetings, so the calendar is a first-class source, not an afterthought.

1. Pull today's Work calendar (`.agent/skills/google-calendar-connector/gcal_manager.py list --profile work --days-back 0 --days-forward 1`). For EVERY meeting that needs the owner's input/decision/output (skip pure standups/prayer/all-day), create a **per-meeting prep item** as a ticket in `journal/state/tickets.json` (id `MTG-<TAG>-<MMDD>`, `due` = today, `kind: self`, owner the owner, correct `project`). This is what surfaces it on the `localhost:3737` Today board, so a meeting like the Example Catalogue weekly is never lost.
2. For each prep item, note the goal, the doc/Slack/email inputs to read first, and any scheduling conflicts (two events same slot). Link them.
3. Flag calendar conflicts and any invite-time mismatches (an emailed updated-invite time differing from the live calendar) explicitly.

## Email Check (mandatory)

the owner acts off email too, so sweep it every morning.

1. `gmail-connector` (Work, `you@yourcompany.com`) -- note the script sits at the skill root, NOT under `scripts/`:
   `python3 .agent/skills/gmail-connector/gmail_manager.py list --query "newer_than:2d -category:promotions -category:social" --limit 25`
2. Surface: (a) anything that is an **input for a meeting today** (e.g. a shared sheet/BRD/doc the meeting depends on), (b) **new meeting invites** not yet on the board, (c) threads **waiting on a the owner reply / decision**, (d) doc-comment mentions.
3. For each, state clearly whether it **needs and can be followed up by email** vs FYI, and tie it to the relevant meeting-prep item or todo.
4. Never auto-send email; surface the follow-up, draft only on request (approval-gated like Slack).

## Mention Ledger, DM Sweep & Prior-Day Backfill (mandatory)

The channel skim (5 msgs/channel) misses DM threads and old-thread replies. The **Mention Ledger** (`.agent/skills/slack-tracker/scripts/mention_ledger.py`, cron `*/30`) is the mechanical safety net for both. Every morning:

0. **Run the ledger first**: `python3 .agent/skills/slack-tracker/scripts/mention_ledger.py report` → embed the "🔴 Waiting on your reply" list in the briefing verbatim (priority/YourManager items on top, with age + permalink). Then `... classify` to GLM-triage the channel digest and fold `needs_reply`/`action_item` results into the day's signal. The ledger is the source of truth for unanswered mentions — do NOT re-derive them from raw channel dumps.
0a. **Access requests are their own pass, and they are not optional.** `mention_ledger.py report` now prints a 🔑 block above everything else; embed it verbatim, oldest first. Then read `journal/state/access_requests.json` (hourly cron snapshot) for the **Google Drive share requests**, which arrive by email and never appear in Slack at all. Refresh it by hand with `python3 .agent/skills/access-watch/scripts/access_watch.py report --days 90 --out journal/state/access_requests.json` if the snapshot is stale.

   Anybody in that list is blocked on the owner and cannot work. It goes in the briefing above sprint numbers. Never bury an access ask inside the long "waiting on your reply" list: on 1 Sep 2026 four of them sat there for a day, and the 39-day-old Drive ones had never been surfaced anywhere. Granting stays approval-gated, so present the grant command, do not run it.

1. Deep-read open DMs, especially **YourManager** (`<SLACK_ID>`) and other leadership. **ANY message from YourManager is high priority by default** and must surface in the day's signal.
2. If **NO evening update ran the prior day**, also pull yesterday's **Fathom** meeting outcomes (registry + MCP) so closed-meeting decisions are not lost, since Fathom is otherwise an evening-only harvest step.
3. If **NO evening update ran the prior day**, also run the **tracker reconcile** from `.agent/workflows/evening-update.md` step 5b (verify long-overdue tickets against sent DMs/MOMs/email, update status with evidence) so the dashboard Today tab does not accumulate stale items.

## New Ledgers & Cards (mandatory)

Each ledger below is the **SOURCE OF TRUTH** for its domain — embed its `report` output verbatim and do NOT re-derive its items from raw Slack/Fathom/calendar dumps.

1. **Pre-meeting cards**: `python3 .agent/skills/premeeting-cards/scripts/premeeting_cards.py generate` (idempotent; cron generates at `32 12 * * 1-5` and enriches at `45 12 * * 1-5` WIB — i.e. 12:32/12:45 WIB, so a morning run before then must generate them itself; rerun is safe), then `... report` → embed the index verbatim. Cards live in `journal/premeeting/<date>/`; link each card next to its meeting in the briefing.
   - **Enrichment pass (mandatory, AFTER the final `generate` of the day — regenerate wipes hand edits).** The generator only emits the mechanical join. Enrichment runs through **agy-bridge (GLM), never Claude** (the owner's standing rule, 17 Jul 2026):
     ```bash
     python3 .agent/skills/premeeting-cards/scripts/enrich_cards_agy.py --date <YYYY-MM-DD WIB>
     ```
     It selects substantive Work meetings (skips prayer/focus/home), runs the scripted live-status check, hunts + verifies source docs, and has the bridge model write each card as a walk-in brief with `## 🎯 Goal` · `## 📌 What this is` · `## ✅ Drive in the room` · `## ⚠️ Watch` · `## 🔗 Sources` · `## 🧾 Open items`. Weight by what the owner decides in the room, not recency. The mechanical join is preserved underneath in a `<details>` block as the audit trail. Flags: `--dry-run`, `--regenerate` (re-enrich existing briefs). `--force-glm` is a DEPRECATED no-op since z.ai/GLM was retired 2026-07-27; enrichment always runs the Gemini-first agy chain. Surface any printed `STATUS FLAGS` at the top of the briefing.
     - **`enrich_meeting_cards.workflow.js` is RETIRED** (Claude subagent fan-out, needed per-run Workflow opt-in so it silently got skipped and left cards mentah). Do not invoke it.
     - **`unknown` status means UNCHECKED, not "on".** The Slack check only catches a cancellation when the message names the meeting or lands in a channel whose name does. It MISSES the most common shape: a key attendee DM-ing "I won't be able to join today" without naming it, which is exactly how YourManager cancelled the 16 Jul Weekly PMO (verified 17 Jul: still `unknown` even at a 48h window). The authoritative signal is calendar event status, which is not wired in yet. Until it is, eyeball the day rather than trusting a clean run.
     - GLM only links docs Python verified on disk. If a card cites something, it exists. Do not let a future change hand the model a topic and ask it for URLs.
   - **Every source doc gets a link — no exceptions (extends [[feedback_always_link_cited_docs]]).** When a card names or leans on any artifact, embed it as a clickable link, never bare text:
     - **BRD/PRD/notes in the repo** → link the **repo-relative path** (e.g. `Clients/Work/Example Program/BRD_...md`). The dashboard renders relative-path markdown links as drawer openers, so the owner reads the doc in-place. Verify the file exists (`ls`) before linking; if it lives only on Drive, link the **GDoc URL** instead.
     - **Figma** → the Work-account Figma URL (per [[feedback_figma_work_account]]); resolve via the master-links index, don't guess.
     - **Driving Slack thread / ping** → the **permalink** (per [[feedback_slack_sending_playbook]]).
     - **Jira** → the issue link. Never reference "the BRD"/"the PRD"/"the ticket"/"the Figma" without the link attached — that is the exact gap the owner flagged (card looked thin because the doc wasn't clickable).
   - Actively hunt the relevant docs (grep the repo, `master_links.md`, fathom_registry, the ticket's own links) rather than only linking what the mechanical card happened to surface. A thorough card front-loads every doc the owner would otherwise have to go dig for.
2. **Commitments (the owner owes others)**: `python3 .agent/skills/commitment-ledger/scripts/commitment_ledger.py report` → embed verbatim (overdue first). If the last `sweep` printed `FALLBACK_TO_CLAUDE` or `pending_candidates` remain in `journal/state/commitments.json`, Claude extracts those candidates itself (read each candidate's text, decide if it is a real commitment, identify recipient + due) and registers the results via `commitment_ledger.py add ...` — do not leave candidates to rot.
3. **Waiting-on watchdog (others owe the owner)**: `python3 .agent/skills/waiting-watchdog/scripts/waiting_watchdog.py sweep --check-slack` (the Slack-thread close check is SOP-only, never cron'd), then `... report` → embed verbatim. Every 🚨 BREACHED line becomes an **explicit escalation action in today's Top-5** (who to ping, on which channel, per the item's `escalate_to`/`escalation_path`).
4. **Decision log**: `python3 .agent/skills/decision-log/scripts/decision_log.py report` → embed verbatim (overdue-open decisions on top). Surface any decision whose deadline is today/past as a today-action.
5. **Work-tree coverage**: `python3 .agent/scripts/work_tree.py coverage` → if any OPEN record is unfiled, surface the count as one line in the briefing with the top 3 items from `journal/state/work_tree_triage.json`, and ask the owner which node each belongs to. An unfiled open record is work the tree cannot see, so it keeps appearing here until it is filed (CLAUDE.md, "Every Ticket Belongs To A Work-Tree Node"). File the answers with `<ledger>.py refile <ID> --node <node>`.
6. **Reply queue (drafts only)**: `python3 .agent/skills/reply-queue/scripts/reply_queue.py draft --limit 15`, then `... report` → embed, and link today's draft file `journal/reply_drafts_<date>.md` in the briefing. If the file contains a `## FALLBACK_TO_CLAUDE` section, Claude drafts those replies itself in the owner's voice: plain flowing prose, no emoji, no numbered-bold lists. Drafts are never sent from here — sending stays approval-gated via [`slack_send.md`](../protocols/slack_send.md).

## Priority Setting & Summary

1. The script writes two outputs: `daily_update_morning.md` (human-readable) and `_temp/harvest_morning_[date].json` (structured sidecar). It also saves the proposed plan to `_temp/daily_plan_[date].md`.
2. **For synthesis, read `_temp/harvest_morning_[date].json` first** -- it is a compact structured JSON (sections: jira, calendar, slack, todo_p0) and avoids re-reading the full 100+ KB markdown dump. Fall back to `daily_update_morning.md` only if the JSON is missing or a section is empty. The markdown remains the user-facing deliverable and is NOT deleted.
3. Update `Dashboard.md`:
   - Create/update the `### [Date] (Pagi): Priorities & Prep` section.
   - Refresh the Calendar Focus for today.
   - Sync Jira sprint snapshot.
   - Ensure the visual dashboard is live: run `bash .agent/scripts/ensure_dashboard.sh`. Idempotent — it exits if `localhost:3737` is already up and restarts the server if it died. The server serves `Dashboard.md` live, so this surfaces the freshly-written section. Do NOT run `dashboard_sync.py` here: it would overwrite the hand-written `(Pagi)` section.
4. Present to the owner:
   - Today's calendar with P0/P1 meeting tags.
   - Top 5 proposed priorities (sourced from todo.md, overnight Slack, Jira).
   - Carryover items from yesterday.
   - Any overnight blockers or urgent messages.
5. Wait for the owner's alignment before updating todo.md priority order.

## Focus Block Enrichment (Calendar)

After the owner aligns on priorities, any **focus block** created/refreshed on their Work calendar (`gcal_manager.py create --profile work`) must carry a **rich `--desc`**, never just a terse one-liner. the owner acts directly off the calendar, so each block must answer: *what, why, where do I go, what do I do, who do I tell* — with clickable links. Use this template:

```
🎯 WHY: <one-line reason this matters now / the trigger>
📋 Ticket: <ME-XXX>
🔗 Docs: <Google Doc / PRD / Figma links>
💬 Context: <Slack permalink to the originating thread + who raised it>
✅ DO: <the concrete decision/output to produce in this block>
➡️ THEN: <who to communicate the result to + which channel>
```

Rules:
- Always hyperlink cited docs/threads (per [[feedback_always_link_cited_docs]]); never leave a source as plain text.
- Resolve every Slack ID to a name before writing it (per [[feedback_no_guessing_names]]).
- `gcal_manager.py` has **no `update`** action and MCP Calendar points at Secondary — so the rich `--desc` must be set at **create** time. For an existing block that can't be edited, surface the enriched brief in the Dashboard `(Pagi)` section instead and flag that the calendar copy is terse.

## Quality Rubric (Morning Subset)

Apply checkpoints 1 (Source Citation), 2 (Cross-Reference & Completion), 4 (Staleness Scoring), 7 (Roster & Team Ownership), and 8 (Keyword Sweeper) from the Daily Update Quality Rubric. Other checkpoints are optional in morning mode.

Run verify_briefing_numbers.py against the harvest sidecar; fix any MISMATCH before delivery. (`python3 .agent/scripts/verify_briefing_numbers.py --briefing <briefing.md> --harvest _temp/harvest_morning_<date>.json`)
