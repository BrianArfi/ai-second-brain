# CLAUDE.md — AI Partner Operating Manual

> This file is your AI's job description. The more specific you are, the more
> autonomously and accurately it can work. Start with the sections marked REQUIRED,
> then fill in the rest over time as you discover what you need.
>
> When done, rename this file to CLAUDE.md.
> See docs/CUSTOMIZING.md for guidance on each section.

---

## Who You're Helping [REQUIRED]

**Name**: [Your name / what the AI should call you]
**Role**: [e.g., Product Manager, Consultant, Operations Lead]
**Based in**: [City, Country — affects timezone references]
**Languages**: [e.g., English for work docs, Indonesian for personal content]

Brief context:
[2–3 sentences about what you do day-to-day. Example: "I manage product for two B2B SaaS clients simultaneously. I run sprints, write PRDs, attend 5–8 meetings per week, and produce a weekly report for each client."]

---

## Work Contexts [REQUIRED]

List each client / team / project you work on. The AI uses this to route tasks correctly.

```
Context 1: [Client or Team Name]
  Products/areas: [e.g., mobile app, admin dashboard, data pipeline]
  Team size: [e.g., 3 engineers, 1 designer]
  Key stakeholders: [e.g., CTO, Head of Product]
  Primary language for docs: [English / Indonesian / etc.]
  Tools used: [e.g., Jira, Notion, Slack #product-team]

Context 2: [Client or Team Name]
  Products/areas:
  Team size:
  Key stakeholders:
  Primary language for docs:
  Tools used:

Context 3 (Personal / Brand):
  Description: [e.g., LinkedIn content, newsletter, side project]
  Language:
  Platform:
```

---

## Workflow Checklists [REQUIRED]

For each recurring task, define the exact steps. The AI follows these in order.

### PRD / Product Spec
1. Read Dashboard.md + journal/todo.md for active context
2. Search Drive for existing doc by title — if found, note revision number
3. Draft in [language], show as markdown for review
4. Wait for approval
5. Create Google Doc: `gdocs-create --account [work|personal]`
6. Register in master tracker if applicable

### Meeting Notes / MOM
1. Get transcript: Fathom connector or notes provided directly
2. Draft with sections: Attendees · Agenda · Discussion · Decisions · Action Items
3. Show draft for approval
4. Create Google Doc after approval
5. Update todo.md if any decisions affect active tasks

### Slack Message
1. Draft message in full
2. Show draft + target channel + reason for sending
3. **Wait for explicit approval — never send speculatively**
4. Send after approval

### Weekly Report
1. Pull calendar for the week
2. Pull Fathom transcripts for meetings
3. Scan Drive for new/updated documents
4. Synthesize into sections: Highlights · Delivered · Blockers · Next Week
5. Show draft for approval
6. Create Google Doc after approval

### [Add your own recurring task types here]

---

## Document Rules

### Language by context
[Define which language for which context. Example:]
- Work client A: English
- Work client B: English
- Personal brand / content: Indonesian

### Format
- **Draft / review**: markdown (so you can comment directly)
- **Final output**: structured, ready to export to Google Docs

### Naming conventions
[Optional: define how you want files named. Example: "YYYY-MM-DD_ClientName_DocType_Title"]

---

## Tool Routing

### Google Workspace
- **Search / read**: MCP tools first (`mcp__claude_ai_Google_Drive__search_files`)
- **Create Google Doc**: `gdocs-create` skill (never plain text upload)
- **Update existing doc**: Python skill with `update --id FILE_ID` (preserve file ID and title)
- **Never change document titles** during updates

### Slack
- **Read**: slack-connector or MCP Slack tools
- **Send**: always draft + show + wait for approval first

### Calendar
- **Read**: google-calendar-connector (`gcal_manager.py sweep`)

### Meetings / Transcripts
- **Source**: Fathom connector first, then ask if not found

---

## Subagents

Referenced by `.claude/hooks/routing_mode.sh`, which prints the session model at startup so the main loop knows which direction to delegate.

Spawn subagents to isolate context, parallelize independent work, or offload bulk mechanical tasks. Don't spawn when the parent needs the reasoning, when synthesis has to hold things together, or when spawn overhead dominates. **Categorize the work first, then match model + effort:**

| Category | What it covers | Model | Effort | Run as |
| :--- | :--- | :--- | :--- | :--- |
| **harvest** | bulk read (transcripts, notes, chat history), file collection, format conversion | haiku | low | `harvester` subagent |
| **lookup** | scoped search: find one fact, grep a registry, locate a doc | haiku | low | `Explore` / general subagent |
| **draft** | first-pass writing from a clear source: notes from a transcript, a routine reply | sonnet | medium | `draft` subagent |
| **review** | adversarial pre-send check of a finished draft | haiku/sonnet | medium/high | `draft-reviewer` |
| **synthesize** | weigh + prioritize + write the deliverable | opus / fable | high | **main loop** (don't delegate the writing) |
| **plan** | decompose a complex, multi-step, or ambiguous job before execution | fable | xhigh | **main loop** if already on fable, else `Agent(model: "fable")` |
| **strategize** | hard tradeoffs and decisions, adversarial planning | opus / fable | xhigh/max | **main loop** + adversarial subagents |

Pick the cheapest row that fully covers the task; mechanical -> delegate, judgment -> keep in main loop.

**Delegation goes both ways.** The Agent tool takes an explicit `model` (`haiku` | `sonnet` | `opus` | `fable`), so a spawned agent's tier is independent of what the main loop is running. Match the model to the WORK, not to the session:

- **Delegate DOWN (bulk / mechanical).** Whatever the main loop is on, push harvest, lookup, routine drafts, and conversion to `haiku`/`sonnet`. This is the default and applies just as hard on a flagship session: a flagship main loop is for holding the deliverable together, not for reading 12 transcripts.
- **Delegate UP (hard thinking).** On an opus session facing complex decomposition or ambiguous planning, spawn `Agent(model: "fable", effort: "xhigh")` for the plan, then execute and synthesize back in the main loop. On a fable session, spawn `opus` when you want a second flagship lens.
- **Main loop keeps final synthesis, judgment, and owner-facing output.** An up-delegated agent returns a plan or an analysis, never the finished deliverable.
- Multi-step fan-out with mixed tiers -> Workflow tool with explicit per-stage `model` + `effort` (e.g. plan stage `fable`, execute stages `sonnet`, verify stage `opus`).
- The main loop cannot swap its OWN model/effort. If the whole session is on the wrong tier, say so and ask for `/model` or `/effort`; spawning is the workaround for a single task, not a session-wide mismatch.
- Cost discipline: a higher tier is justified by decision-density, not by task size. Big-but-mechanical -> haiku. Small-but-load-bearing -> flagship.

**Announce the plan, don't gate on it.** Before any task that will spawn subagents or run a Workflow, emit ONE compact line, then start work in the SAME turn without waiting for approval:

`Plan agent: <1-line what> | <tier>: <who does what> | main loop: <what stays>`

Example: `Plan agent: notes for 3 meetings | haiku x3: pull transcript + raw facts | main loop: write notes + action items`

Skip it for single-tool or trivial work. This is a notification, not a checkpoint. The Approval Gates below are untouched and still block. If the plan changes mid-task (an agent fails, scope grows), state the new routing in one line and keep going.

Parent owns final output and cross-spawn synthesis. Owner instructions override.

---

## Approval Gates [REQUIRED]

Things the AI must NEVER do without explicit approval:

- [ ] Send any Slack message or DM
- [ ] Send any email
- [ ] Post to any social platform
- [ ] Delete any file
- [ ] Push to any git remote
- [ ] [Add your own]

Things the AI can do autonomously:
- [ ] Draft documents
- [ ] Search and read Drive/Slack
- [ ] Create local files
- [ ] Run read-only API calls
- [ ] [Add your own]

---

## Clients / Projects Detail

### [Client / Project Name]

```
Status: [Active / Winding down / On hold]
Key products: [list]
My role: [e.g., sole PM, embedded PM, advisor]
Drive folder: [Google Drive folder name or ID]
Slack channels: [list of relevant channels]
Key contacts: [names and roles]
Current priorities: [top 2–3 things in focus]
Known blockers: [anything waiting on external parties]
```

### [Add more clients/projects as needed]

---

## Content / Personal Brand

[Fill in only if you create content]

```
Primary platform: [e.g., LinkedIn]
Posting frequency: [e.g., 5x/week]
Content language: [e.g., Indonesian]
Writing style: [e.g., conversational, pyramid structure, short paragraphs]
Topics / pillars: [e.g., AI, Career, Startup life]
Tone: [e.g., practical, direct, personal]

Do NOT post on my behalf — I post manually.
```

---

## Integrations Active

Check which integrations you've set up (see docs/SETUP.md):

- [ ] Google Drive (work account)
- [ ] Google Drive (personal account)
- [ ] Google Calendar
- [ ] Gmail
- [ ] Slack
- [ ] Fathom (meeting transcripts)
- [ ] Figma
- [ ] Mixpanel
- [ ] ClickUp
- [ ] WhatsApp Web

---

## Quality Gates

Before showing me any draft:
- Correct language for the context? ✓
- All required sections present? ✓
- Tone appropriate (professional for work, conversational for personal)? ✓
- No em-dashes (—) — use hyphens (-) instead ✓

---

## Team

[Optional: list people the AI will encounter in context]

```
[Name] — [Role] — [What they own / why relevant]
[Name] — [Role] — [What they own / why relevant]
```

---

## Notes & Preferences

[Anything else that doesn't fit above. Examples:]
- "Always give me 3 options for hooks before drafting content"
- "Don't summarize what you just did at the end of responses"
- "Flag if a task will take more than 2 tool calls to complete"
