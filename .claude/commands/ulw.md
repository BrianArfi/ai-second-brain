---
description: Thinking - Ultrawork mode - plan, fan out parallel subagents, self-verify, drive to a definition of done
argument-hint: "<the task to ultrawork>"
---

Read `.agent/skills/ulw/SKILL.md` and follow it exactly. It is the single source of truth for the ultrawork procedure: frame + definition of done, decompose + route, parallel fan-out, synthesize, adversarial verify, check the definition of done, deliver.

Apply the routing table in CLAUDE.md (`## Subagents`) to pick model + effort per subtask, and the existing quality gates (`draft-reviewer`, `report-auditor`). Keep synthesis and strategy in the main loop. If the task is large and highly parallel, offer to run it as a Workflow instead (ask first).

This is synthesis-heavy orchestration: if the session is on a low-tier model, tell the owner before starting.

Task: $ARGUMENTS
