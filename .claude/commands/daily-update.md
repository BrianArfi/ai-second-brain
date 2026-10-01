---
description: Daily - Daily update - reads the owner's timezone from CLAUDE.md; morning prep before 17:00 local time (or if no morning ran yet), evening recap after
argument-hint: "[optional focus, or 'morning'/'evening' to force a mode]"
---

Determine the owner's current local time first. **The timezone comes from the `Timezone` line in `CLAUDE.md`.** Map it to an IANA zone name and call it `<TZ>` below: WIB is `Asia/Jakarta`, GST is `Asia/Dubai`, and a line that already names an IANA zone is used as written. If `CLAUDE.md` has no timezone, ask once, then continue.

**Do NOT run `TZ=<TZ> date` in Git Bash on Windows.** Git Bash ships no tzdata, so the `TZ` value is silently ignored and the command returns **UTC**. On 14 Sep 2026 that returned 05:43 when the real time was 12:43 local (UTC+7), and the run entered morning mode after a morning update had already gone out.

Get the time from a host that has tzdata:

```bash
# WSL or macOS (native):
TZ=<TZ> date '+%H:%M %Z %A %Y-%m-%d'

# Windows native (proxy to WSL):
wsl.exe bash -c "TZ=<TZ> date '+%H:%M %Z %A %Y-%m-%d'"

# Any host with Python 3.9 or later:
python3 -c "from datetime import datetime; from zoneinfo import ZoneInfo; print(datetime.now(ZoneInfo('<TZ>')).strftime('%H:%M %Z %A %Y-%m-%d'))"
```

Sanity check before trusting it: `date -u` plus the zone's UTC offset must equal the local figure. If a machine reports the same clock for both `date -u` and the `TZ=<TZ>` command, and the zone is not UTC, that machine has no tzdata and its answer is UTC. Use another host.

**Then check whether a morning update already ran today**, because that decides the mode as much as the clock does: grep `Dashboard.md` for a `(Pagi)` section carrying today's date, and look for `_temp/daily_plan_<today>.md`. If either exists, morning has run and the mode is evening regardless of the hour.

If $ARGUMENTS forces a mode ("morning" or "evening"), obey it. Otherwise (the owner's rule: morning until 17:00 local time, since his work window starts ~12:30; change the hour here if your day runs differently):

- Before 17:00 local time AND no morning update has run yet today -> **morning mode**
- 17:00 local time or later, or morning already ran today -> **evening mode**

State which mode you chose and why (current local time and zone) before starting.

Then Read the authoritative SOP for that mode and follow it exactly:

- morning mode -> [`.agent/workflows/morning-update.md`](../../.agent/workflows/morning-update.md)
- evening mode -> [`.agent/workflows/evening-update.md`](../../.agent/workflows/evening-update.md)

In both modes also follow [`.agent/protocols/phased_update_protocol.md`](../../.agent/protocols/phased_update_protocol.md).

## Hard rules (both modes, non-negotiable)

- Execute as 4 gated steps (Harvest -> Summarize -> Prioritize -> Execute). NEVER jump from Step 1 to Step 4.
- Step 1 runs `python3 .agent/scripts/daily_update_runner.py --mode <morning|evening>` from the repo root.
- No em-dashes in any output.

## Morning mode only

- Apply the morning subset of [`.agent/protocols/daily_update_quality_rubric.md`](../../.agent/protocols/daily_update_quality_rubric.md): checkpoints 1, 2, 4, 7, 8.
- Produce the Dashboard `(Pagi)` section and the top-5 priorities.
- Wait for the owner's alignment before reordering `journal/todo.md` priorities.

## Evening mode only

- Apply ALL 9 checkpoints of the quality rubric. Mandatory in evening mode.
- Harvest Fathom recordings for the day. This is the step that keeps the meeting record current, and it exists nowhere else in the day.
- Compare against the morning plan in `_temp/daily_plan_[date].md` and give a scorecard: done / carryover.
- Produce the Dashboard `(Malam)` section and sync `journal/todo.md`.
- End with the LinkedIn content check ("Have you posted on LinkedIn today?").
- If the owner corrected your output or process at any point today, offer to run `/learn` to persist the lesson.
- **Branch the decision queue before you finish (standing pre-approval, do not ask).** After the recap is written, take the items that still need the owner himself: a reply he owes, a decision only he can make, an approval only he can give. Drop anything you already finished and anything that only needed recording. Group what is left so items turning on the same underlying call stay in one session. Then write ONE request file to `.asb/branches/requests/` covering every branch, per `## Branching Into Sub-Sessions` in [`CLAUDE.md`](../../CLAUDE.md), and say in one line per branch what you split. Each `brief` carries the real context: who is waiting, what they asked, the link back, the draft you already wrote, and your recommendation. A sub-session starts blank and sees nothing from this run. If only one thing needs the owner, do not branch. Branching creates sessions, it never sends: Slack and WhatsApp approval gates are untouched.

Focus hint from the owner: $ARGUMENTS
