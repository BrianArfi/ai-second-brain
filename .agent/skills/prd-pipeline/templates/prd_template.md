# PRD Template

> This template is used by the Strategist Agent in STATE 2. Every section is MANDATORY. No section may contain "TBD", "To be defined", or be left empty.
>
> Follow `../prd_rules.md` (Ed Biden's 23 rules). Exceptions to "every section is mandatory": a tier S PRD keeps only section 0, section 2, acceptance criteria and the owner; section 12 (Impact) stays empty until 30 days after launch; delete any section that does not apply to the tier, never pad it. A tier L PRD opens with a one-paragraph press release and 3 customer FAQs above section 0.

---

| Field | Details |
| :--- | :--- |
| **Project** | [Project Name] |
| **Project Code** | [Code] |
| **Status** | Draft |
| **Owner** | [ONE name. Everyone else goes in the RACI table] |
| **Tier** | S / M / L (see `prd_rules.md`) |
| **Stage** | Problem / Solution / Launch / Impact |
| **Links** | [Figma, Linear/Jira tickets, MOMs, Fathom recordings, DEC ids, artifacts] |
| **Date** | [Date] |
| **Priority** | P0 / P1 / P2 |
| **Dependencies** | [List all external teams/systems] |

---

## 0. Problem (page one, no solution allowed)

> Rules 1, 2, 4, 11 in `prd_rules.md`. Name no feature, screen or service here. Show the owner this page alone first. Grow the PRD only after he agrees it.

| Field | Details |
| :--- | :--- |
| **Problem** | [What is broken or missing, in the user's terms] |
| **Segment** | [Who exactly: tenant, role, market, volume. Never "users"] |
| **Evidence** | [Ticket, Slack thread, MOM, Fathom link or data point. Assumed? Say so] |
| **Workaround today** | [What they do instead now] |
| **Why now** | [Cost of waiting, with a number] |

### Non-goals

| Non-goal | Why it is out |
| :--- | :--- |
| [At least 3 rows] | [Reason] |

---

## 1. Executive Summary

### The "Why"
[2-3 paragraphs. What is the business problem? Why does it matter NOW? Include quantified pain — cost, time, headcount, SLA breaches. Cite sources from the harvested context.]

### The "What"
[1-2 paragraphs. What are we building? High-level scope. What is explicitly OUT of scope?]

### Expected Impact
[Bullet list of measurable outcomes. Every bullet must have a number.]

---

## 2. Success Metrics & KPIs

| Metric | Baseline | Target | How Measured |
| :--- | :--- | :--- | :--- |
| [Metric 1] | [Current value] | [Target value] | [Instrumentation method] |

> Every metric must have a defined instrumentation method. If we can't measure it, we can't ship it.

---

### RACI

| Person | Role in this PRD (R / A / C / I) |
| :--- | :--- |
| [Name] | [R/A/C/I] |

---

## 3. Users, Roles & Permissions (RBAC)

| Role | Capabilities | View Scope | Authentication |
| :--- | :--- | :--- | :--- |
| [Role 1] | [What they can do] | [What they can see] | [SSO/2FA/Token] |

> Include the "System" role for automated actions (cron jobs, webhooks, background workers).

---

## 4. System Architecture

### 4.1 Architecture Diagram
```mermaid
graph TD
    A[Source] --> B[Processing]
    B --> C[Storage]
    C --> D[Presentation]
```
> Replace with actual architecture. Show data flows, queues, databases, external APIs.

### 4.2 Integration Contracts
| Integration | Direction | Protocol | Payload | Error Handling |
| :--- | :--- | :--- | :--- | :--- |
| [System A] | Inbound/Outbound | REST/Webhook/gRPC | [Link to schema] | [Retry/DLQ/Circuit breaker] |

### 4.3 Data Model (Key Entities)
| Entity | Key Fields | Relationships |
| :--- | :--- | :--- |
| [Entity 1] | [field1, field2, ...] | [FK to Entity2] |

---

## 5. Detailed Feature Specifications

### Phase 1: [Phase Name] (P0 — Must Have)

#### 5.1 [Feature Group Name]
| Req ID | Feature | Description | Validation Rules |
| :--- | :--- | :--- | :--- |
| **XX-01** | [Feature] | [Detailed description] | [Field constraints, limits] |

### Phase 2: [Phase Name] (P1 — Should Have)
[Same table format]

### Phase 3+: [Phase Name] (P2 — Nice to Have)
[Same table format]

---

## 6. User Flows

### Flow 1: [Happy Path Name]
1. [Step-by-step numbered flow]
2. [Include decision points]

### Flow 2: [Unhappy Path / Edge Case Name]
1. [Step-by-step describing failure scenario]
2. [How the system recovers]

> Minimum 2 happy paths and 3 unhappy paths required.

---

## 7. User Stories (Gherkin)

> Minimum 8 stories required. Must cover: Happy paths, Unhappy paths, Edge cases, Admin/Supervisor perspectives.

**US.X.X ([Story Title])**
- **Given** [precondition with specific values]
- **And** [additional precondition if needed]
- **When** [user action — specific and concrete]
- **Then** [observable, measurable outcome]
- **And** [additional outcome if needed]

> Every `Then` must be testable by QA without interpretation. Bad: "system works correctly." Good: "response status is 200 AND payload contains `ticket_id` field."

---

## 8. Non-Functional Requirements

| Category | Requirement | Target | Measurement |
| :--- | :--- | :--- | :--- |
| **Performance** | [e.g., API latency] | [e.g., < 200ms p95] | [APM tool] |
| **Security** | [e.g., PII handling] | [e.g., encrypted at rest] | [Audit] |
| **Availability** | [e.g., uptime] | [e.g., 99.9%] | [Monitoring] |
| **Scalability** | [e.g., concurrent users] | [e.g., 10x current] | [Load test] |

---

## 9. Rollout & Migration Plan

| Phase | Scope | Traffic % | Duration | Rollback Trigger | Monitoring |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Canary | [Subset] | 5% | [Time] | [Condition] | [Dashboard/Alert] |

---

## 10. Options Considered and Decision Log

| Option | Why rejected | Decided by | Date |
| :--- | :--- | :--- | :--- |
| [Option] | [Reason] | [Name] | [Date] |

| Question | Call | Who | When | DEC id |
| :--- | :--- | :--- | :--- | :--- |
| [Question] | [Decision] | [Name] | [Date] | [DEC-xxxx] |

---

## 11. Launch Checklist

| Area | What | Owner | Date |
| :--- | :--- | :--- | :--- |
| Support | [Brief, macros, escalation path] | [Name] | [Date] |
| Legal | [Review needed?] | [Name] | [Date] |
| Marketing | [Client or tenant note] | [Name] | [Date] |
| FAQ | [Top 5 questions] | [Name] | [Date] |

---

## 12. Impact (fill 30 days after launch)

| Metric | Before | After | Verdict | Next step |
| :--- | :--- | :--- | :--- | :--- |
| [From section 2] | [Value] | [Value] | Moved / Flat / Worse | [Action] |

---

## 13. Open Questions & Risks

| # | Question / Risk | Likelihood / Impact | Mitigation (not "monitor") | Owner | Due Date | Status |
| :--- | :--- | :--- | :--- | :--- |
| 1 | [Specific question] | [H/M/L] | [Action] | [Person] | [Date] | Open / Resolved |
