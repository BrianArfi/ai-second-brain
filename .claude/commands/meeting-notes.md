---
description: Product - Turn a transcript or rough notes into clean minutes with owned action items
argument-hint: "<paste notes, or name the meeting>"
---

Write up this meeting: $ARGUMENTS

If nothing was pasted, look for the most recent transcript or raw notes in
`inbox/` and use that, saying which file you picked.

Produce, in this order:

1. **One paragraph** on what the meeting was actually for and what changed as a
   result. Someone who missed it should be able to stop reading here.
2. **Decisions** — only things genuinely settled. A topic that was discussed and
   left open is not a decision; it belongs in Open questions.
3. **Action items** — one line each: what, who owns it, by when, and its
   work-tree node as `[node:<id>]`. Use the owner named in the notes. Never
   assign an item to me by default just because the owner was not stated; write
   "owner unclear" and flag it. A meeting usually maps to one node, so decide it
   once for the meeting and let the items inherit it; ask if the meeting itself
   does not obviously belong anywhere.
4. **Open questions** — what was raised and not resolved, with who needs to
   answer.

Then file it as `notes/meetings/YYYY-MM-DD-<short-name>.md` and tell me the path.

Rules:

- Never invent an attendee, a date, or a commitment that is not in the source.
- If a transcript has unresolved speaker labels ("Speaker 2"), leave them as-is
  rather than guessing who spoke.
- Quote the source for anything that reads as a firm commitment, so it can be
  checked later.
