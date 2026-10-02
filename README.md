# AI Second Brain

**Your PM admin, done before you open Slack.**

Your morning plan, meeting minutes, weekly report and every reply you owe, drafted from your own calendar, Slack, email and meetings. Nothing goes out until you approve it.

For PMs, team leads and founders whose week runs on meetings, Slack and Google Docs. Free and open source. You need a terminal once, to install it.

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Version 0.8.1](https://img.shields.io/badge/version-0.8.1-green.svg)](CHANGELOG.md)
[![Made for Claude Code](https://img.shields.io/badge/made%20for-Claude%20Code-orange.svg)](#requirements)

![Animated: "Your morning, already sorted." The prompt "Write up this morning's meeting." types itself. The local dashboard's Today view slides in with sample data: 2 overdue, 2 due today, 4 commitments due, and a morning briefing of the three things that matter today. Then the meeting note it filed drops in front: one decision and three action items, two with an owner and a day, the third flagged "Owner unclear". Last line: "Nothing is sent until you approve it."](docs/hero.gif)

## Sound familiar?

It's Friday, 16:40. Maya is a PM, and her weekly report is due at five.

- Eight meetings this week, and the notes sit in four places: a recorder, a Google Doc, Slack threads and her own head.
- She types the weekly report prompt from scratch, same as last Friday, then pastes the notes in one meeting at a time.
- On Monday she told the AI to lead with what shipped. Today it forgot, because every new chat starts from zero.
- On Tuesday someone said "I'll handle the bank fallback". Nobody wrote down who.
- Three people are waiting on a reply from her. She'd love help with those, but one wrong message to a VP and she's back to checking everything by hand.

None of this is hard. It's the same admin every week, rebuilt by hand. And building a better setup yourself starts from an empty folder: which commands, which rules, which tools.

## Who it is for

**Good fit if you:**

- Run a week of meetings, Slack threads and Google Docs, and owe people minutes, plans, reports and replies.
- Write the same kinds of documents every week: minutes, a weekly report, PRDs, follow-ups.
- Want the AI to work inside your real tools, with you approving each thing before it goes out.
- Are fine opening a terminal once to install it, then talking in plain language after that.

**Not for you if:**

- You want an assistant that sends messages on its own. This one is built to stop and wait for your yes.
- You want a hosted app with nothing to install. This is a folder on your machine that runs in Claude Code.
- You need one shared system for a whole team. It is one person's workspace, on one computer.
- You do not have Claude Code. It needs a paid Claude plan.
- Your week has few meetings and few recurring documents. There is not much here for it to take off your plate.

## Before and after: a PM's Friday

| Before | After |
| :--- | :--- |
| Open eight meetings' notes in four places and paste them into a chat | `/weekly-report` reads every meeting and note of the week itself, in parallel |
| Type the same report prompt from scratch | The procedure is saved in [`weekly-report.md`](.claude/commands/weekly-report.md). Ask in plain words |
| The draft leads with the latest meeting | It weights what shipped first, then blockers that got unblocked, then risks |
| You are the only check on the draft | A `report-auditor` subagent scores it against a rubric before you see it |
| Paste the result into a new Google Doc every week | After you approve, it updates the same Doc in place and adds a changelog row |
| Make the same correction every week | Correct it once and run `/learn`. It drafts a rule and saves it when you confirm |

![Illustration, with a sample PM named Maya. Before: six cards pile up on Friday at 16:40, "8 meetings, notes in 4 places", "type the weekly report prompt from scratch, again", "paste each meeting's notes in one by one", "the draft leads with this morning's call", "the same mistake you corrected on Monday is back", "nobody wrote down an owner". A lime wipe turns the label to After: Claude Code runs "Write this week's progress report.", harvests 8 meetings and notes, weights shipped work first, attaches the report-auditor scorecard, and ends "Draft ready. Approve it, or tell me what to change." Then "Always lead with what shipped. /learn" drafts a rule that is saved once you confirm.](docs/before-after.gif)

## How it works

1. **Tell it who you are, once.** Run `/setup`. It interviews you about your role, projects, team and rules, and writes `CLAUDE.md`. It reads that file before every task.
2. **Ask in plain words.** "Write up this morning's meeting." A plain request follows the same saved procedure as the slash command, because `CLAUDE.md` routes both to the same file in `.claude/commands/`.
3. **It reads your context and your tools, then drafts.** It pulls from what you connected: calendar, email, Slack, meeting notes, tickets, and your `notes/` and `inbox/` folders. Big jobs fan out to cheaper subagents that read in parallel, and a reviewer subagent checks the draft against your rules.
4. **You decide.** Approve, and it creates the Google Doc or sends the message. Correct it, run `/learn`, and the lesson is kept for every future session.

![Illustration of the daily loop, drawn step by step. 1, your tools: Calendar, Gmail, Slack, meeting notes, Jira and Docs, notes/ and inbox/. 2, your context and skills: CLAUDE.md (who you are, your projects, your team, your house rules, read before every task) and .claude/commands/ (/daily-update, /meeting-notes, /weekly-report, /follow-ups). 3, drafts: today's top 3, minutes with owners, the weekly report, replies you owe. 4, you decide: "You review. Nothing goes out before this." Approve sends the doc, message or ticket. "Correct it" goes to /learn, the correction becomes a rule, and an arrow loops back to CLAUDE.md: next session already knows.](docs/how-it-works.gif)

The full loop, with a diagram, is in [How it works](docs/HOW_IT_WORKS.md). Every command is in [Commands](docs/COMMANDS.md).

## What it does

- **Plans your morning** from your calendar, email, Slack and yesterday's meetings, and checks the plan again in the evening (`/daily-update`).
- **Writes meeting minutes** with decisions and action items that have an owner and a date. An unclear owner is flagged, not guessed (`/meeting-notes`).
- **Writes the weekly report** into the same Google Doc every week, leading with what shipped, not with whatever happened on Friday (`/weekly-report`).
- **Drafts every reply you owe.** It lists the open loops and drafts a message for each. Nothing is sent until you approve it (`/follow-ups`).
- **Remembers corrections.** Correct it once, run `/learn`, and the next session already knows.
- **Shows it all in a local dashboard** at `http://localhost:3737`. Pure Python standard library, nothing to pip install.

## See it run

**The inbox, on the real local dashboard.** Once Slack and Gmail are connected, everyone waiting on you lands in one queue, with replies drafted. Open a thread, read the draft, and nothing goes out until you press Approve.

![The real local dashboard on a fresh clone, with sample data. The Inbox tab lists who is waiting on you: under "Needs a reply (3)", Dina on Slack about the final checkout copy for the 3 pm design review, Sam by email about the partner API limit, both marked "draft ready", and Budi in #launch about the refund rules one-pager. Under FYI, Maya's note that the release notes draft is pinned. The cursor opens Dina's thread: her two messages, then the drafted reply, "Hi Dina, the payment screen strings are final. I will drop them in this thread by 1 pm...". The cursor rests on "Approve & send" without pressing it, and the caption reads "Nothing is sent until you press Approve".](docs/inbox-demo.gif)

**Your morning and your tracker.** The Today tab opens on the counters and the morning briefing, then the Work tab shows every open ticket by priority.

![The local dashboard in use, with sample data. The Today tab shows the morning briefing: the three things that matter today, the meetings, what can wait, and an escalation row with a drafted chase for a late reply. Then the Work tab shows the tracker: open tickets by priority and project, with what is overdue and due today.](docs/demo.gif)

*Both recorded with Playwright on a fresh clone with sample data. Nothing here is connected to a real account. The inbox recording is reproducible: `python docs/src/record_inbox_demo.py`.*

## Quick start

```bash
git clone https://github.com/BrianArfi/ai-second-brain.git
cd ai-second-brain && bash install.sh
claude
```

Then type `/setup`. It interviews you about your work and writes your `CLAUDE.md`. Needs Claude Code (a paid Claude plan). The first run needs no other API keys and no connected tools.

To see the dashboard straight away, no connector needed: `python3 dashboard/server.py`, then open <http://localhost:3737>.

## Example

You type, in plain words:

> Write up this morning's meeting.

It picks the newest transcript or notes in `inbox/`, files the minutes as `notes/meetings/YYYY-MM-DD-<short-name>.md`, and shows you a summary, the decisions, the action items and the open questions. A sample run:

![You type "Write up this morning's meeting." It reads the newest transcript in inbox/, files the minutes in notes/meetings/, and shows a summary, one decision and two action items: one with an owner and a due date, one flagged because the owner is unclear. It ends: Draft ready. Nothing was sent. Approve it, or tell me what to change.](docs/images/meeting-minutes.png)

---

## The local dashboard

Everything it tracks also shows up in a local dashboard: Today, Inbox, Work, Meetings, Hours, System and What's new. It is a window onto your files, so it starts mostly empty and fills in as you use the brain. See [Dashboard](docs/DASHBOARD.md).

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
It is set up not to. Slack sends refuse to run without an `--approved` flag, and a hook asks you before a Slack send goes through. Jira writes need the same flag. In the dashboard, Approve & send asks you to confirm, then works only with a one-time token minted for that one click. For email and WhatsApp, the guard is the rule in `CLAUDE.md`: show the draft, then wait for your yes. The WhatsApp bridge can also hold every send until you approve it outside the chat. Documents written to Drive stay inside your company domain once you set `WORK_DOMAIN` (see [`docs/SETUP.md`](docs/SETUP.md)), unless you publish them deliberately.

**Is my data safe?**
Your notes and credentials stay on your computer. What the AI reads goes to the AI service you use, the same as a normal chat. Credential files are never committed by the sync or the template tooling.

**How does it remember what I correct?**
Two ways. `CLAUDE.md` is the memory you write: your role, projects, languages and house rules. `/learn` is the memory it writes for you: it drafts the lesson from the session, shows it to you, and saves it only after you confirm. A lesson you confirm again and again can be promoted into `CLAUDE.md` as a standing rule.

**Can I use only part of it?**
Yes. Every command is one markdown file in `.claude/commands/`, and [`docs/SETUP.md`](docs/SETUP.md) shows how to choose only the skills you need.

**What does it cost?**
The template is free and open source under Apache-2.0. You need a paid Claude plan for Claude Code. The other services you connect are your own accounts.

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

The README's images are rebuilt from `docs/src/`: `python docs/src/render_gifs.py` for the animations, `python docs/src/render.py` for the still hero, and `python docs/src/record_inbox_demo.py` for the inbox recording.

## Versioning

Releases are tagged `vX.Y.Z`. What counts as breaking, and what a release means for a fork, is in [`docs/VERSIONING.md`](docs/VERSIONING.md).

## Changelog

The full history is in [CHANGELOG.md](CHANGELOG.md), and the dashboard shows it in the **What's new** tab. **Latest: [0.8.1] - 2026-10-01**: the README leads with what the template does for you, `/daily-update` reads your timezone from `CLAUDE.md`, and domain sharing reads `WORK_DOMAIN` from your environment.

## License

[Apache-2.0](LICENSE). Use it, fork it, sell what you build with it.

The name "AI Second Brain", the AI Circle name, and the artwork are not part of that grant. Give your fork its own name. See [`NOTICE`](NOTICE).

More AI skills: [BrianArfi.com/skills](https://BrianArfi.com/skills)
