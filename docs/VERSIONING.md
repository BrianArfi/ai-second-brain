# Versioning

The template is versioned `MAJOR.MINOR.PATCH`, tagged `vX.Y.Z`.

It is not a library, so "the API" is not a set of functions. It is the set of
things a fork depends on and cannot see changing:

- the folder layout (`.agent/skills/`, `.claude/commands/`, `.claude/agents/`,
  `.claude/hooks/`, `journal/state/`),
- the `SKILL.md` frontmatter keys,
- the command line of any script under `.agent/scripts/`,
- the shape of the files in `journal/state/`,
- the environment variable names in `.env.example`.

| Change | Bump |
| :--- | :--- |
| Any of the above changes shape, or a skill is removed or renamed | MAJOR |
| A skill, command, agent, or hook is added; a script grows a flag | MINOR |
| A fix, a doc, a message, anything a fork cannot observe as a contract | PATCH |

## Before 1.0

While the major is 0, MINOR carries the breaking changes. That is the honest
signal: the contracts are still moving, and a fork should read the changelog
before updating rather than assume nothing moved.

## What a release means for a fork

`/update-harness` merges upstream into a fork. A MAJOR release is the one that
needs reading first, because it is the one that can require the fork to change
something it wrote itself. The changelog entry for a MAJOR release says what,
and what to do about it.

## What is not versioned here

The desktop and mobile applications carry their own versions and their own
release cadence. A template version says nothing about which application
version reads it.
