# Contributing

Thanks for looking. This document tells you what this repository is, what a good
contribution looks like here, and the one thing about the release process you
have to know before you spend an evening on a patch.

## What this repository is

`ai-second-brain` is a **workspace template**. You clone it, run `install.sh`,
and an AI coding agent turns it into your own second brain: your `CLAUDE.md`,
your clients, your ledgers.

It is not the desktop app and not the mobile app. Those are separate, closed
repositories. They read this template and load its capabilities, which is why
the folder layout here is a contract rather than a preference.

Three layers, and every contribution lands in exactly one of them:

| Layer | Folder | What it is |
| :--- | :--- | :--- |
| The brain | `CLAUDE.md.template` | Always-on rules and routing |
| The reflexes | `.claude/commands/`, `.claude/agents/`, `.claude/hooks/` | Slash commands, subagents, guards |
| The hands | `.agent/skills/`, `.agent/scripts/` | Scripts that touch real services |

## Before you write code

**Open an issue first for anything larger than a fix.** A new skill, a new
command, a change to `CLAUDE.md.template`, or a change to the folder layout all
need agreement before the code exists. Small fixes (a broken path, a wrong flag,
a typo, a crash) need no issue: send the pull request.

## How the pipeline actually works, and why it matters to you

This template is generated. A private working repository is the upstream for
most skills and scripts, and a scrub-and-export pass copies them here. That has
one consequence you need to plan around:

> **A patch to a generated file can be overwritten by the next export.**

So a merged pull request is only half the job; the maintainer has to carry the
same change upstream. Two things follow:

- Say in the pull request body which files you touched, so the maintainer knows
  what has to travel back.
- Prefer **new** files (a new skill, a new command) over edits to existing
  generated ones. New files are never in the export's way.

Files that are safe to edit directly, and are not generated: `README.md`,
`docs/`, `install.sh`, `setup/`, `CONTRIBUTING.md`, `SECURITY.md`, this file's
neighbours, and anything under `.github/`.

## Adding a skill

A skill is a folder under `.agent/skills/`. The shape is fixed:

```
.agent/skills/your-skill/
  SKILL.md          what it does, when to use it, how to invoke it
  scripts/
    your_client.py  the actual work
  token.env         credentials, gitignored, never committed
```

`SKILL.md` starts with YAML frontmatter, and the `description` is what an agent
reads to decide whether to use your skill. Write it for that reader:

```markdown
---
name: your-skill
description: One line saying what it does and when to reach for it.
---
```

Rules that get a skill rejected if broken:

1. **No credential in the repository.** Read from `token.env` or the
   environment. Never a default value that is a real key.
2. **No personal data.** No real client names, no Google Doc IDs, no email
   addresses, no absolute home directories, no ticket keys. There is an
   automated scrub check and it fails the build.
3. **Anything that sends, publishes, or deletes is approval-gated.** The
   convention in this repository is an explicit `--approved` flag with no
   environment-variable bypass. Follow it.
4. **Print what you did.** A skill that writes something remote prints the id
   and the link, or it reports failure.
5. **Cross-platform.** macOS, Linux, and WSL. Use `pathlib`, not string paths.

`docs/ARCHITECTURE.md` has a minimum viable skill script you can copy.

## Adding a command, agent, or hook

- **Command** is one markdown file in `.claude/commands/`, with YAML frontmatter
  carrying a `description`. It is a standard operating procedure written for an agent, not
  a script. Say what to read first, what to produce, and what to never do.
- **Agent** is one markdown file in `.claude/agents/`. State its model tier and
  keep its job narrow. An agent that both gathers and decides is two agents.
- **Hook** is a script in `.claude/hooks/`. It must exit 0 on anything it does
  not understand, must never make a network call on a write, and must run in
  well under a second. A slow hook gets disabled by users, which is the same as
  not existing.

## Style

- Python 3.9+, standard library first. A new third-party dependency needs a
  reason in the pull request.
- Documentation is written in plain, direct English. Simplified Technical
  English is the house style: short sentences, active voice, one instruction per
  sentence, the simplest exact word.
- **No em-dash.** There is a hook that catches it. Rewrite the sentence instead
  of substituting another dash.
- Comments explain why, not what. The most valuable comment in this repository
  is the one recording the failure that made the code look like that.

## Pull requests

- One concern per pull request.
- Say what you tested and on which operating system. "Ran it against a real
  Slack workspace on macOS" is worth more than a green checkmark.
- Keep commits conventional: `feat:`, `fix:`, `docs:`, `chore:`.
- If it changes what a user has to do, update `README.md` or `docs/` in the same
  pull request.

## Reviews

The maintainer reviews when he can, and this is not his job. Expect days, not
hours. A pull request that adds a capability the maintainer does not use himself
needs to carry its own tests, because nobody else can catch it breaking.

By contributing you agree that your work is licensed under Apache-2.0, the same
as the rest of this repository.
