# sweep-engine

The engine behind `/sweep`: one adapter per tool (Slack mention ledger, Slack threads,
Gmail, Jira, calendar, Linear, GitHub) collects what may be waiting on you, and
`sweep_core.py` collapses each thread, ticket or doc into one item, closes what you
already answered, drops noise by rule and ranks the rest. `/sweep` describes the steps.

    python3 .agent/skills/sweep-engine/run_sweep.py --refresh-ledger --stream journal/sweep/stream

## Setup

- Connect the tools you use through their connectors (`slack-connector`, `gmail-connector`,
  `jira-connector`, `google-calendar-connector`, `linear-connector`, the `gh` CLI). A tool that
  is not set up shows as `skipped` in the report, never as an empty clean result.
- Optional: copy `config.example.json` to `config.json`. Every key is optional. Your Slack id
  and workspace URL come from Slack itself, the Google Drive app is matched by name, and the
  calendar uses the `default` profile.
- `filter.json` is created from `filter.example.json` on the first run. Teach it with
  `sweep_core.py teach` (`--allow`, `--deny`, `--vip`); fill `me` with your own ids and emails.

`--stream <dir>` prints `STREAM <adapter> <n> <survivors path>` as each adapter finishes, so the
caller can label and branch fast sources before slow ones (Slack threads) are done.
