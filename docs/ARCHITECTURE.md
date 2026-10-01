# Architecture Reference

This document covers the system's internals: how the layers fit together, what each skill does, and how to extend the system.

---

## What it is: three layers

Most AI tools are a blank chat box. This repo gives that box a **job description**, **standard operating procedures**, and **hands that reach your real tools**.

```
   YOU SAY:  "draft the PRD for the new checkout flow"
                              |
   +----------------------------------------------------------+
   |  CLAUDE.md       THE BRAIN     who you are, your rules,   |
   |                                your languages, your memory|
   +----------------------------------------------------------+
   |  .claude/        THE REFLEXES  saved commands, subagents, |
   |                                guardrail hooks            |
   +----------------------------------------------------------+
   |  .agent/skills/  THE HANDS     Drive, Docs, Slack,        |
   |                                Calendar, meetings, Jira   |
   +----------------------------------------------------------+
                              |
   YOU GET:  a real Google Doc, in your format, ready to share
```

1. **`CLAUDE.md` is the brain.** It states who you are, which projects you run, which language each document should be in, and the rules it must follow. It grows as you teach it. The more specific it is, the more autonomously the AI can act.
2. **`.claude/` is the reflexes.** Commands are saved workflows: draft a PRD, write meeting notes, produce a weekly report. Subagents split big jobs across cheaper helpers. Hooks enforce your rules automatically, for example asking before anything is sent to Slack.
3. **`.agent/skills/` are the hands.** Each one is a small script that reads or writes a real service: create a Google Doc from markdown, post a Slack message as you, pull a meeting transcript, fetch a sprint board, update a tracking sheet.

**Runs on your machine, across machines.** The repo detects whether it is on macOS, WSL, or Windows at the start of each session and adapts how it runs your tools. Your credentials and notes stay local. Nothing is uploaded to a third party beyond the API calls the AI makes on your behalf.

**Guardrails that hold.** Slack sends refuse to run without an `--approved` flag, and a hook asks you before a Slack send goes through. Jira writes need the same flag. For email and WhatsApp, the guard is the rule in `CLAUDE.md`: show the draft, then wait for your yes. The WhatsApp bridge can also hold every send until you approve it outside the chat. Documents written to Drive stay inside your company domain once you set `WORK_DOMAIN` (see [`SETUP.md`](SETUP.md)), unless you publish them deliberately.

---

## Capability catalog

The repo ships 33 commands and about 65 skills. A representative slice of what you can ask, in plain language:

| Area | What you can ask it to do |
| :--- | :--- |
| **Communication** | Sweep Slack across many channels and draft a reply in your voice; send email as you; draft and send WhatsApp messages; reply inside a Google Doc comment thread. Slack sends need your approval flag; email and WhatsApp drafts are shown to you first. |
| **Documents** | Turn markdown into a real, formatted Google Doc; make surgical in-place edits (add links, insert table rows, embed diagrams) without clobbering your hand edits; export a branded PDF; draft and quality-gate a PRD; build an HTML explainer, prototype or deck. |
| **Meetings** | Record and transcribe a meeting locally on your own machine; turn any transcript into clean minutes with decisions and action items filed to your tracker; prepare a one-page brief before a meeting. |
| **Reporting and ops** | A morning briefing and evening recap; a weekly report that weighs what mattered; a PRD pipeline; ledgers for commitments, decisions and waiting-on items; a live visual dashboard of every project. |
| **Data** | Query Jira sprints and flag anyone overloaded; pull funnels and retention from Mixpanel; run SQL against Metabase; sweep your calendar into a clean view. |
| **Design and media** | Generate and edit images from a prompt; build diagrams from a description; assemble slide decks. |
| **Learning** | Remember a correction permanently via `/learn`; keep your dashboard, to-do list, and trackers in sync automatically. |
| **Under the hood** | Fan a big job out to parallel workers; route bulk work to cheaper models with a guaranteed fallback; run a quality-gate reviewer before anything reaches you. |

---

## Multi-agent setup: faster and cheaper

Here is the part that makes it economical to run every day.

A big job such as a weekly report, a deep-research brief, or a large PRD is rarely one kind of work. It is mostly **bulk reading**, a little **focused analysis**, and a bit of **careful synthesis**. Run all of it on one expensive model and you overpay for the reading. Run all of it on one cheap model and the thinking falls apart.

So this repo splits the job. One strategist directs; a fleet of cheap, fast workers does the reading in parallel; only the distilled facts come back for synthesis.

```
                    +------------------------------------+
   "write this      |      MAIN SESSION: Opus 5          |  plans + synthesizes
    week's   ------>|      the strategist                |  (smart, pricey)
    report"         +-----------------+------------------+
                                      | spawns a fleet, all at once
          +----------+---------------+---------------+----------+
          v          v               v               v          v
      +-------+  +-------+       +-------+       +-------+  +-------+
      |harvest|  |harvest|       |harvest|  ...  |harvest|  |review |   Haiku 4.5
      | mtg 1 |  | mtg 2 |       | mtg 3 |       | Slack |  | pass  |   (cheap, fast)
      +---+---+  +---+---+       +---+---+       +---+---+  +---+---+
          |   each reads ~12K of raw source, returns ~1.5K of facts   |
          +----------+---------------+---------------+----------+
                                     v
                    +------------------------------------+
                    |  Opus reads 15K of clean facts     |
                    |  -> writes the finished report     |
                    +------------------------------------+
```

Two things save money and time at once. The **bulk reading**, usually the largest share of tokens, runs on a model that costs a fifth as much. And the **parallel workers** finish in the time a single agent would spend reading one file. The flagship spends its pricey tokens only where judgment is actually required.

A third saving comes from **prompt caching**: the large, stable parts of a prompt such as your `CLAUDE.md` or a long document are cached and reread at about a tenth of the normal input price across a session.

Two subagents ship as working examples: a **harvester** that reads many sources and returns structured facts without trying to write the final document, and a **reviewer** that checks a draft against your rules before it reaches you.

### Which model for which job

Use the cheapest model that can do the subtask well. Match the tier to the work, not the other way around.

| Tier | Model | Model ID | Context | Price /1M (in / out) | Use it for |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Bulk** | Claude Haiku 4.5 | `claude-haiku-4-5` | 200K | $1 / $5 | Mechanical work with no judgment: bulk reading, formatting, extraction, classification. The default for harvester and reviewer subagents. |
| **Scoped** | Claude Sonnet 5 | `claude-sonnet-5` | 1M | $3 / $15 | Scoped research, code exploration, in-scope synthesis. The best balance of speed and intelligence for focused subtasks. |
| **Flagship** | Claude Opus 5 | `claude-opus-5` | 1M | $5 / $25 | The main session for synthesis-heavy work: planning, weighing tradeoffs, writing the final deliverable. |
| **Frontier** | Claude Fable 5 | `claude-fable-5` | 1M | $10 / $50 | The most demanding long-horizon, autonomous work, where one run may plan, build, and verify across many steps. When correctness matters more than cost. |

A practical default: run the main session on **Opus 5**, delegate bulk work to **Haiku 4.5** subagents, and reach for **Sonnet 5** when a subtask needs real research rather than mechanical effort. Move the main session up to **Fable 5** for the hardest end-to-end jobs.

> Model IDs are exact strings. Use them as written, with no date suffix. Prices are list API prices and may change; check the provider's pricing page for current figures.

### What it saves on a real job: the weekly report

The weekly report is the clearest case, because it is mostly bulk reading wrapped around a little synthesis, exactly the shape the diagram above is built for. Take a representative week:

- **8 meeting transcripts** at ~12K tokens each, plus written notes, dashboard sections, the to-do list, and Slack history: about **150K tokens of raw source**.
- Of that, only about **15K tokens of distilled facts** actually matter for writing the report.

| | **A. One flagship agent does it all** | **B. This repo: Haiku harvests, Opus synthesizes** |
| :--- | :--- | :--- |
| Who reads the 150K of sources | Opus, in one growing context | 9 Haiku workers, in parallel |
| What the flagship then carries | all **150K** of raw transcript, re-read every turn | only the **15K** of facts |
| Reading cost (150K input) | 150K x $5/1M = **$0.75** | 150K x $1/1M = **$0.15** |
| Synthesis (~10 drafting turns) | 150K x 10 = 1.5M token-reads | 15K x 10 = 150K token-reads |
| Wall-clock to read sources | 8 transcripts, one after another | 8 transcripts at once (~1/8 the time) |

Two levers pull at the same time:

1. **Tier swap.** Every token of bulk reading moves from Opus to Haiku, a flat, exact **5x cheaper** (input $5 to $1, output $25 to $5). Same work, on the model the work actually needs.
2. **Context compression.** In version A the 150K of raw transcript sits in the flagship's window and is re-processed on *every* drafting turn. In version B the flagship only ever holds 15K of facts, so the drafting phase re-reads **10x less**. Usually the bigger saving, and it makes the report *better*, because the model reasons over clean facts instead of hunting through raw transcripts.

```
   COST OF ONE WEEKLY REPORT   (illustrative, from list prices)

   One flagship agent does everything
   ####################  ~$2.00

   This repo: Haiku harvests, Opus synthesizes
   ####  under $0.50            <- ~4-5x cheaper, and finishes faster
```

> These figures are an **illustrative model from list prices, not a published benchmark.** Your exact numbers depend on how many meetings you had, transcript length, and caching. The two levers and their direction hold regardless: bulk work on a 5x-cheaper tier, and a flagship context that never bloats with raw source. The same pattern applies to deep-research briefs, large PRDs, and any gather-then-synthesize job.

### Optional: offload bulk work to non-Claude models

The repo ships an optional **model bridge** (`.agent/skills/agy-bridge/`) that can route harvest, critique, and research subtasks to cheaper non-Claude backends (GLM, Kimi, Gemini via CLI) when you happen to have those subscriptions.

**You do not need any of them.** With no bridge backends configured, every caller detects it instantly and falls back to the Claude tiers above; the whole harness runs Claude-only at full capability. The bridge is a cost saver for people who already pay for a second model, never a requirement. Run `python3 .agent/skills/agy-bridge/run.py --doctor` to see your mode.

### It keeps getting cheaper: the token-efficiency loop

Cost discipline is not a one-time setup, so the harness audits itself:

- A weekly cron runs `.agent/scripts/token_efficiency.py report`: tokens, cost, and offload share per task type, week over week, from real usage logs.
- Every change made to save tokens is recorded with `token_efficiency.py log-change`, so the next report shows each optimization next to its **observed** effect, not its promised one.
- The dashboard renders the trend, the current top-3 token hotspots, and the what-changed log; weekly planning picks at most one hotspot to optimize next.

The protocol lives in [`.agent/protocols/token_efficiency.md`](../.agent/protocols/token_efficiency.md).

---

## System Layers

```
┌─────────────────────────────────────────────────┐
│  Layer 3: AI Interface                          │
│  CLAUDE.md — operating manual                   │
│  Dashboard.md — real-time project status        │
└─────────────────────────────────────────────────┘
                       ↕ reads/writes
┌─────────────────────────────────────────────────┐
│  Layer 2: Automation Engine                     │
│  .agent/skills/   — modular integrations        │
│  .agent/scripts/  — orchestration               │
│  .agent/workflows/ — workflow definitions       │
└─────────────────────────────────────────────────┘
                       ↕ reads/writes
┌─────────────────────────────────────────────────┐
│  Layer 1: Knowledge Base                        │
│  Clients/ — documents per client/project        │
│  journal/ — tasks, trackers, notes              │
│  _output/ — generated reports and logs          │
└─────────────────────────────────────────────────┘
```

### Layer 1 — Knowledge Base

Where all your content lives. Structure by client and project:

```
Clients/
└── YourClient/
    ├── ProductArea/       PRDs, specs, backlogs
    ├── meetings/          Meeting notes (MOM files)
    ├── strategy/          Strategic documents
    └── Research/          Analysis, user research

journal/
├── todo.md                Master task list (P0/P1 priorities)
└── master_followup_tracker.md  GENERATED view over the PM ledgers (commitments/waiting_on/decisions);
                                 never hand-edited, rendered by project-tracking-update/scripts/render_followup_tracker.py
```

### Layer 2 — Automation Engine

The `.agent/` folder contains everything that automates work:

```
.agent/
├── skills/        30+ modular skills (one folder per integration)
├── scripts/       Orchestration scripts
│   └── daily_update_runner.py   ← primary daily automation
├── workflows/     Markdown workflow definitions
└── protocols/     Communication and delivery protocols
```

### Layer 3 — AI Interface

Two files the AI reads at the start of every session:

- **`CLAUDE.md`** — your operating manual (see [CUSTOMIZING.md](CUSTOMIZING.md))
- **`Dashboard.md`** — auto-updated project status. Acts as shared working memory between you and the AI across sessions.

---

## Skills Reference

### Google Workspace

| Skill | Invoke | What It Does |
|---|---|---|
| `work-drive-connector` | `python3 .agent/skills/work-drive-connector/gdrive_manager.py [action]` | Upload, update, search, read, rename files in your work Google Drive |
| `personal-drive-connector` | `python3 .agent/skills/personal-drive-connector/gdrive_manager.py [action]` | Same, for your personal Google Drive |
| `secondary-drive-connector` | `python3 .agent/skills/secondary-drive-connector/gdrive_manager.py [action]` | For a second work account or secondary Google Workspace |
| `gdocs-create` | `python3 .agent/skills/gdocs-create/gdocs_create.py create-doc --title "..." --file doc.md --account work\|personal` | Convert markdown → real editable Google Doc |
| `gmail-connector` | `python3 .agent/skills/gmail-connector/gmail_manager.py [action]` | List, read, archive Gmail messages |
| `google-calendar-connector` | `python3 .agent/skills/google-calendar-connector/gcal_manager.py sweep --profile work` | List and create calendar events |

**Drive actions**: `upload`, `update`, `search`, `read`, `delete`, `share`, `comments`, `rename`

**Rule**: Always use `update --id FILE_ID` for existing docs. Never re-upload. Never change document titles.

---

### Communication

| Skill | Invoke | What It Does |
|---|---|---|
| `slack-connector` | `python3 .agent/skills/slack-connector/scripts/slack_client.py --action [list_channels\|history\|post]` | Read channel history, list channels, post messages |
| `slack-channel-manager` | `python3 .agent/skills/slack-channel-manager/scripts/manage_channels.py --action list --client [name]` | Whitelist and track specific channels per client |
| `whatsapp-connector` | Browser automation via CDP | Read and send WhatsApp messages via persistent Chrome session |

---

### Product Documentation

| Skill | What It Does |
|---|---|
| `prd-pipeline` | 4-stage PRD generation: (1) harvest context from Drive/Slack/Figma → (2) draft PRD → (3) quality score (min 9/10) → (4) generate engineering tickets |
| `user-story-writer` | Turn feature descriptions into INVEST-validated user stories with Gherkin acceptance criteria |
| `marketplace-product-manager` | Triple-pass PRD reviewer: structure check → self-challenge (edge cases, unhappy paths) → expansion |
| `master-product-list` | Register new PRDs into a master tracking spreadsheet |
| `dashboard-updater` | Sync Drive + Calendar + Slack → update Dashboard.md |

**PRD Pipeline stages:**

```
Stage 1: Context Harvest
  → Reads: local files, Drive, Slack, Figma (if connected)
  → Output: context brief

Stage 2: Draft
  → Input: context brief + your brief
  → Output: full PRD draft in markdown

Stage 3: Crucible (quality check)
  → Scores against rubric (min 9/10 to pass)
  → Self-challenges: unhappy paths, edge cases, admin needs
  → Iterates until passing

Stage 4: Tickets
  → Converts approved PRD → engineering-ready tickets
```

---

### Reporting & Briefings

| Skill | Invoke | What It Does |
|---|---|---|
| `daily_update_runner.py` | `python3 .agent/scripts/daily_update_runner.py` | Full daily scan: calendar + Drive + Slack → `daily_update_output.md` |
| `weekly-report-generator` | Via Claude Code prompt | Synthesize week across all clients → structured report → Google Doc |
| `dashboard-updater` | `python3 .agent/skills/dashboard-updater/scripts/dashboard_sync.py` | Pull Drive/Calendar/Slack into Dashboard.md |

**Daily runner** runs for ~2–3 minutes and produces a full briefing covering:
- Today's calendar
- New/updated Drive documents
- Slack channel highlights
- Open action items from todo.md

---

### Analytics & Research

| Skill | Invoke | What It Does |
|---|---|---|
| `fathom-connector` | `python3 .agent/skills/fathom-connector/scripts/fathom_client.py --action [list\|transcript --id ID]` | List meetings, pull transcripts from Fathom |
| `figma-connector` | `python3 .agent/skills/figma-connector/scripts/figma_client.py get-comments --file-key KEY` | Extract design data and comments from Figma files |
| `mixpanel-connector` | `python3 .agent/skills/mixpanel-connector/scripts/mixpanel_client.py [action]` | Query events, funnels, retention from Mixpanel |
| `seo-audit` | `python3 .agent/skills/seo-audit/scripts/comprehensive_audit.py --url https://yoursite.com --limit 50` | Playwright-based technical SEO crawl |
| `clickup-connector` | `python3 .agent/skills/clickup-connector/scripts/clickup_client.py --action [list_tasks\|create_task]` | Read and create ClickUp tasks |

---

### Quality & Enforcement

These are rule-based skills — they define constraints the AI enforces automatically.

| Skill | Rule |
|---|---|
| `no-emdash` | Never use em-dashes (—) in any document — use hyphens (-) instead |
| `no-title-change` | Never rename a Google Doc during an update operation |
| `document-alignment` | When updating any strategic doc, check for consistency with related docs (financial model, GTM, roadmap) |
| `execution-guard` | Wraps all scripts with 180s timeout and graceful error handling |
| `temp-file-organizer` | Auto-sort stray generated files into `_temp/` subcategories |

---

### Utilities

| Skill | What It Does |
|---|---|
| `browser-service` | Manages a persistent Chrome CDP session on port 9222 — required before any browser automation |
| `mcp-switcher` | Toggle MCP servers on/off (useful when hitting the 100-tool limit) |
| `gdocs-writer` | Legacy: markdown → .docx → upload. Use `gdocs-create` instead unless .docx is specifically needed |

---

## Daily Operations Flow

```
Morning
  └── daily_update_runner.py (2–3 min)
        ├── Scans Google Calendar → today's meetings
        ├── Scans Drive → new/updated files
        ├── Pulls Slack channel updates
        └── Writes → daily_update_output.md

        You read this, then open Claude Code:
        "What should I focus on today?"
        AI reads Dashboard.md + daily_update_output.md → gives you a prioritized plan

During the Day
  ├── "Write a PRD for X" → prd-pipeline
  ├── "Summarize the meeting I just had" → fathom-connector → meeting notes → Drive
  ├── "What's blocking Y?" → reads master_followup_tracker.md (generated view over the PM ledgers)
  └── "Draft a Slack update for the team" → slack-connector (draft + approval)

End of Week
  └── weekly-report-generator
        ├── Reads all week's calendar + transcripts + Drive
        └── Produces report → Google Doc
```

---

## Repository layout

```
.agent/skills/      Connectors and skills (Drive, Docs, Slack, Calendar, meetings, Jira, and more)
.agent/scripts/     Shared helpers, including the machine detection used at session start
.agent/workflows/   Reusable multi-step workflow definitions
.claude/commands/   Saved workflows you can invoke by name or in plain language
.claude/agents/     Subagent definitions (harvester, reviewer)
.claude/hooks/      Automatic guardrails (send confirmation, formatting checks)
meeting-recorder/   Record + transcribe meetings locally (macOS, Windows, Linux)
meetbot/            Rust bot that auto-joins Meet/Teams calls and transcribes them
dashboard/          Local visual dashboard web app (http://localhost:3737)
docs/               Setup, customizing, and architecture guides
CLAUDE.md.template  Rename to CLAUDE.md and make it yours
```

---

## File Organization

```
product-second-brain/
├── .agent/
│   ├── AGENT_STATE.md           token status registry
│   ├── skills/                  one folder per integration
│   ├── scripts/
│   │   └── daily_update_runner.py
│   ├── workflows/               markdown workflow definitions
│   └── protocols/               delivery and communication protocols
├── Clients/
│   └── [ClientName]/
│       ├── [ProductArea]/
│       ├── meetings/
│       ├── strategy/
│       └── Research/
├── journal/
│   ├── todo.md
│   └── master_followup_tracker.md
├── _output/                     generated reports (gitignored)
├── _temp/                       scratch files (gitignored)
├── CLAUDE.md                    your operating manual
├── Dashboard.md                 live project status
├── requirements.txt
└── .env.example
```

---

## Adding a New Skill

Skills follow a consistent structure:

```
.agent/skills/your-skill-name/
├── README.md          what the skill does + invoke examples
├── token.env          API credentials (gitignored)
└── scripts/
    └── your_client.py  the actual script
```

Minimum viable skill script:

```python
#!/usr/bin/env python3
"""
your-skill-name: brief description
"""
import os
import argparse

TOKEN_ENV_PATH = os.path.join(os.path.dirname(__file__), "..", "token.env")

def load_token():
    if os.path.exists(TOKEN_ENV_PATH):
        with open(TOKEN_ENV_PATH) as f:
            for line in f:
                if line.startswith("YOUR_API_KEY="):
                    return line.split("=", 1)[1].strip()
    return os.environ.get("YOUR_API_KEY")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", required=True, choices=["list", "get"])
    parser.add_argument("--token", help="API token (or set YOUR_API_KEY env var)")
    args = parser.parse_args()

    token = args.token or load_token()
    if not token:
        print("Error: no token found. Set YOUR_API_KEY in token.env or pass --token")
        exit(1)

    if args.action == "list":
        # your logic here
        pass

if __name__ == "__main__":
    main()
```

Then document it in `AGENT_STATE.md` and add it to your `CLAUDE.md` tool routing.

---

## Token Management

Track integration health in `.agent/AGENT_STATE.md`.

| Token type | Expiry | Refresh |
|---|---|---|
| Google OAuth | Never (has refresh token) | Auto-refresh on each call |
| Slack Bot Token | Never (unless revoked) | Manual re-install |
| Fathom API Key | Never | Manual replacement |
| Figma PAT | Never (unless revoked) | Manual replacement |

When a Google token fails:
```bash
# Delete the old token and re-run to trigger fresh OAuth
rm .agent/skills/work-drive-connector/token.json
python3 .agent/skills/work-drive-connector/gdrive_manager.py search --query "test"
```
