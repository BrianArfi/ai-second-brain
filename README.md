# AI Second Brain

**Stop rebuilding your meeting notes, morning plan and weekly report by hand. An AI partner drafts them in your real tools, and you approve.**

For PMs, consultants and team leads whose week runs on meetings, Slack and Google Docs. You need a terminal once, to install it.

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Version 0.8.1](https://img.shields.io/badge/version-0.8.1-green.svg)](CHANGELOG.md)
[![Made for Claude Code](https://img.shields.io/badge/made%20for-Claude%20Code-orange.svg)](#requirements)

![Your morning, already sorted. On the left, the prompt "Write up this morning's meeting." and the line "Nothing is sent until you approve it." On the right, the local dashboard's Today view with sample data: 2 overdue, 2 due today, 4 commitments due, and a morning briefing of the three things that matter today. In front of it, the meeting note it filed: one decision and three action items, one flagged "Owner unclear".](docs/hero.png)

## Why

- Every Friday you type the same prompt for the weekly report, then paste in your meeting notes one by one.
- You correct the AI on Monday. On Thursday it makes the same mistake, because a new chat starts from zero.
- Building a better setup yourself starts from an empty folder: which commands, which rules, which tools.

## What it does

- **Plans your morning** from your calendar, email, Slack and yesterday's meetings, and checks the plan again in the evening.
- **Writes meeting minutes** with decisions and action items that have an owner and a date. An unclear owner is flagged, not guessed.
- **Writes the weekly report** into the same Google Doc every week, leading with what shipped, not with whatever happened on Friday.
- **Drafts every reply you owe.** Nothing is sent until you approve it.
- **Remembers corrections.** Correct it once, run `/learn`, and the next session already knows.

![The local dashboard in use, with sample data. The Today tab shows the morning briefing: the three things that matter today, the meetings, what can wait, and an escalation row with a drafted chase for a late reply. Then the Work tab shows the tracker: open tickets by priority and project, with what is overdue and due today.](docs/demo.gif)

*Recorded on a fresh clone with sample data. Nothing here is connected to a real account.*

## Quick start

```bash
git clone https://github.com/BrianArfi/ai-second-brain.git
cd ai-second-brain && bash install.sh
claude
```

Then type `/setup`. It interviews you about your work and writes your `CLAUDE.md`. Needs Claude Code (a paid Claude plan). The first run needs no other API keys and no connected tools.

## Example

You type, in plain words:

> Write up this morning's meeting.

It picks the newest transcript or notes in `inbox/`, files the minutes as `notes/meetings/YYYY-MM-DD-<short-name>.md`, and shows you a summary, the decisions, the action items and the open questions. A sample run:

![You type "Write up this morning's meeting." It reads the newest transcript in inbox/, files the minutes in notes/meetings/, and shows a summary, one decision and two action items: one with an owner and a due date, one flagged because the owner is unclear. It ends: Draft ready. Nothing was sent. Approve it, or tell me what to change.](docs/images/meeting-minutes.png)

---

## The local dashboard

Everything it tracks also shows up in a local dashboard. See [Dashboard](docs/DASHBOARD.md).

![The local dashboard's Today tab with sample data. A row of tabs from Today to What's new. Counters show 2 overdue tickets, 2 due today, no SLA breaches, no decisions due and 4 commitments due. Below them, a morning briefing lists the three things that matter today, today's meetings and what can wait, and a Top tickets list marks each ticket as overdue or due today](docs/images/dashboard-today.png)

## Documentation

- [Getting started](docs/GETTING_STARTED.md): the full install, a first task with no connectors, and the local dashboard.
- [How it works](docs/HOW_IT_WORKS.md): the daily loop, who it is for, and how it learns you.
- [Commands](docs/COMMANDS.md): every command, what you get, and when to use it.
- [Setup and authentication](docs/SETUP.md): Google, Slack, calendars and Jira, and how to choose only the skills you need.
- [Customizing `CLAUDE.md`](docs/CUSTOMIZING.md): how to write a strong profile.
- [Meeting recorder](docs/MEETING_RECORDER.md): records and transcribes on your own machine (macOS, Windows, Linux) and drafts the minutes. This is the default source of meeting notes; a cloud recorder is optional.
- [Dashboard](docs/DASHBOARD.md): the local dashboard at `http://localhost:3737`, its tabs, the Hours count, the cost panels, the cron jobs that feed it, and its network exposure.
- [Architecture](docs/ARCHITECTURE.md): the three layers, the capability catalog, the multi-agent cost model with model prices, and the folder layout.
- [Open Knowledge Format](docs/okf_adaptation.md): why the memory system follows Google Cloud's Open Knowledge Format, and the verification principle it applies to `mom_reconcile.py`.
- [Updating your fork](docs/UPDATING.md): pull the latest template updates (or type `/update-harness`).
- [Panduan instalasi](docs/INSTALL_ID.md): the step-by-step installation guide in Indonesian (workshop companion).

## Requirements

- **Claude Code.** The core instructions in `CLAUDE.md` also work with other agentic harnesses.
- **git**, to clone the template and pull updates.
- **Node.js 18 or later**, for the Claude Code CLI.
- **Python 3**, for the connectors, the dashboard and the meeting recorder. The first conversational path works without it.
- **macOS, Linux, WSL or Windows.** The repo detects which one it runs on at session start.
- Accounts for the tools you choose to connect (Google, Slack, Jira and others). None is required to start.

## FAQ

**Do I need to code?**
You need to be a little comfortable with a terminal to install it. After that you talk to it in plain language. `/setup` writes your `CLAUDE.md` through an interview.

**Do I have to connect every tool first?**
No. The first path takes about 15 minutes, with no API key. Connecting Google, Slack and your calendar takes roughly 2 to 4 hours, mostly Google sign-in. Connect only the ones you use.

**Will it send something on its own?**
It is set up not to. Slack sends refuse to run without an `--approved` flag, and a hook asks you before a Slack send goes through. Jira writes need the same flag. For email and WhatsApp, the guard is the rule in `CLAUDE.md`: show the draft, then wait for your yes. The WhatsApp bridge can also hold every send until you approve it outside the chat. Documents written to Drive stay inside your company domain once you set `WORK_DOMAIN` (see [`docs/SETUP.md`](docs/SETUP.md)), unless you publish them deliberately.

**Is my data safe?**
Your notes and credentials stay on your computer. What the AI reads goes to the AI service you use, the same as a normal chat. Credential files are never committed by the sync or the template tooling.

**Do I have to use Claude?**
It is built for Claude Code. The core instructions also work with other agentic tools. The optional model bridge can move bulk reading to other models, and everything falls back to Claude when it is not configured.

## Contributing

Patches are welcome. Start with [`CONTRIBUTING.md`](CONTRIBUTING.md): it explains the three layers, the shape of a skill, and the one thing about the release pipeline you have to know before you spend an evening on a patch.

Before you open a pull request:

```bash
python3 tools/repo_check.py
```

That checks the tree for anything personal (client names, home paths, emails, ticket keys, credentials), makes sure every script parses, and reports skills that are missing frontmatter.

Found a security problem? Do not open an issue. Follow [`SECURITY.md`](SECURITY.md).

## Versioning

Releases are tagged `vX.Y.Z`. What counts as breaking, and what a release means for a fork, is in [`docs/VERSIONING.md`](docs/VERSIONING.md).

## Changelog

The full history is in [CHANGELOG.md](CHANGELOG.md), and the dashboard shows it in the **What's new** tab.

## License

[Apache-2.0](LICENSE). Use it, fork it, sell what you build with it.

The name "AI Second Brain", the AI Circle name, and the artwork are not part of that grant. Give your fork its own name. See [`NOTICE`](NOTICE).

More AI skills: https://you.com/skills
