---
description: Comms - Toggle or inspect auto reply drafts (reply-router) - on/off, per source, status
argument-hint: "on | off | status | health | list | run | close --all-answered | on --source gmail"
---

Auto reply drafts (reply-router). Full SOP: `.agent/skills/reply-router/SKILL.md`.

Run the matching CLI and report its output in one line:

```bash
python3 .agent/skills/reply-router/scripts/reply_router.py $ARGUMENTS
```

- No argument or `status` -> `status`.
- `health` is the one-line verdict, for callers that need something to branch on. Exit 2 means work is held by something that will not clear by itself, or by a limit that has held the same work for over a day. `status` now also shows cap usage and every held block: how many items, held or dropped, which setting, and when the work comes back. Across all automations: `python3 .agent/scripts/automation_settings.py held` (the morning update runs this at step 0c). Contract: [`docs/harness_reference.md#automation-limits`](../../docs/harness_reference.md#automation-limits).
- `list` shows the open reply conversations, their age and whether each one claimed a session id.
- `close --conv <key> --summary "..."` finishes one conversation and retires its chat; `close --all-answered` finishes every one Slack already marked answered.
- `on` / `off` toggles the master switch; add `--source slack|gmail` for one source.
- `run` is the dispatch pass itself. The ASB app runs it on its own ticker, every 5 minutes while the app is open, from `src-tauri/src/auto_drafts.rs` -- NOT from a routine and not from a crontab. A `runner: app` row for this job is a second host: it ran the same script a second time from 11 to 17 Sep 2026, cost a model turn per fire, and put a stray top-level chat beside the reply group. Add `--dry-run` to see what would dispatch without opening sessions.
- A toggle takes effect on the next ticker pass, within 5 minutes, with the app open.
- **A toggle only reaches the ticker through git.** The config path is resolved from the script's own location, so a toggle secondaryped in one checkout writes that checkout's file, while the ticker runs on whichever machine has the app open. `on`, `off` and `status` now print the `config:` path, the machine, and a warning when the setting is not committed or not pushed. Read that warning: it is the difference between a switch that is on and a switch that only looks on. This is what kept the router off from 3 to 14 September 2026.
- This toggles DRAFTING only. Sending stays approval-gated regardless.
