---
description: Evening closing update - full day recap, accomplishments vs morning plan, completion tracking
---
// turbo-all

# Evening Update Workflow

This workflow should be triggered each evening at ~21:30 WIB (or on-demand with `/daily-update evening`) to close the day with a full recap and completion tracking.

## Run Automated Script (Evening Mode)

Execute the daily runner in evening mode for a thorough data harvest.

```bash
// turbo
python .agent/scripts/daily_update_runner.py --mode evening
```

## Email Check (mandatory)

the owner acts off email too, so sweep it every evening (the runner does NOT pull email).

1. `gmail-connector` (Work, `you@yourcompany.com`) -- note the script sits at the skill root, NOT under `scripts/`:
   `python3 .agent/skills/gmail-connector/gmail_manager.py list --query "newer_than:1d -category:promotions -category:social" --limit 30`
2. Surface, today-scoped: (a) **milestones / signals** an email confirms (e.g. an App Store submission accepted, a client approval), (b) threads still **waiting on a the owner reply / decision** to carry over, (c) **new meeting invites** not yet on the board, (d) doc-comment mentions owed.
3. Tie each to the morning plan, a meeting-prep item, or a todo: did it get done, or does it carry over?
4. Filter noise (order pings, newsletters, Otter/Read/Fireflies/Fathom auto-recaps). Never auto-send email; surface the follow-up, draft only on request (approval-gated like Slack).

## Closing Recap & Completion Tracking

0. **Mention Ledger pass (mandatory)**: `python3 .agent/skills/slack-tracker/scripts/mention_ledger.py report` → embed "🔴 Waiting on your reply" in the recap (anything still open at end of day is a carryover candidate for tomorrow's plan); run `... classify` to GLM-triage the day's channel digest. The ledger is the source of truth for unanswered mentions/DMs/threads — never re-derive from raw dumps.
0a. **Access requests**: embed the 🔑 block from the same report, plus the Drive share requests in `journal/state/access_requests.json`. Anything still there at end of day is a person who could not work today, so it carries into tomorrow's top items, not into the general carryover list.
1. The script writes two outputs: `daily_update_evening.md` (human-readable) and `_temp/harvest_evening_[date].json` (structured sidecar).
2. **For synthesis, read `_temp/harvest_evening_[date].json` first** -- it is a compact structured JSON (sections: slack, jira, calendar, files_modified, files_created, backlogs, fathom, morning_plan, portfolio) and avoids re-reading the full 150-180 KB markdown dump. Fall back to `daily_update_evening.md` only if the JSON is missing or a section is empty. The markdown remains the user-facing deliverable and is NOT deleted.
3. Cross-reference the morning's proposed priorities from `_temp/daily_plan_[date].md`:
   - Mark completed items.
   - Identify carryover items that need to move to tomorrow.
4. Update `Dashboard.md`:
   - Create/update the `### [Date] (Malam): Closing & Recap` section.
   - Update project statuses and backlogs.
   - Archive daily entries older than 7 days to `journal/daily_logs/`.
   - Ensure the visual dashboard is live: run `bash .agent/scripts/ensure_dashboard.sh`. Idempotent — it exits if `localhost:3737` is already up and restarts the server if it died. The server serves `Dashboard.md` live, so this surfaces the freshly-written recap. Do NOT run `dashboard_sync.py` here: it would overwrite the hand-written `(Malam)` section.
5. Update `journal/todo.md`:
   - Mark completed items as `[x]`.
   - Flag stale items with no activity in 7+ days.
5a. **New Ledgers pass (mandatory — each ledger is the SOURCE OF TRUTH for its domain; embed `report` output verbatim, never re-derive from raw dumps):**
   - **Decision log**: `python3 .agent/skills/decision-log/scripts/decision_log.py report` → embed. Then capture today's decided items: for every decision that actually landed today (in a meeting, Slack thread, or doc), run `decision_log.py decide <DEC-id> --decision "<what was decided>"`; brand-new decisions surfaced today get an `add` first (with `--source` + `--source-type`).
   - **Commitments**: `python3 .agent/skills/commitment-ledger/scripts/commitment_ledger.py sweep` then `... report` → embed. Where the mechanical auto-close missed something the owner verifiably delivered today (sent DM, shared doc, MOM evidence), close it manually: `commitment_ledger.py close <COM-id> --note "<evidence>"`.
   - **Waiting-on watchdog**: `python3 .agent/skills/waiting-watchdog/scripts/waiting_watchdog.py report` → embed. Any 🚨 BREACHED item carries into tomorrow's plan as an explicit escalation action.
   - **Stakeholder pages**: `python3 .agent/skills/stakeholders/scripts/stakeholders.py render --all` (regenerates the AUTO blocks on every promoted `Clients/Work/People/` page from today's ledger state; idempotent. Roster-only people with no page are skipped and listed, not an error - see the skill's docstring for the two-tier model, and `promote <slug>` to give someone a page).
   - **Followup tracker**: `python3 .agent/skills/project-tracking-update/scripts/render_followup_tracker.py` (regenerates `journal/master_followup_tracker.md` as a GENERATED VIEW over the three ledgers above - the runner also calls this mechanically, this is the belt-and-suspenders re-run after any manual ledger edits made during this pass. Never hand-edit the tracker file itself.)
   - **Monday only — outcomes loop**: `python3 .agent/skills/outcomes-loop/scripts/outcomes_loop.py report` → embed (the weekly `check` cron ran Monday 08:20 WIB; if a metric shows `needs_reauth`, surface the Metabase re-auth need to the owner).
5a-bis. **MOM coverage reconcile (mandatory — the pipeline cannot self-report a meeting it missed):**
   - `python3 meeting-recorder/mom_reconcile.py` → reads `journal/state/mom_coverage.json`. It now enumerates directly from LIVE Fathom, so it no longer depends on `fathom_registry_sync.py` having run first (that chain is retired).
   - Every other capture component is artifact-driven: an empty recordings dir is indistinguishable from a day with no meetings, so a missed meeting produces NO MOM and NO alarm. On 16 Jul this silently dropped YourManager's mandatory Product/Growth/PMO weekly (62 min, 12 decisions) while `vexa-auto` logged 19 "ok" heartbeats. Fathom is the only record of what happened that this harness does not produce itself, so it is the reconciliation source.
   - Exit codes: `0` = clean, `1` = real gaps found, `2` = CANNOT VERIFY (could not reach Fathom / ground truth). Exit 2 no longer degrades to a false clean, so a `2` means the coverage claim is unproven and must be resolved before the recap.
   - Any `missing` entry → pull the Fathom transcript and produce the MOM before delivering the recap. Any `suspect` entry (stub, sub-2KB, or filed under a non-meeting block like "Prayer") → treat as NOT minuted; a false-positive MOM reads as done and hides the miss.
   - Never report the day's meetings as covered without this check passing (exit 0).
   - **Runs on its own cron** on the work window alongside `meeting-recorder/watcher.py`, not only as a passenger on this evening chain, so a gap alarms within the window instead of at night. Crontab line to add (do not rely on this workflow to trigger it):
     ```
     */15 12-22 * * * flock -n /tmp/mom_reconcile.lock /bin/bash -c 'cd . && python3 meeting-recorder/mom_reconcile.py' >> ./meeting-recorder/mom_reconcile_cron.log 2>&1
     ```

5b. **Tracker reconcile (mandatory — keeps the dashboard Today tab honest):**
   - Sweep `journal/state/tickets.json` for open tickets with `due` >= 3 days in the past.
   - For each, verify reality before touching it: the owner's SENT Slack DMs (`from:@yourhandle`), today's MOMs, email threads, Jira. Evidence it happened -> set `status: done` + a comment with the evidence link. Still real but slipped -> move `due` forward or downgrade priority, with a comment saying why. Riding another workstream -> `status: waiting` / `monitor` and name the vehicle.
   - Never mark done on guesswork; if unverifiable, leave open and note "unverified as of [date]".
   - Refresh `journal/state/portfolio.json` `updated_wib` + any initiative whose health/workstream status changed today, then regenerate the mirror via `python3 .agent/scripts/portfolio_render.py`.
   - Target end-state: zero tickets showing "stale ≥3d" on the dashboard Today tab without an explanatory comment.
5c. **Work tree refresh (mandatory, narrow — the dashboard Work tab has no other writer):**
   - `journal/state/work_tree.json` is read by `/api/work-tree` and `dashboard/public/tab-work.js` and written by NOTHING else. Before this step existed it sat frozen at 30 Jul 2026 for five days while SAIB shipped a BRD revision, so the tab showed a client card whose `next` pointed at a session that never happened and whose `blocker` was `null` while the work was in fact blocked. A stale tree is worse than an empty one: it reads as live.
   - **Only touch threads that actually moved today.** Derive the moved set mechanically, do not scan all 51 threads: `files_modified` + `files_created` + `jira` + `slack` + `portfolio` from `_temp/harvest_evening_[date].json`, today's new MOMs, and the ledger deltas from step 5a. Typical night is under 12 threads. A thread with no evidence of movement is left byte-identical.
   - For each moved thread rewrite only `progress`, `blocker`, `next`, and `status`, and add `"updated_wib": "<ISO+07:00>"`. Append any new primary artifact to `sources` (doc, Figma, published page, Slack permalink). Never invent a node: a genuinely new workstream gets added under its existing parent with the same field set.
   - Then set the top-level `"refreshed_wib"` to now. Leave `period` and `generated_wib` alone: those belong to the weekly regeneration in `/weekly-report`, and overwriting them would claim a full-tree refresh this step does not do.
   - **The judgment is the whole point, so do not delegate this to a subagent or to agy.** You have already formed it while writing the `(Malam)` section, and this step is a write pass, not a fresh analysis. Costed 4 Aug 2026 against `journal/state/token_usage.json`: as a byproduct here it is roughly $1.50 to $3 a night on top of a run that already costs about $11, because the harvest is already paid for. The same job as a standalone offloaded cron costs about $0.40 and produces `blocker: "waiting on estimate from Teammate"`, which is worthless. The value lives in the sentence a scrape cannot write, for example "80 hours is identical across all four options, so the client has nothing to choose between."
   - `blocker` must name the specific obstruction and who owns it, or be `null`. Banned as filler: "waiting on feedback", "pending review", "in progress". If nothing blocks it, `null` is the honest answer.
   - Validate before moving on: `python3 -c "import json; json.load(open('journal/state/work_tree.json'))"`. A malformed tree makes the whole tab go blank, not just one card.
   - Say in one line of the recap how many threads were refreshed out of the total, so a night where the moved set comes back empty is visible rather than silent.

6. Sync Fathom meeting notes and Work Document Index.
7. Run GitHub sync to push all changes.
8. Present to the owner:
   - Accomplishments vs Morning Plan scorecard.
   - Key Slack signals and decisions from today.
   - Open items carrying to tomorrow.
   - Sprint progress delta since morning.

## Quality Rubric (Full)

Apply ALL 9 checkpoints from the Daily Update Quality Rubric. This is mandatory for evening updates.

Run verify_briefing_numbers.py against the harvest sidecar; fix any MISMATCH before delivery. (`python3 .agent/scripts/verify_briefing_numbers.py --briefing <briefing.md> --harvest _temp/harvest_evening_<date>.json`)
