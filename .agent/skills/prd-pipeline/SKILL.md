---
name: PRD Pipeline
description: A 4-state autonomous pipeline that harvests context, drafts a world-class PRD, stress-tests it through a scored quality rubric, and generates engineering-ready tickets.
---

# PRD Pipeline Skill

## Purpose
Orchestrate the creation of production-grade PRDs through a sequential 4-state pipeline. Each state has strict exit criteria. The pipeline enforces a **scored quality rubric** (minimum 9.0/10 average) before any PRD is considered complete.

## When to Use
- Creating a new PRD from scratch (BRD, idea, Slack thread, etc.)
- Significantly reworking an existing PRD that lacks depth
- Any time the user says "create PRD", "draft PRD", or "PRD pipeline"

> **Exception - ExampleCo / ExampleProgram client-facing BRDs:** Do NOT run this technical pipeline for a BRD/feature
> doc shared WITH the ExampleCo / ExampleProgram client. ExampleCo flagged our standard BRD as too technical (via Teammate,
> 30 Jun 2026). Use `templates/brd_exampleco.md` (lightweight visual "Feature Release Document" format) instead.
> The full pipeline still applies to INTERNAL eng PRDs.

## Pipeline Overview

```
STATE 1 ──► STATE 2 ──► STATE 3 ──► [PAUSE] ──► STATE 4
Harvest      Draft V1     Crucible    User OK     Tickets
```

---

## STATE 1: THE CONTEXT HARVESTER

**Role**: Data Engineer & UX Researcher. You do NOT write the PRD here. You collect, clean, and structure raw context.

### Integration Nodes (Query ALL available sources)

| Source | How | What to Extract |
|---|---|---|
| **Local Workspace** | `find_by_name`, `grep_search`, `view_file` on `product-second-brain/` | Existing PRDs, BRDs, meeting notes, backlogs, related markdown files |
| **Google Drive** | Secondary Drive Connector (`search`, `read`) | Related docs, research, meeting transcripts, decision logs |
| **Slack** | Secondary Slack Connector (`history`) | Recent discussions in relevant channels — pain points, feature requests, complaints |
| **ClickUp** | ClickUp Connector (`get_tasks`, `get_list`) | Existing tickets, epics, bug reports, backlog items related to the project |
| **User Input** | The raw idea, brief, or BRD provided by the user | Core intent and constraints |

### Signal Filtering Rules
1. **Keep**: Pain Points, Feature Requests, Technical Debt, Business Metrics, User Verbatims (direct quotes), Architectural Constraints
2. **Discard**: Off-topic chatter, resolved/closed items, duplicate signals
3. **Tag & Score**: Group findings by theme. Assign Severity/Impact score (1-10) to each pain point.

### Output Format (Structured JSON)
```json
{
  "harvested_context": {
    "project_name": "string",
    "sources_queried": ["local", "gdrive", "slack", "clickup"],
    "core_problems_identified": [
      { "problem": "string", "source": "string", "impact_score": 1-10 }
    ],
    "user_verbatims": ["direct quotes from users/tickets/slack"],
    "technical_constraints": ["from codebase, architecture, or existing systems"],
    "business_metrics": ["cost figures, headcount, SLA targets"],
    "existing_work": ["links to related PRDs, tickets, docs"]
  }
}
```

### Exit Criteria
- JSON payload is generated and non-empty
- At least 2 sources queried (local + at least one external)
- At least 3 core problems identified
- **Optional checkpoint**: If invoked with `--confirm-harvest`, PAUSE here and present the JSON to the user for validation before proceeding to STATE 2

---

## STATE 2: THE STRATEGIST (PRD Drafting)

**Role**: Senior Product Manager. Draft PRD V1 using the harvested context.

### Mandatory Rules
1. **Follow the template**: Use `templates/prd_template.md` as the structural backbone. Every section must be filled — NO "TBD" or "To be defined" allowed.
2. **Use the Marketplace Product Manager skill** as a quality reference (see `marketplace-product-manager/SKILL.md`).
3. **Gherkin is mandatory**: Every user story must have Given-When-Then acceptance criteria where every `Then` clause is objectively measurable.
4. **INVEST validation**: Each user story must pass INVEST criteria (Independent, Negotiable, Valuable, Estimable, Small, Testable). For individual story refinement, reference the `user-story-writer/SKILL.md` skill.
5. **Mermaid diagrams required**: At least 1 architecture diagram and 1 user flow diagram.
6. **Quantify everything**: No vague statements like "improve performance." Use numbers, percentages, time bounds.
7. **No emdashes (`—`)**: Use regular hyphens (`-`) exclusively for all formatting and punctuation.
8. **PRD rules**: Follow `prd_rules.md` (Ed Biden's 23 rules). Pick the tier (S/M/L) first. Tier S skips the Crucible score and the 8-story minimum. Page one is the Problem page and names no solution; it is shown to the owner alone and agreed before the rest is drafted. Write the success metric before the features.

### Output
- A complete PRD V1 written to the appropriate `Clients/[Client]/[Project]/` directory.
- File naming: `PRD_[Project_Name].md`

### Exit Criteria
- PRD V1 follows the template structure with zero empty sections
- Contains ≥ 8 Gherkin user stories covering happy paths, unhappy paths, edge cases, and admin/supervisor perspectives
- Contains ≥ 1 architecture diagram and ≥ 1 flow diagram

---

## STATE 3: THE CRUCIBLE (Quality Assurance Loop)

**Role**: Devil's Advocate. Your ONLY input is the PRD V1 text. You have NO access to the original user brief, harvester JSON, or any instructions given to the Strategist. You are an independent auditor.

### The Quality Rubric (6 Dimensions)

Each dimension is scored 1-10. Detailed scoring criteria are in `templates/quality_rubric.md`.

| # | Dimension | What It Measures |
|---|---|---|
| 1 | **Completeness** | All template sections filled. No gaps, no TBDs, no "see above." |
| 2 | **Depth & Specificity** | Concrete numbers, field-level specs, validation rules. Not vague. |
| 3 | **Edge Cases & Unhappy Paths** | ≥ 5 documented failure scenarios with explicit handling. |
| 4 | **Testability** | Every AC is Gherkin. Every `Then` is measurable by QA. No subjective criteria. |
| 5 | **Architectural Clarity** | Data flows, integration contracts, sequence diagrams. An engineer can build from this. |
| 6 | **Operational Readiness** | Rollout plan, monitoring, rollback, SLA definitions, alerting. |

### PASS / FAIL Criteria

> [!CAUTION]
> **PASS requires ALL of the following:**
> - Average score across all 6 dimensions ≥ **9.0**
> - No single dimension scores below **7**
> - Zero unresolved critical or high-severity findings
>
> **If FAIL**: The Devil's Advocate produces a numbered list of specific deficiencies per dimension. The Strategist revises the PRD to address each deficiency. Re-score.
> **Maximum revision loops**: 3. If still FAIL after 3 loops, PAUSE and escalate to user with the current scores and remaining gaps.

### Critique Checklist (Devil's Advocate MUST answer)
1. Can an engineer build this WITHOUT asking a single clarifying question?
2. If a customer does something unexpected (double-click, back button, timeout, paste garbage), is it handled?
3. Is there a monitoring story? How do we know this is working in production?
4. Is rollback defined? If we deploy and it breaks, what happens in the next 5 minutes?
5. Are permissions airtight? Can a lower-role user escalate or access restricted data?
6. Are success metrics tied to specific instrumentation? HOW will we measure each KPI?
7. Is the data model implicit or explicit? Are schemas defined?
8. What happens at scale? (10x current volume)
9. What are the dependencies? If dependency X is down, what happens?
10. Is there a clear phase boundary? What ships in V1 vs V2?
11. Does page one name a solution? (Fail if a feature, screen or service appears in section 0.)
12. Is the segment specific (tenant, role, market, volume), or just "users"?
13. Are there at least 3 non-goals, each with a reason?
14. Are rejected options written down with who decided and when?
15. Is there exactly one owner, with a RACI for everyone else?
16. Does every risk have a mitigation that is an action, not "monitor"?
17. Tier L only: is there a press release and a launch checklist covering support, legal, marketing and FAQ?
18. Is any section padding? Name the sections a reader could delete without losing a decision.

### Output
- Scored rubric table with justification per dimension
- Numbered list of deficiencies (if any)
- Final verdict: PASS or FAIL
- If PASS: PRD V2 (revised) written to the same file path, overwriting V1

### Exit Criteria
- Rubric score ≥ 9.0 average, no dimension < 7
- OR 3 revision loops exhausted → escalate to user

---

## [PAUSE FOR APPROVAL]

After STATE 3 completes with a PASS, present to the user:
1. The final rubric scores
2. A 3-line summary of what changed between V1 and V2
3. Ask: **"Type EXECUTE to proceed to ticket generation, or provide feedback."**

Do NOT proceed to STATE 4 without explicit user approval.

---

## STATE 4: TICKET GENERATION & HANDOFF

**Role**: Engineering Program Manager. Break the approved PRD into actionable work items.

### Output Structure
```json
{
  "tickets": [
    {
      "type": "Epic | Task | Sub-task",
      "parent": "Epic ID (if sub-task)",
      "title": "string",
      "description": "string",
      "acceptance_criteria": [
        "Given ... When ... Then ..."
      ],
      "priority": "P0 | P1 | P2",
      "phase": "Phase 1 | Phase 2 | Phase 3 | Phase 4",
      "estimated_effort": "S | M | L | XL",
      "dependencies": ["ticket IDs or external systems"]
    }
  ]
}
```

### Rules
1. Every PRD requirement (Req ID) must map to at least one ticket.
2. Epics correspond to major feature groups (e.g., "Unified Inbox", "Routing Engine").
3. Tasks are independently deliverable and deployable units.
4. Sub-tasks are implementation steps within a task.
5. Acceptance Criteria are copied directly from the PRD Gherkin stories where applicable.

### Exit Criteria
- Every Req ID from the PRD is covered by at least 1 ticket
- Ticket array is valid JSON
- User is offered the option to push tickets to ClickUp via the ClickUp Connector skill

---

## Rules of Engagement

1. **Zero Fluff**: No preambles ("Sure, I'll help!"). Output results directly.
2. **Context Isolation**: The Devil's Advocate in STATE 3 must NEVER see the original user brief or harvester output. It only receives the PRD text.
3. **Pause for Approval**: Always pause after STATE 3. Never auto-execute STATE 4.
4. **Preserve & Expand**: When revising, never delete existing detail unless factually wrong. Always ADD depth. "Add, Improve, Do Not Remove."
5. **Cite Sources**: When harvesting, always note which source (Slack thread, ClickUp ticket, Drive doc) each finding came from.
