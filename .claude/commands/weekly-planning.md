---
description: Weekly - Weekly review + planning - synthesize last week across all clients, set next week's priorities. Phase-gated.
argument-hint: "[optional focus]"
---

Run the Weekly Planning session. The two documents below are the authoritative SOP - follow them exactly:

@.agent/workflows/weekly-planning.md

@.agent/protocols/phased_update_protocol.md

Hard rules:
- Execute the phases in order with user gates - NEVER jump from gathering straight to a finished plan.
- Delegate bulk reading (transcripts, MOMs, Slack history) to the `harvester` subagent; synthesis stays with you.
- Cross-reference `journal/todo.md` P0/P1 and `journal/master_followup_tracker.md` (generated view over the PM ledgers) before proposing next week's priorities.
- Include the post-launch outcomes picture: run `python3 .agent/skills/outcomes-loop/scripts/outcomes_loop.py report` and embed it in the review (shipped features vs their success metrics; flag any `needs_reauth` metric to the owner for a Metabase cookie refresh).
- Skim the latest harness health snapshot: `python3 .agent/skills/harness-health/scripts/harness_health.py report` (monthly cron `0 9 1 * *`). If it flags failing/silent cron jobs or stale ledgers, add a fix to next week's plan.
- Read `journal/state/token_efficiency.json` `hotspots` and pick at most one token-efficiency optimization for the coming week (see `.agent/protocols/token_efficiency.md`).
- Wait for the owner's alignment before rewriting todo.md.
- No em-dashes in any output.

Focus hint from the owner: $ARGUMENTS
