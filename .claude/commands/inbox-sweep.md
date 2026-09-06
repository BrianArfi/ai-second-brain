---
description: Comms - Sweep connected email and Slack for things needing attention
---

Sweep the tools this workspace is connected to and turn anything that needs
attention into a note in `inbox/`, one note per item. Never act on anything
found; only capture it and ask before doing more.

0. **Slack: run the mention ledger FIRST. It is the sweep, not a supplement.**

   ```
   python3 .agent/skills/slack-tracker/scripts/mention_ledger.py sweep
   python3 .agent/skills/slack-tracker/scripts/mention_ledger.py report
   python3 .agent/skills/access-watch/scripts/access_watch.py report --days 90
   ```

   The third line is the access pass. It covers the half of the problem Slack
   cannot see: a Google Drive "Share request" arrives by **email**. On 1 Sep 2026
   five were pending, the oldest 39 days, and no sweep had ever read them. It
   verifies against live Drive permissions, so anything already granted drops off
   by itself. Treat its output as blocking work: those people cannot do their job
   until the owner acts.

   This is mandatory and it replaces any hand-rolled Slack search. Same rule
   the morning/evening updates already follow (`.agent/workflows/morning-update.md`
   step 0). The ledger holds state across runs and closes items only when the owner
   has *mechanically* answered them, so it surfaces the 5-day-old DM that a
   recency window would drop.

   **Do not substitute `slack_client.py --action search` for this.** On 5 Aug 2026
   a sweep did exactly that, querying `<@<SLACK_ID>> after:<date>`, and returned
   7 items while the ledger had 76. A mention search is structurally blind to
   **1:1 DMs**, because nobody @-mentions you in a DM, and it under-reports thread
   replies. Every missed item that day was a DM or a thread reply.

   Read the ledger's `[open]` items in full. `report` truncates each preview, so
   pull the full message for anything you intend to act on.

1. **Check what else is connected.** Run `claude mcp list` for Gmail. If Gmail is
   not connected, say so and tell the owner to open **Settings, Connected tools** and
   press Connect on the **Gmail** card. Never hand out `claude mcp add` lines.
   Don't guess or invent inbox items from nothing.

2. **Sweep Gmail** (Slack is already covered by step 0): recent unread or flagged
   emails, anything that looks like it's waiting on a reply from the user. Keep
   the window reasonable (recent unread/unresolved items, not the entire history
   of the account).

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
   answering them in one chat means answering all of them at once. This is a
   standing pre-approval, so branch directly and do not offer first. Once the
   notes are written, follow the **Branching Into Sub-Sessions** protocol in
   `CLAUDE.md` for the items that genuinely need the user's own reply or
   decision. Items that only needed capturing stay as notes and do not
   branch, and neither does anything you can simply finish yourself.

   Each `brief` must carry the item's real context: who is asking, what they
   asked, the link back, and what you think the answer is. A sub-session
   starts with no memory of this sweep.
