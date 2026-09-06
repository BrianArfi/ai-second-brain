---
description: Daily - Daily update - auto-detects WIB time; morning prep before 17:00 WIB (or if no morning ran yet), evening recap after
argument-hint: "[optional focus, or 'morning'/'evening' to force a mode]"
---

Determine current WIB time first: run `TZ=Asia/Jakarta date '+%H:%M %A %Y-%m-%d'`.

If $ARGUMENTS forces a mode ("morning" or "evening"), obey it. Otherwise (the owner's rule: morning until 17:00 WIB, since his work window starts ~12:30):

- Before 17:00 WIB AND no morning update has run yet today -> **morning mode**
- 17:00 WIB or later, or morning already ran today -> **evening mode**

State which mode you chose and why (current WIB time) before starting.

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
