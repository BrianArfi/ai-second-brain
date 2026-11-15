# PRD Rules (23)

> Source: Ed Biden, "/prd-rules: My PRD is better than yours. 23 rules" (adopted by the owner 2 Nov 2026). Attributions in brackets are Ed's. Each rule says where it lands in this harness. `/prd` (`.claude/commands/prd.md`) and the Crucible (`SKILL.md` State 3) enforce them.

## Before you write a word

| # | Rule | How it lands here |
| :--- | :--- | :--- |
| 1 | Problem first. No solution allowed on page one. [Intercom] | Page one of every PRD is the Problem page (template section 0). It names no feature, screen or service. |
| 2 | Haven't picked the problem yet? You don't need a PRD. | No agreed problem means no PRD. Write a problem note or a decision record (`decision_log.py add`) and stop. |
| 3 | Write the success metric before you write the feature. [Lenny Rachitsky] | Section 2 (metrics) is filled and agreed before section 5 (features) is drafted. |
| 4 | Say who it's for. A segment, not "users". [Product Hunt] | Problem page names the segment: tenant, role, market, volume. "Users" or "customers" alone fails the Crucible. |
| 5 | Write the non-goals. Most teams skip it. Most need it. [Asana] | Non-goals table is mandatory, minimum 3 rows, each with a reason. |
| 6 | Big launch? Write the press release first. [Amazon] | Tier L PRDs open with a one-paragraph press release and 3 customer FAQs before section 1. |

## Write it as you go, not in one go

| # | Rule | How it lands here |
| :--- | :--- | :--- |
| 7 | Start the PRD the day the idea hits the backlog. | A new backlog idea that the owner owns gets a Problem page stub the same day, filed under its work-tree node. |
| 8 | Fill it in at each product review: problem, solution, launch, impact. | The header table carries `Stage: Problem / Solution / Launch / Impact`. Each review moves it one stage. |
| 9 | Order sections from least likely to change to most. [Linear] | Template order: problem, metric, non-goals, users, then solution, specs, stories, rollout. Stable on top. |
| 10 | Match the detail to the risk. A 2-day fix isn't a 2-month project. | Pick a tier first (table below). Tier S skips the full pipeline. |
| 11 | Stay on one page until the problem is agreed. Then grow it. [Adam Thomas] | Problem gate: show the owner the Problem page alone. Grow the PRD only after he agrees it. |

## Make decisions stick

| # | Rule | How it lands here |
| :--- | :--- | :--- |
| 12 | Log every decision: question, call, who, when. [Adam Waxman] | Decision log section in the PRD, and each call also goes to `decision_log.py` with a `DEC-` id. |
| 13 | Write down the options you rejected, so nobody reopens them. [Linear] | "Options considered" table: option, why rejected, who decided, date. |
| 14 | Name the risks and how you'll reduce them. [Steve Morin] | Risks table: risk, likelihood, impact, mitigation, owner. "Monitor" alone is not a mitigation. |
| 15 | One owner. RACI everyone else. | Header has exactly one Owner. A RACI table lists everyone else. |
| 16 | Link everything to it. One link should answer any question. | Header links the Figma, tickets (Linear/Jira), MOMs, Fathom recordings, DEC ids and artifacts. The tickets link back to the Doc. |

## Keep it alive

| # | Rule | How it lands here |
| :--- | :--- | :--- |
| 17 | If engineers and designers aren't opening it, something's gone wrong. | At each review, ask the tech lead and designer if they used it this sprint. If not, find out why and fix the Doc, not the people. |
| 18 | Every section you add, fewer people read it. [Product Hunt] | Delete template sections that do not apply to the tier. Never pad to fill the template. |
| 19 | Edit it down every time you touch it. Curate, don't pile up. | Every revision removes stale text, moves superseded content to the changelog row, and keeps one current answer per question. |
| 20 | Changed beyond recognition? Start a new one. | When the problem or segment changes, write a new PRD. The old Doc gets a "Superseded by <link>" banner and keeps its title. |

## Launch, then check

| # | Rule | How it lands here |
| :--- | :--- | :--- |
| 21 | Launch is cross-functional: support, legal, marketing, FAQs. [Figma] | Launch checklist section: support brief, legal review, marketing note, FAQ, each with an owner and a date. |
| 22 | Get it red-teamed before stakeholders see it. Human or Claude. | The Crucible (State 3) plus `/hyperplan` on tier L, before the Doc goes to anyone outside the owner. |
| 23 | Write the Impact section. Did it move the metric? | 30 days after launch, fill the Impact section: metric before, metric after, verdict, next step. A commitment with a due date is created at launch. |

## Tiers (rule 10)

| Tier | When | What the PRD contains |
| :--- | :--- | :--- |
| **S** | A fix or small change, about 2 weeks of work or less, no new integration | One page: Problem page, metric, non-goals, acceptance criteria, owner. No Crucible score, no 8-story minimum. |
| **M** | A feature inside one product, one team | Full template, Crucible at 9.0 or more. Press release optional. |
| **L** | A launch across teams or tenants, new product, client-visible | Press release first, full template, Crucible, then `/hyperplan` red team, launch checklist mandatory. |

If the tier is not obvious, say the tier you picked and why in one line, and continue.
