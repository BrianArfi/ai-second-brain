---
description: Thinking - Adversarial review - 5 hostile critics stress-test a plan/decision/draft in parallel, then keep only what survives
argument-hint: "<plan/decision/draft, or path to the doc>"
---

Read `.agent/skills/hyperplan/SKILL.md` and follow it exactly. It is the single source of truth for the adversarial-review procedure: frame the target, spawn 5 `hyperplan-critic` subagents in parallel (one lens each: skeptic, stakeholder, evidence, strategist, contrarian; strategist + contrarian on `model: opus`), then synthesize survivors vs refuted in the main loop and give a GO / GO WITH CHANGES / RETHINK verdict.

Use this for high-stakes or hard-to-reverse work (a decision, a PRD before sign-off, a pitch to YourManager, a roadmap commitment). For routine pre-send checks use `draft-reviewer` instead. This is judgment-heavy synthesis: if the session is on a low-tier model, tell the owner before starting.

Target: $ARGUMENTS
