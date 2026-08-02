---
description: Sweep connected email and Slack for things needing attention
---

Sweep the tools this workspace is connected to and turn anything that needs
attention into a note in `inbox/`, one note per item. Never act on anything
found; only capture it and ask before doing more.

1. **Check what's connected.** Run `claude mcp list`. If nothing relevant
   (Gmail, Slack) is connected, stop here and say so: point the user to
   `/connect-tools` and don't try to guess or invent inbox items from
   nothing.

2. **Sweep each connected source:**
   - **Gmail**: recent unread or flagged emails, anything that looks like
     it's waiting on a reply from the user.
   - **Slack**: recent DMs and mentions across channels the user has
     access to, anything directed at them that hasn't been answered.

   Keep the window reasonable (recent unread/unresolved items, not the
   entire history of the account).

3. **For each item found, write one note in `inbox/`** with:
   - a short, descriptive filename (kebab-case, dated if useful, e.g.
     `inbox/2026-07-<YOUR_DRIVE_ID>.md`)
   - the source (Gmail or Slack), sender, and a link or clear reference back
     to the original if the tool provides one
   - a one- or two-line summary of what it's asking or waiting on, in your
     own words, not the full raw text pasted in
   - what looks like it needs to happen next, if anything is obvious

4. **Don't file duplicates.** If something very similar already has a note
   in `inbox/` or `notes/`, mention the overlap instead of creating a
   second note for it.

5. **Never reply, react, archive, or mark anything as read.** This command
   only reads and captures. If something looks urgent enough that it should
   be answered right away, say so and ask; don't draft or send anything
   here, that's what `/follow-ups` is for, and only with explicit approval.

6. **Summarize the sweep** when done: how many items were found, how many
   were new versus already captured, and what most needs the user's eyes
   first.

7. **Split what needs the user, one sub-session per thing.** A sweep is the
   case branching exists for: several unrelated people are waiting, and
   answering them in one chat means answering all of them at once. Once the
   notes are written, follow the **Branching Into Sub-Sessions** protocol in
   `CLAUDE.md` for the items that genuinely need the user's own reply or
   decision. Items that only needed capturing stay as notes and do not
   branch, and neither does anything you can simply finish yourself.

   Each `brief` must carry the item's real context: who is asking, what they
   asked, the link back, and what you think the answer is. A sub-session
   starts with no memory of this sweep.
