---
name: achievements-tracker
description: Finds the owner's product wins in real data each month, drafts CV bullets with caveats and the decisions behind them, messages the owner, and adds them to the CV after a grace period (silence = approved). Use when the owner says "review my CV wins", "approve ACH-xxxx", "reject ACH-xxxx", asks what wins are pending, or asks to add an achievement to their CV.
---

# Achievements Tracker

People forget their own wins. By the time they update their CV, the numbers are gone. This skill reads the product's monthly numbers, spots real improvements, and writes them into the CV as they happen.

- **Scan (monthly):** reads the monthly numbers from the configured source. A series is a win when the last closed month is at least 1.5x its low of the six months before, is the best month since that low, and clears a size floor. A segment that started inside the window (a new category, plan or market) counts from its first month. Product-level metrics merge into one bullet. Each win becomes a candidate `ACH-xxxx` with a draft bullet, caveats (cancellations up 5 points or more, still below the all-time peak), and the owner's logged decisions in that window, which give the interview story.
- **Message:** one message per scan with new or changed candidates (WhatsApp self-chat through a local bridge, any command, or none).
- **Silence is approval:** a candidate with no reply after `grace_days` (default 7) goes into the CV with the draft wording. Newer numbers for an existing bullet replace that bullet.
- **Monthly review:** a recurring calendar event is where the owner polishes wording and rejects anything wrong. A scanner can tell that a number grew. Only the owner knows what they did to make it grow.

If a `LOCAL.md` exists next to this file, read it first. It holds this install's specifics (where the CV copies live, the schedule, the source quirks).

Script: `.agent/skills/achievements-tracker/scripts/achievements.py`. Config: `config.json` next to this file (copy `config.example.json`). The `ACHIEVEMENTS_CONFIG` and `ACHIEVEMENTS_ROOT` env vars override both.

## Setup (once)

1. Copy `config.example.json` to `config.json` and fill in:
   - `cv_path`, the CV in markdown, and `cv_section`, the line that starts the job the bullets belong under (for example `**Acme Inc**`). Bullets go after the last bullet of that section.
   - `product`, `currency`, `total_metrics`, `metric_labels`, `floors`.
   - `source`:
     - `series-file`: a JSON file that something else keeps up to date. This can be an agent turn that reads a connected analytics tool, an export, or a sheet. Shape: `{"total": {"2026-09": {"orders": 700, "revenue": 50000, "cancel_pct": 4.1}}, "groups": {"subscriptions": {"2026-09": {"revenue": 30000}}}}`. `cancel_pct` is optional; when present it powers the cancellations caveat.
     - `metabase`: `db_id`, `sql_total` and `sql_groups` returning the column `m` (YYYY-MM), one column per metric, and `grp` for groups.
     - `command`: `argv` that prints the series JSON.
   - `notify`: `whatsapp-bridge` (`server_dir`, `api_url`, and `recipient` = the owner's own number), `command` (`argv`, message on stdin), or `none`.
   - `decisions` (optional): a decision log (`{"items": {...}}` with `status`, `decided_at`, `decider_slug`, `title`, `project`) to attach the owner's decisions to each win.
2. Schedule `scan` then `auto-approve`, a few times a day on weekdays. A month that was already scanned exits at once, so the extra runs only matter when the source was unreachable.
3. Create a recurring monthly calendar event, "CV review", with an email reminder the day before.

## When the owner says "review my CV wins"

1. Run `achievements.py list`. It shows pending candidates and anything not yet copied to other versions of the CV. Show each bullet with its caveats and decisions.
2. Apply what they say:
   - Keep it: `approve ACH-xxxx [--text "<their wording>"] [--keep]`. `--keep` keeps the older bullet that this one updates.
   - Drop it: `reject ACH-xxxx --reason "..."`. This also removes a bullet that was already auto-added.
   - New wording for an auto-added bullet: reject it, then put their wording in the CV.
3. If the CV also lives somewhere else (a Google Doc, a website), update that copy, then run `achievements.py synced`.
4. If the last scan says `unreachable` or `needs_reauth`, fix the connection with the owner, then run `scan`.

A rejected win comes back only when it grows 25% more.

## Commands

- `scan [--month YYYY-MM] [--force] [--dry-run] [--fixture file.json]`
- `auto-approve [--dry-run]`
- `list [--all]`, `approve`, `reject`, `synced`

## Limits

- It sees numbers, not causes. A good month can be seasonality, a client campaign or another team's work. The caveats and the monthly review are there for that.
- Draft wording is plain. A bullet written by hand is stronger, and the review is the place to write one.
