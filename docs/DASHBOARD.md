# Visual Dashboard

A local web cockpit for your second brain. It runs on your machine at
`http://localhost:3737` and gives you a glanceable view over your `Dashboard.md`, your
calendar, your project docs, your to-do tracker, meeting health, and more, with a few
things you can act on directly (edit a ticket, trigger a routine, kick off an AI task).

It lives in `dashboard/` and is pure Python standard library (no pip install, no build
step), so it runs anywhere you have `python3`.

- [Start it](#start-it)
- [What you see, and what fills in over time](#what-you-see-and-what-fills-in-over-time)
- [The tabs](#the-tabs)
- [The Hours tab: work-hours productivity tracker](#the-hours-tab-work-hours-productivity-tracker)
- [Where each panel gets its data](#where-each-panel-gets-its-data)
- [Keeping it running](#keeping-it-running)
- [Security](#security)

---

## Start it

```bash
python3 dashboard/server.py
```

Then open **http://localhost:3737** in your browser. To use a different port:

```bash
DASHBOARD_PORT=4000 python3 dashboard/server.py
```

The server serves the UI from `dashboard/public/` and exposes a small JSON API the
page polls. It is stateless: it reads your repo files live on each request, so it always
reflects the current state on disk.

---

## What you see, and what fills in over time

The dashboard is a **window onto your data, not a database of its own.** On a fresh clone
it is mostly an empty shell, and that is expected. Panels light up as you actually use the
brain:

- The **overview and notes** panel reads `Dashboard.md`, so it works as soon as you have one.
- The **calendar** panel works once you connect a calendar (see `docs/SETUP.md`).
- The **projects** panel lists the markdown docs under your `Clients/` folder, so it fills
  as you create PRDs, MOMs, and strategy docs.
- The **tracker, ledgers, meeting-health, routines, and AI-task** panels read state files
  under `journal/` that the matching skills and commands build up as you work. Until that
  state exists, those panels are empty or show "no data." That is normal, not a bug.

In other words: connect your tools, run your daily and weekly workflows, and the cockpit
gets richer on its own.

---

## The tabs

There are seven tabs. Each owns its own frontend module under `dashboard/public/` except
Today, which lives in `app.js` alongside the shared router and utilities.

- **⭐ Today** (`app.js`): the daily landing view. Approvals waiting on you (command-queue
  drafts that finished and need a decision), today's meetings with prep cards, top
  tickets, and an SLA-breach escalation strip.
- **📥 Inbox** (`tab-inbox.js`): one triage queue for every inbound thread: Slack mentions
  and DMs, Gmail, Google Doc comments, and Jira mentions. Each item gets reversible
  triage (done, ignore, reopen), a link into the ticket tracker, and an optional AI
  copilot pass that can draft a reply for your approval.
- **📋 Work** (`tab-work.js`): the ticket tracker (create, edit, comment on tickets right
  in the page), the project portfolio by team, a decisions log, commitments, and
  stakeholder rollups. Clicking an initiative drills into its own health summary,
  blockers, and task hierarchy.
- **🎥 Meetings** (`tab-meetings.js`): live recorder health, recent meetings from the
  Fathom registry grouped by day, minutes and notes, and bot activity. See also
  `docs/MEETING_RECORDER.md`.
- **⏱ Hours** (`tab-hours.js`): the work-hours productivity tracker. Actual vs parallel
  hours, the leverage multiplier, per-stream detail, and the weekly trend. Full
  methodology below.
- **⚙ System** (`tab-system.js`): harness self-observability. Job routines (failing ones
  surface first, each expands to a job-log drill with a Run-now / Ack action), harness
  health findings, a live map of the harness, activity, cost and savings, Claude token
  usage, and the token-efficiency trend.
- **✨ What's new** (`tab-whatsnew.js`): the repository's `CHANGELOG.md`, served by
  `/api/changelog` and parsed by `dashboard/changelog.py`. The Unreleased section and
  the newest release open by default, older ones are collapsed. HTML comments are
  dropped, links to repository files show as plain text, and every section is
  HTML-escaped before it is rendered. Test: `python3 dashboard/test_changelog.py`.

Many list panels open a detail drawer when you click a row.

---

## The Hours tab: work-hours productivity tracker

The most distinctive panel in the dashboard. It reconstructs the owner's working day from
digital traces rather than a manually filled timesheet, and shows both what is measured
and what is estimated, side by side, rather than blending them into one number.

Built by `.agent/skills/work-hours/scripts/work_hours.py`, state in
`journal/state/work_hours.json`, served at `/api/work-hours`.

**What it shows, per day:**

- **Actual hours**: the union of every active minute across all workstreams, overlaps
  counted once. This is measured, not estimated: it comes from Claude Code transcript
  timestamps (interactive sessions only, cron and automation runs are excluded),
  attended meetings, and git commits.
- **Parallel output (effective hours)**: the sum of per-stream hours, so three streams
  running for one hour count as three hours. Also measured, no assumptions.
- **Leverage multiplier**: parallel output divided by actual hours. Measured parallelism,
  the honest answer to "how much did I get done in the time I had."
- **Productivity / output multiplier**: (meetings at 1x, plus AI-stream hours at the
  AI-speed factor) divided by actual hours. This one is an estimate, not a measurement.
- **Streams breakdown**: a per-day timeline of overlapping streams by lane (Meetings,
  Work PM, You, Other AI), stacked so overlaps are visible, with a table twin for
  accessibility.
- **Weekly trend**: the same actual / effective / leverage / productivity figures
  aggregated by week, so a single noisy day does not distort the read.

**What is measured vs what is assumed, stated plainly because this is the number people
quote:**

- Measured: actual hours, effective (parallel) hours, and the leverage multiplier. These
  come straight from transcript timestamps, calendar/meeting records, and git commits,
  with no conversion factor applied.
- Assumed: the productivity / output multiplier, because it applies an AI-speed factor to
  convert AI-stream hours into an estimated manual-solo-equivalent. The default factor is
  **2.5**, research-calibrated (see `.agent/skills/work-hours/research_ai_speed_factor.md`)
  but still an estimate. The dashboard UI labels every figure that uses it "assumed," and
  it is overridable via `--ai-speed N` or the `WORK_HOURS_AI_SPEED` env var.
- The workday boundary is **04:00 WIB**, not midnight, so work that runs past midnight
  counts to the day it started rather than splitting across two days.
- Overlapping meetings are merged into a single stream before counting. the owner is one
  person: a double-booked slot, or one recording that spans two calendar events, is never
  counted twice.

---

## Where each panel gets its data

| Panel | Reads from | Needs |
|---|---|---|
| Overview / notes | `Dashboard.md` | nothing (ships as a stub) |
| Calendar | your calendar connector | calendar set up in `docs/SETUP.md` |
| Projects | `Clients/<context>/**/*.md` | your own project docs |
| Inbox | `journal/state/inbox.json` | the inbox-hub skill (sweep cron or the ↻ Sweep button) |
| Command-queue approvals (Today) | `journal/state/command_queue.json` | the command-queue skill |
| Tracker | `journal/state/tickets.json` | created as you add tickets |
| Portfolio | `journal/state/portfolio.json` | `.agent/scripts/portfolio_sync.py` |
| Ledgers | `journal/state/*.json` (commitments, waiting-on, decisions, outcomes) | the ledger skills/commands |
| Meeting health | `meeting-recorder/` + `journal/fathom_registry.json` | the meeting recorder |
| Hours (productivity tracker) | `journal/state/work_hours.json` | the work-hours skill (self-refreshes on tab open, no cron required) |
| Routines | `dashboard-data/agent_heartbeat.jsonl` | your scheduled jobs writing heartbeats |
| Token usage | `journal/state/token_usage.json` | the token tracker |
| Token efficiency | `journal/state/token_efficiency.json` | `.agent/scripts/token_efficiency.py report` (weekly cron) |
| Cost and savings (agy-bridge) | `dashboard-data/agy_cost_summary.json` | any agy-bridge `--task` call or `probe.py` |

If a source file does not exist yet, the panel degrades to empty rather than erroring the page.

---

## Keeping it running

For everyday use, just start it when you want it. If you want it always on, run it under
your OS process manager or a simple cron keepalive that restarts it if the port is not
listening. Because it is stdlib-only Python, the same `python3 dashboard/server.py` works
on macOS, Linux, and WSL.

---

## Security

- **It binds to localhost.** Do not expose port 3737 to your network. There is no
  authentication: anyone who can reach the port can read your data and use the action
  buttons.
- **It can run local commands on your behalf.** The action endpoints edit your local
  tracker files, and the optional AI-task and run-job buttons execute local scripts and can
  invoke the headless `claude` CLI. Those are conveniences for a single-user, on-your-own-machine
  setup. Run the dashboard only on a machine you control, and review what a button does before
  clicking it.
- **No data leaves your machine** from the dashboard itself, beyond the API calls your
  connectors already make (e.g. fetching your calendar).
