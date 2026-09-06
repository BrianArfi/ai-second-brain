---
description: Weekly - Work Weekly Progress Report for YourManager - harvest first via subagent, audit against rubric, update existing Drive doc in place
argument-hint: "[week-ending date, defaults to this Friday WIB]"
---

Generate the Work Weekly Progress Report. Authoritative SOP for structure, styling, and Drive sync:

@.agent/skills/work-weekly-report/SKILL.md

Also apply the format facts in harness memory `project_work_weekly_report_format.md` (5 sections, status icons, table layout, upload flow).

Hard rules:
1. **Harvest first, never synthesize while harvesting.** Run the harvest in parallel: for each Fathom recording that week, spawn a `meeting-harvester` subagent (it isolates each transcript and returns raw facts); for the rest (written MOMs, Dashboard daily sections, `journal/todo.md`, relevant Slack channels), spawn the `harvester` subagent. Wait for all raw facts before drafting anything.
2. Weight by: delivered milestones > unblocked blockers > active risks > ongoing work. Recency is NOT importance. Cross-reference todo.md P0/P1 designations.
3. Draft in ENGLISH. No em-dashes. This is synthesis-heavy work - if the session is on a low-tier model, tell the owner before drafting.
4. Before presenting: spawn the `report-auditor` subagent with the draft + the source list. Include its scorecard in what you show the owner. Fix anything NOT READY first.
5. After the owner approves: upload via the Drive Update Protocol - UPDATE the existing weekly report doc in place (same file ID), never create a duplicate, add a changelog row.
6. Confirm with the file ID + Drive link.

Week ending: $ARGUMENTS
