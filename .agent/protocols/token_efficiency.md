# Token-Efficiency Protocol

## Purpose
Every workflow, tool, and skill in this harness must get cheaper over time -
fewer Claude tokens per unit of work, more offload to non-Claude backends
(agy-bridge: GLM/Gemini), fewer wasted tokens on failed runs. This is a
standing continuous-improvement loop, not a one-off cleanup.

## Weekly auto-report (cron)
`token_efficiency.py report` aggregates the last 8 ISO weeks from
`journal/state/token_usage.json` (Claude-side sweep), `dashboard-data/agy_usage_log.jsonl`
(offload calls), and `journal/ai_runs/*.json` (headless runs), grouped by task
type. It writes `journal/state/token_efficiency.json` and prints totals,
week-over-week delta, top-3 hotspots, and any changelog entries from the last
14 days paired with their observed delta. Runs Monday 12:50 WIB via cron
(proposed line below); degrades to an empty report on a fresh install rather
than crashing.

## Rule: log every token-saving change
Any change made to reduce token spend (moving a step to agy-bridge/GLM,
removing a duplicate call, scoping a headless tool, caching, prompt trims)
**must** be logged in the same session via:

```
python3 .agent/scripts/token_efficiency.py log-change \
  --what "one-line description" \
  --files "path/a.py,path/b.md" \
  --task-types "task-type-1,task-type-2" \
  --expected "one-line expected effect"
```

No exceptions - an unlogged optimization is invisible to next week's report
and the delta can never be attributed to it. `--task-types` must match the
task_type strings token-tracker/agy-bridge/ai_runs actually use (see
`token_usage.py`'s classification, or grep a recent week in
`token_efficiency.json`).

## Per-session and subagent-model view
`token_efficiency.py` buckets spend by task type. For "which session, and was it
subagents on Opus", run `.agent/scripts/token_usage_report.py` (cron Monday 12:55
WIB, output `journal/reports/token_usage/<date>.md`; baseline
[`2026-10-13-baseline.md`](../../journal/reports/token_usage/2026-10-13-baseline.md): 87% of subagent spend on Opus). The 13 Oct 2026 target
is for that share to fall toward planning and design agents only.

## Hotspots feed weekly-planning
`/weekly-planning`'s review phase reads `journal/state/token_efficiency.json`
`hotspots` (top 3 token sinks with a one-line why) and picks **at most one**
optimization to schedule for the coming week. Don't queue more than one -
verify the prior week's change actually moved the delta before stacking
another.

## Dashboard surface
`journal/state/token_efficiency.json` is the data source for a dashboard
panel (built separately, see `dashboard/server.py` + `dashboard/public`) that
should show: weekly token/cost trend, offload share, hotspots, and the
changelog-to-delta pairing. This protocol does not own the UI.

## Cron (proposed, not installed)
```
50 12 * * 1 flock -n /tmp/token_efficiency.lock python3 ./.agent/scripts/token_efficiency.py report >> ./.agent/skills/token-tracker/token_efficiency_cron.log 2>&1
```
