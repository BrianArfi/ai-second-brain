---
description: Weekly - Weekly progress report for your manager - harvest first via subagent, audit against a rubric, update the existing Drive doc in place
argument-hint: "[week-ending date, defaults to this Friday]"
---

Generate the weekly progress report. Authoritative SOP for structure, styling, and Drive sync:

@.agent/skills/weekly-report-generator/SKILL.md

Hard rules:

1. **Harvest first, never synthesize while harvesting.** Run the harvest in parallel: for each meeting recording that week, spawn a `meeting-harvester` subagent, which isolates one transcript and returns raw facts. For everything else (written minutes, Dashboard daily sections, `journal/todo.md`, the relevant channels), spawn the `harvester` subagent. Wait for all raw facts before drafting a line.
2. **Weight by delivered milestones, then unblocked blockers, then active risks, then ongoing work.** Recency is NOT importance. Cross-reference the P0 and P1 items in `journal/todo.md`.
3. No em-dashes. This is synthesis-heavy work, so if the session is on a low-tier model, say so before drafting.
4. Before presenting, spawn the `report-auditor` subagent with the draft and the source list. Include its scorecard in what you show. Fix anything it marks NOT READY first.
5. After approval, UPDATE the existing report doc in place, same file id, and add a changelog row. Never create a duplicate.
6. Confirm with the file id and the Drive link. No id back means it failed.

Week ending: $ARGUMENTS
