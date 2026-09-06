---
name: Jira Connector
description: A custom skill to query sprint progress across ExampleVendor and Work Incentives Atlassian instances, identify developer bottlenecks, and auto-generate Markdown summaries for the daily update.
---

# Jira Connector Skill

This custom skill provides direct integration with Jira Software across multiple instances (ExampleVendor and Work Incentives Atlassian domains) under Your Name's management. It parses active sprints, tracks ticket distributions, and alerts on critical resource bottlenecks.

## Capabilities

1. **Dual-Instance Agile Routing**: Queries ExampleVendor and Work Incentives Jira boards dynamically.
2. **Workload Analysis & Alerts**: Flags if any single team member holds more than 40% of active sprint tickets.
3. **Status Standardization**: Maps custom project workflows to uniform statuses (`TO DO`, `IN PROGRESS`, `UNDER REVIEW`, `DONE`).
4. **Daily digest output**: Compiles clean Markdown digests suitable for inclusion in [`daily_update_output.md`](../../../daily_update_output.md).

## Integration Setup

The credentials for Jira are stored in the active Atlassian credentials in the workspace (`credentials.json` or environment variables).

- **Instance A**: `examplevendor.atlassian.net` (MSP, MBA, STOR)
- **Instance B**: `yourcompany.atlassian.net` (MP, MPS)

5. **Portfolio-scoped sprint status**: each board belongs to exactly one of the owner's four portfolios, so `PORTFOLIO_BOARDS` is the mechanical portfolio boundary (`marketplace`->MP, `platform`->MPS, `b2c`->MBA, `ecom-solution`->MSP+STOR). Never mix boards across portfolios in one review.

## Usage

Run the connector from the workspace root:

```bash
python .agent/skills/jira-connector/scripts/jira_client.py daily-digest

# active-sprint snapshot for ONE portfolio, as JSON (used by premeeting-cards)
python .agent/skills/jira-connector/scripts/jira_client.py sprint-status \
    --portfolio marketplace --stale-before 2026-07-21
```

`sprint-status` returns per board: sprint name + end date, total/done/open counts,
`by_status`, `by_assignee` (descending, for concentration checks), `open_issues`,
and `stale` (open issues not updated since `--stale-before`). Sprint issues are
paginated, so busy boards are not silently truncated at 100.

## Editing a ticket

Everything below runs against the same `token.env`, and the **site is derived from the
issue key**, so `--domain` is only for a key this map does not know:

| Prefix | Site |
| :--- | :--- |
| `MP`, `MPS` | `yourcompany.atlassian.net` |
| `MSP`, `MBA`, `STOR` | `examplevendor.atlassian.net` |

**Creating on MPS: the service token is rejected.** On 1 Sep 2026 `create-issue --project MPS` returned
400 "The target project doesn't exist or you don't have permission to create issues in it," while the same
account reads MPS fine and `Task` is a valid type there. So it is a create permission on the token, not a bad
payload. Until that token is fixed, create through the owner's own Atlassian account with
`mcp__claude_ai_Atlassian__createJiraIssue` (`cloudId: yourcompany.atlassian.net`, `contentFormat: markdown`,
priority through `additional_fields`). That route also files the ticket as the owner rather than as a service user.
Reads, comments and transitions through the script are unaffected.

**Read first. Always.** A ticket usually has comments that change what the edit should say.

```bash
# the ticket, readable, description flattened out of ADF
python3 .agent/skills/jira-connector/scripts/jira_client.py get-issue ABC-123
python3 .agent/skills/jira-connector/scripts/jira_client.py get-issue ABC-123 --raw

# comments, with their ids
python3 .agent/skills/jira-connector/scripts/jira_client.py comment ABC-123 --list
```

**Write.** Every write needs `--approved`, exactly like `create-issue` and like a Slack
send. `--dry-run` prints the payload and writes nothing, and works without approval.

```bash
# change fields; only what you pass is touched
python3 .agent/skills/jira-connector/scripts/jira_client.py edit-issue ABC-123 \
    --summary "..." --description-file /tmp/spec.md --dry-run
python3 .agent/skills/jira-connector/scripts/jira_client.py edit-issue ABC-123 \
    --description-file /tmp/spec.md --approved

# comment, or replace one that is now wrong
python3 .agent/skills/jira-connector/scripts/jira_client.py comment ABC-123 \
    --text-file /tmp/note.md --approved
python3 .agent/skills/jira-connector/scripts/jira_client.py comment ABC-123 \
    --comment-id 368254 --text-file /tmp/note.md --approved

# move it
python3 .agent/skills/jira-connector/scripts/jira_client.py transition ABC-123 --list
python3 .agent/skills/jira-connector/scripts/jira_client.py transition ABC-123 \
    --to "In Progress" --approved
```

### Rules

1. **Use this, not the Atlassian MCP, for writes.** The MCP needs a raw `cloudId` that
   nobody remembers, and it has no approval gate, so a write can go out with no sign-off.
2. **`--label` and `--component` REPLACE the whole list.** To keep the existing labels and
   add one, use `--add-label`.
3. **Correct a wrong comment, do not stack another one on top.** Pass `--comment-id`. A
   ticket that contradicts itself sends the engineer to the wrong spec, and the wrong
   version is the one they read first. This is why `--list` prints ids.
4. **Long descriptions go in a file.** `--description-file` and `--text-file` take markdown
   and convert to ADF, so no shell escaping and no lost newlines.
5. **A ticket edit is a tracked action.** Update the matching ledger record in the same
   turn, per `CLAUDE.md` "Ledger Discipline".
