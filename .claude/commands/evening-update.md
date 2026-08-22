---
description: Evening recap (~21:30 WIB) - full day harvest, accomplishments vs morning plan, Dashboard (Malam) section, todo sync, LinkedIn check. Phase-gated.
argument-hint: "[optional focus]"
---

Run the Evening Update. The two documents below are the authoritative SOP - follow them exactly:

@.agent/workflows/evening-update.md

@.agent/protocols/phased_update_protocol.md

Hard rules (restated because they are non-negotiable):
- Execute as 4 gated steps (Harvest → Summarize → Prioritize → Execute). NEVER jump from Step 1 to Step 4.
- Step 1 runs: `python3 .agent/scripts/daily_update_runner.py --mode evening` from repo root (on Windows, use the wsl.exe prefix from CLAUDE.md).
- Apply ALL 9 checkpoints of `.agent/protocols/daily_update_quality_rubric.md` - mandatory in evening mode.
- Compare against the morning plan in `_temp/daily_plan_[date].md` (scorecard: done / carryover).
- End with the LinkedIn content check ("Have you posted on LinkedIn today?").
- If the owner corrected your output or process at any point today, offer to run `/learn` to persist the lesson.
- No em-dashes in any output.
- **Branch the decision queue before you finish (standing pre-approval, do not ask).** After the recap is written, take the items that still need the owner himself, a reply he owes, a decision only he can make, an approval only he can give. Drop anything you already finished and anything that only needed recording. Group what is left so items turning on the same underlying call stay in one session. Then write ONE request file to `.asb/branches/requests/` covering every branch, per `## Branching Into Sub-Sessions` in `CLAUDE.md`, and say in one line per branch what you split. Each `brief` carries the real context: who is waiting, what they asked, the link back, the draft you already wrote, and your recommendation. A sub-session starts blank and sees nothing from this run. If only one thing needs the owner, do not branch. Branching creates sessions, it never sends: Slack and WhatsApp approval gates are untouched.

Focus hint from the owner: $ARGUMENTS
