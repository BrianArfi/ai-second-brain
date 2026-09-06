---
description: Product - Draft and file a Jira or Linear ticket from a spec, decision or meeting; approval-gated
argument-hint: "<what the ticket is, or a ticket key to update>"
---

Draft the ticket in full, show the owner, then file it only after he approves.

## 1. Pick the tracker, do not guess

**Linear is Work's tracker** (decided 7 Aug 2026, org-wide, cutover 25 August). Jira stays live
for the old-world Marketplace board until that board is archived, so both run in parallel and the
work tree carries tickets from both.

- New Work work -> Linear, [`linear-connector`](../../.agent/skills/linear-connector/SKILL.md)
- Existing `MP-`, `MPS-`, `MSP-`, `MBA-`, `STOR-` key -> Jira, [`jira-connector`](../../.agent/skills/jira-connector/SKILL.md). The issue key determines the site.

Never use the Atlassian MCP for writes. It takes a raw cloudId and has no approval gate.

## 2. Every ticket belongs to a work-tree node

Hard rule. Resolve the node before you draft:

```bash
python3 .agent/scripts/work_tree.py find "<search>"
```

Confident, because the source names the client, drop or initiative: file it and say the node in
one line. Not confident: draft the ticket, file nothing, and put the candidate nodes to the owner in
the batched question at the end of the turn. A ticket under a plausible-but-wrong node is worse
than an unfiled one, because it reads as tracked and never surfaces again.

## 3. Draft it so it can be built

- **Title states the change**, not the area.
- **Description**: the problem, the expected behaviour, and the source. Link the PRD, the MOM, the Slack permalink, the decision id. A ticket with no source becomes an argument in three weeks.
- **Acceptance criteria in Gherkin**, every `Then` measurable. Copy them from the PRD if one exists rather than rewriting from memory.
- **Long descriptions go in a file**, never inline: `--description-file /tmp/spec.md` converts markdown to ADF properly.

## 4. Show the owner, then file

Present the full ticket text, the target project or team, the node, and one line on why it exists.
Wait for explicit approval.

Dry run first on any edit to an existing ticket:

```bash
python3 .agent/skills/jira-connector/scripts/jira_client.py edit-issue ABC-123 \
    --description-file /tmp/spec.md --dry-run
```

Then, with approval in hand, add `--approved`:

```bash
# Jira
python3 .agent/skills/jira-connector/scripts/jira_client.py edit-issue <KEY> --description-file /tmp/spec.md --approved
python3 .agent/skills/jira-connector/scripts/jira_client.py comment <KEY> --text-file /tmp/note.md --approved
python3 .agent/skills/jira-connector/scripts/jira_client.py transition <KEY> --to "In Progress" --approved

# Linear
python3 .agent/skills/linear-connector/scripts/linear_client.py create-issue --team <TEAM> --title "..." --description "..." --approved
python3 .agent/skills/linear-connector/scripts/linear_client.py update-issue <KEY> --state "In Progress" --approved
```

`--approved` is the only signal the owner signed off on this specific ticket. There is no environment
variable bypass. Add it only once approval is actually in hand.

Two gotchas that have bitten before: `--label` and `--component` REPLACE the whole list, so use
`--add-label` to keep what is there. To correct a wrong comment use `--comment-id`, do not stack
another comment on top.

## 5. Record it in the same turn

Filing or transitioning a ticket is a tracked action. Update the matching ledger record now, not
later, via the CLI. If it genuinely maps to no tracked item, say so in one line.

Ticket: $ARGUMENTS
