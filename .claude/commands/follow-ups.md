---
description: Comms - Find open loops and promises, draft messages for your approval
---

Scan this workspace for things that are still open, such as promises made,
replies still owed, or people waiting on the user, and produce a follow-up
list. Draft messages where useful, but **never send or post anything**
without the user explicitly approving each one.

1. **Read `notes/` and `inbox/`** for anything that reads like an open loop:
   - something the user said they'd do or send, that doesn't look finished
   - a question aimed at the user that doesn't have a written answer yet
   - a person or team described as waiting on the user for something

2. **If Slack, Gmail, or Jira are connected** (check with `claude mcp list`),
   also check for:
   - Slack threads or DMs where the last message was to the user and it's
     been a while
   - emails awaiting a reply
   - Jira tickets assigned to or mentioning the user with no recent activity

   If none of those are connected, work from `notes/`/`inbox/` alone and
   say so. Don't invent status by guessing what's happening outside this
   workspace.

3. **Produce a follow-up list**, grouped by person or thread, each item
   showing:
   - what's open and since when, as best as can be told from the files
   - where it came from (note, inbox item, email, Slack thread, ticket)
   - who's actually waiting on whom: don't assume it's always the user;
     say plainly if the user is the one waiting on someone else

4. **For items where the user is the one who owes a reply**, draft a short
   message for each, in the tone the source suggests (short reply for a
   quick Slack ping, fuller for an email). Show every draft plainly labeled
   with who it's to and where it would go.

5. **Wait for explicit approval before sending anything.** Never call a
   send/post tool for any drafted message without the user saying yes to
   that specific one. If several are approved at once, still send them one
   at a time and confirm each went out.

6. **For items where someone else owes the user a reply**, don't draft an
   outbound message by default; just flag it as waiting-on and let the
   user decide whether it's worth a nudge.

7. **Split what still needs the user, one sub-session per thing.** This is a
   standing pre-approval: branch directly, do not offer first. Once the list
   and the drafts exist, follow the **Branching Into Sub-Sessions** protocol
   in `CLAUDE.md` for the items that genuinely need the user's own reply or
   decision. Anything you already finished stays here and does not branch,
   and neither does a set of items that all turn on the same single call.
   One item is not a branch.

   Each `brief` must carry the item's real context: who is asking, what they
   asked, the link back, the draft you already wrote, and what you think the
   answer is. A sub-session starts with no memory of this run. Branching
   creates sessions, it never sends, so the approval gate in step 5 still
   applies inside each sub-session.

Keep the list grounded in what's actually written down or visible through a
connected tool. If a "promise" is vague or ambiguous, say so rather than
guessing what was meant.
