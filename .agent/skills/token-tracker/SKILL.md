---
name: token-tracker
description: Records how many tokens and how much money each task type costs, split by interactive, daily-update, mom, prd, weekly-report, subagent, and workflow-agent. Use to see where the budget goes.
---

# token-tracker — Harness Token Consumption per Task Type

Tracks how many tokens (and how much API-equivalent money) the Claude Code
harness burns, split per task type: `interactive`, `daily-update`, `mom`,
`prd`, `weekly-report`, ..., `subagent`, `workflow-agent`, `ai-<kind>`
(headless dashboard runs).

> Cost framing: the owner pays a subscription, so every Claude figure here is an
> **estimasi setara-API** (what the same tokens would cost on the API), not a
> bill. Real offload spend (GLM/Gemini) lives in agy-bridge cost telemetry.

## What it does

- **Incremental sweep** of the Claude Code transcript store
  `~/.claude/projects/-home-you-antigravity-projects-product-second-brain/`
  (ALL `*.jsonl` recursively: sessions, `<session>/subagents/*.jsonl`,
  `<session>/subagents/workflows/wf_*/*.jsonl`). Per file it keeps
  `{mtime, size, summary}` in state and reparses only changed files.
- **Dedupe rule (important):** the transcript logs one row per content block of
  the same API message, each repeating the identical `message.usage` object —
  usage is counted once per `message.id` (verified on real transcripts
  2026-07-11; naive row-summing overcounts ~2.4x).
- **Pricing** comes from [`pricing.json`](pricing.json), extracted from the
  bundled claude-api skill (v2.1.205, table cached 2026-06-24) — never from
  memory. Cache reads bill at 0.1x input; cache writes at 1.25x (5m TTL) /
  2x (1h TTL) — the parser reads the `cache_creation.ephemeral_5m/1h` split
  when present. Unknown model id ⇒ cost null for those rows, listed under
  `unknown_models`, never guessed. `claude-sonnet-5` uses intro pricing
  ($2/$10) — swap to $3/$15 after 2026-08-31.
- **State / output:** `journal/state/token_usage.json` with a 30-day
  `aggregate` block: `totals`, `by_task_type` (runs · avg in/out/total ·
  avg+total cost · share %), `by_model`, `by_day` (30 zero-filled WIB dates).
- The dashboard serves the aggregate at `GET /api/token-usage` and triggers a
  background sweep automatically when the state is older than 6h.

## Usage

```bash
python3 .agent/skills/token-tracker/scripts/token_usage.py sweep          # incremental
python3 .agent/skills/token-tracker/scripts/token_usage.py sweep --full   # force reparse
python3 .agent/skills/token-tracker/scripts/token_usage.py report         # markdown, briefing-ready
python3 .agent/skills/token-tracker/scripts/token_usage.py report --json  # aggregate as JSON
```

Perf reference (2026-07-11, 1,049 files / ~508 MB): full reparse ≈ 1.2 s,
incremental ≈ 0.1 s. `sweep` prints a one-line summary and writes a heartbeat
(`--job token-tracker`, ok/fail).

## Task-type classification

| Signal | Type |
| :-- | :-- |
| path contains `/subagents/workflows/` | `workflow-agent` |
| path contains `/subagents/` | `subagent` |
| first ~20 rows contain `<command-name>X</command-name>` or a user message starting `/X ` | `X` (local CLI toggles like `/model` are ignored) |
| top-level file starting inside a `journal/ai_runs/*.json` run window ±3 min, not a VS Code session | `ai-<kind>` |
| everything else | `interactive` |

Note: headless `claude -p --output-format json` dashboard runs currently leave
no transcript in the project store, so their exact usage+cost is captured on
the runner side instead (run meta `tokens_in/tokens_out/cost_usd`, surfaced by
`GET /api/ai-task`). The `ai-<kind>` join stays in for the day transcripts
appear.

## Cron (the owner installs; on-window rule 12:30–22:00 WIB)

Cron runs on the WSL automation host only. Do not install on macOS.

```
50 12,18 * * * flock -n /tmp/token_tracker.lock python3 ./.agent/skills/token-tracker/scripts/token_usage.py sweep >> ./.agent/skills/token-tracker/token_tracker_cron.log 2>&1
```

## Wiring (dashboard + registries)

- `dashboard/server.py`: `token-tracker` is whitelisted in `JOB_RUN_MAP`
  (manual run button) and `JOB_LOG_MAP` (log/heartbeat join); new route
  `GET /api/token-usage`.
- `harness_health.py` `CRON_REGISTRY` entry (owned by harness-health — add there):

```python
{
    'job': 'token-tracker',
    'match': 'token-tracker/scripts/token_usage.py',
    'cadence_minutes': 6 * 60,   # 12:50 + 18:50 WIB, tolerate overnight gap
    'heartbeat_job': 'token-tracker',
    'log_file': os.path.join(BASE_DIR, '.agent', 'skills', 'token-tracker', 'token_tracker_cron.log'),
},
```

- `journal/state/routines.json` row (owned by routines wiring — add there):

```json
{
  "job": "token-tracker",
  "name": "Token usage sweep",
  "schedule": "12:50 & 18:50 WIB",
  "command": "python3 .agent/skills/token-tracker/scripts/token_usage.py sweep",
  "enabled": true
}
```
