# Commands

You do not have to memorize these. Ask in plain words and a natural request follows the same procedure as the slash command, because `CLAUDE.md` routes both to the same file. Every command is one markdown file in `.claude/commands/`.

| Ask for | What you get | Use it for |
| :--- | :--- | :--- |
| [`/daily-update`](../.claude/commands/daily-update.md) | Morning mode reads your calendar, email, Slack and yesterday's meetings, then gives the top priorities. Evening mode scores what got done against the morning plan | Starting the day with a plan. Closing the day with an honest carryover |
| [`/meeting-notes`](../.claude/commands/meeting-notes.md) and [`/mom`](../.claude/commands/mom.md) | Minutes from a transcript or rough notes: decisions, action items with owner and date, open questions. An unclear owner is flagged, not guessed | Notes after a client call. Action items nobody loses |
| [`/meeting-prep`](../.claude/commands/meeting-prep.md) | A one-page brief: who attends, context from past notes, open items, questions worth raising | Walking into an important meeting with the context in hand |
| [`/weekly-report`](../.claude/commands/weekly-report.md) | A progress report built from every meeting and note of the week, weighted by delivered work, not recency. It updates the existing Drive Doc in place | The weekly update for your manager |
| [`/follow-ups`](../.claude/commands/follow-ups.md) | A list of open loops: promises made, replies owed, people waiting on you. Drafts for each, sent only after you approve each one | Clearing overdue replies on Slack and email |
| [`/prd`](../.claude/commands/prd.md) | A PRD that starts with the right questions: who needs this most, what they do today instead, how you will know it worked. If the PRD is already in Drive, it revises that one | Specs for a new feature |
| [`/mockup`](../.claude/commands/mockup.md), [`/deck`](../.claude/commands/deck.md), [`/artifact`](../.claude/commands/artifact.md) | One HTML file that opens in any browser: a clickable prototype with presenter controls, a keyboard driven slide deck, or an explainer page | A demo tomorrow. A presentation. A proposal to send |
| [`/learn`](../.claude/commands/learn.md) | Lessons from the session, drafted as rules. You confirm before anything is saved | Making a correction permanent |

## Example requests

| Ask for | Example |
| :--- | :--- |
| `/daily-update` | "Prep my day." |
| `/meeting-notes` | "Write up this morning's meeting." |
| `/weekly-report` | "Write this week's progress report." |
| `/learn` | "Never post to Slack without asking me first. /learn" |

## More commands

More commands ship for notes and search (`/capture-note`, `/search`, `/remember`, `/organize-inbox`), weekly reflection and planning, tickets, user stories and incident reviews.
