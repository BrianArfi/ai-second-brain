---
name: Linear Connector
description: Query Work's Linear workspace (linear.app/yourcompany) for teams, projects, cycles and issues, generate the daily digest with the bottleneck alert, feed the work-tree ticket dump, and create/update/comment on issues behind an approval gate.
---

# Linear Connector Skill

Work chose Linear org-wide on 7 August 2026, replacing the ExampleVendor-hosted Jira
boards, with cutover fixed at **25 August**. This connector is the Linear half
of the ticket chain the harness already runs on Jira, so the cutover is a
data-source swap rather than a blackout.

Deliberately shaped as a mirror of `jira-connector`: same portfolio boundary,
same >40% assignee-concentration alert, same digest layout, same flattened
record shape in the dump. A reader of one can read the other.

## Capabilities

1. **Workspace read**: teams, projects, cycles, issue search, single issue with comments.
2. **Cycle status**: `cycle-status` returns the Jira `sprint-status` shape -- counts, `by_status`, `by_assignee` descending, `open_issues`, `stale`.
3. **Bottleneck alert**: flags any assignee holding more than 40% of a cycle's open tickets, the same threshold the Jira digest uses.
4. **Work-tree feed**: `dump_all_issues.py` writes `_temp/linear_all_issues.json` in the same record shape as the Jira dump.
5. **Gated writes**: `create-issue`, `update-issue`, `comment` all refuse to run without `--approved`.

## Setup

1. Generate a key: Linear -> Settings -> Security & access -> API keys -> Personal API key.
2. `cp token.env.example token.env` and paste it in as `LINEAR_API_KEY`. `token.env` is gitignored (`*.env`).
3. `python3 .agent/skills/linear-connector/scripts/linear_client.py verify-connection`
4. `... teams --write` to discover the real team keys and write `teams.json`.

**`teams.json` is discovered, never guessed.** Linear derives issue identifiers
from team keys (`ENG-123`), and this repo did not know Work's until discovery
ran. Two things the write step reports and the owner has to resolve:

- **Jira key collisions.** A Linear team keyed `MP`, `MPS`, `MSP`, `MBA` or `STOR` makes an identifier ambiguous between two systems, and `work_tree_link.py`'s `JIRA_RE` would claim it and link to Atlassian. Reported as `jira_key_collisions`.
- **Portfolio assignment.** `portfolios` starts empty on purpose. Each team belongs to exactly one of the owner's four portfolios, the same mechanical boundary as `PORTFOLIO_BOARDS` in the Jira connector, and filing a team under the wrong one silently mixes portfolios in a review.

## Usage

```bash
L=.agent/skills/linear-connector/scripts/linear_client.py

python3 $L verify-connection
python3 $L teams                       # live list, with collision flags
python3 $L teams --write               # refresh teams.json
python3 $L projects --team ENG
python3 $L cycles --team ENG
python3 $L cycle-status --team ENG --stale-before 2026-08-11
python3 $L cycle-status --portfolio b2c
python3 $L issue ENG-123
python3 $L issues --team ENG --query "checkout" --limit 20
python3 $L daily-digest

# writes -- refuse without --approved
python3 $L create-issue --team ENG --title "..." --description "..." --approved
python3 $L update-issue ENG-123 --state "In Progress" --approved
python3 $L comment ENG-123 --body-file draft.md --approved

# work-tree feed
python3 .agent/skills/linear-connector/scripts/dump_all_issues.py \
    --out _temp/linear_all_issues.json
```

## Gotchas

- **Pagination is not optional.** Linear defaults to 50 nodes per page and caps at 250. Every list query here goes through `paginate()`; a new query that skips it silently truncates a busy team.
- **The API key goes in raw**, not as `Bearer <key>`. The Bearer form is for OAuth tokens and a personal key sent that way fails as a bare 400.
- **HTTP 200 does not mean success.** Linear returns 200 with an `errors` array for a bad field or a permission failure, so `gql()` checks both.
- **Linear identifiers are not ledger ids.** `ENG-123` is external, like a Jira key, so `ledger_link.py` deliberately does not linkify it to the local dashboard.
