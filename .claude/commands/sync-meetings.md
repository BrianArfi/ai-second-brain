---
description: Product - Pull recent meetings out of whichever note-taker you connected and file them as notes
argument-hint: "[how far back, or the name of one meeting]"
---

Bring meetings into the workspace: $ARGUMENTS

If nothing was said, take the last 7 days.

## Where the meetings come from

Whichever note-taker is connected under Settings > Connected tools. They all sit
behind the same kind of connection, so this command does not care which one it
is: Fireflies, Fathom, Granola, Krisp, Sembly, or anything added through Custom
MCP. Look at the tools you actually have and use those.

Tool names differ per service and the shapes disagree. Fireflies calls it a
transcript, Krisp calls it a document, Granola puts the verbatim text behind a
paid plan. Read the tool descriptions rather than assuming a name. Two rules
hold everywhere:

- **List first, fetch second.** Every one of these has a cheap "what meetings
  are there" call and an expensive "give me the whole transcript" call. Do not
  pull transcripts for meetings you are about to skip.
- **If more than one note-taker is connected, ask which to use** before pulling
  anything, unless the request already names one. Two services often recorded
  the same call, and syncing both files the meeting twice under two names.

If nothing is connected, say so and point at Settings > Connected tools rather
than going looking in `inbox/`.

## What to write

One file per meeting, at:

```
notes/meetings/YYYY-MM-DD_<short-slug>_<source>.md
```

`<source>` is the service it came from, lowercase: `fireflies`, `granola`,
`krisp`. That suffix is the whole deduplication story, so never leave it off.
The date is the day the meeting happened, not today.

**Skip anything already filed.** Check `notes/meetings/` for a file with the
same date and a similar slug before writing, and say what you skipped. Re-running
this command twice in a day must not produce a second copy of Monday's standup.

Each file gets:

```markdown
# <meeting title>

**Recorded**: <when, in full>
**Source**: <service>
**Attendees**: <names, as the service gave them>

## Summary

One paragraph. What it was for, and what changed as a result.

## Decisions

Only things genuinely settled. Discussed-and-left-open is an open question.

## Action items

- what / owner / by when [node:<id>]

## Open questions

## Transcript

<the verbatim text, speaker-labelled, with timestamps if the service gave them>
```

Leave a section out entirely if the meeting genuinely has nothing for it. An
empty "Decisions" heading reads as a meeting where nothing was decided, which is
a claim, not an absence.

## Rules

- **Never invent.** No attendee, date, decision or commitment that is not in the
  source. If the transcript is a summary rather than verbatim text (Granola on a
  free plan does this), say so under the Transcript heading instead of writing a
  transcript that is really a paraphrase.
- **Unresolved speakers stay unresolved.** "Speaker 2" is left as "Speaker 2".
  Guessing who spoke puts words in a named person's mouth.
- **Action items need an owner from the source.** If the source did not name
  one, write "owner unclear" rather than defaulting it to me.
- **One work-tree node per meeting.** Decide it once and let the items inherit
  it. Ask if the meeting does not obviously belong anywhere; do not spread one
  meeting across four nodes.

Finish by listing what you filed and what you skipped, with paths.
