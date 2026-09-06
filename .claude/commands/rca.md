---
description: Product - Incident postmortem under the Iron Law - no root cause, no "resolved"
argument-hint: "<the incident, or a path to the thread/MOM>"
---

Run the four-phase RCA. Authoritative SOP: [`.agent/protocols/incident_rca.md`](../../.agent/protocols/incident_rca.md). Follow it exactly.

**Iron Law: no root cause, no "resolved".** Symptoms are not causes. Mitigations are not root
causes. A restart that made the error stop is a mitigation, and calling it resolved is how the
same incident happens again next month.

## The four phases, in order, no skips

1. **Investigate.** Build the timeline in WIB timestamps. Pull real evidence: Slack permalinks,
   the Fathom recording, logs, the exact error text quoted. State the blast radius: who was
   affected, how many, for how long.
2. **Analyze.** Five Whys down to a cause. Separate the trigger (what set it off this time) from
   the root cause (what made it possible at all). List which causes are confirmed and which are
   still suspected, and label them that way.
3. **Verify.** Confirm the root cause explains the WHOLE timeline: every symptom, the recovery,
   and any recurrence. Quote the evidence. A cause that explains four of five symptoms is not the
   cause yet.
4. **Prevention.** Exactly three actions, each with what, owner, due date, and ticket link. Split
   across Detect, Prevent, Mitigate. Each one attacks the root cause, not the symptom.

## Output

```
# RCA: <incident> - <date WIB>   [RESOLVED | PRELIMINARY]
## Summary          three lines: what broke, impact, root cause
## Timeline         WIB, timestamped, evidence linked
## Root cause       trigger vs latent cause, evidence quoted
## Prevention       3 actions: what / owner / due / ticket, tagged Detect|Prevent|Mitigate
## Open questions   only if PRELIMINARY
```

Mark it `PRELIMINARY` and list the open questions when the root cause is not verified. That is an
honest state and it is allowed. `RESOLVED` on an unverified cause is not.

## Then file it

The three prevention actions are real obligations, so they get real records in the same turn:
the owner's own go to the commitment ledger, someone else's go to the waiting watchdog when they block
the owner. Every record carries a work-tree node. Link each action to its ticket via `/ticket`.

Incident: $ARGUMENTS
