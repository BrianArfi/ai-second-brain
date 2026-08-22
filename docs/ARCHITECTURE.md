# Architecture Reference

This document covers the system's internals: how the layers fit together, what each skill does, and how to extend the system.

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
