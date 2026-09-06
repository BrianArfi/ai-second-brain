---
description: Thinking - Split this conversation into focused sub-sessions, one per thing needing a decision
argument-hint: "[optional: what to split on]"
---

Split the work in this conversation into sub-sessions, following the
**Branching Into Sub-Sessions** protocol in `CLAUDE.md`.

1. **List what is actually open here.** Go back through this conversation and
   write out every distinct thing still waiting on the human: a reply owed, a
   decision to make, a document to approve. Ignore anything you can finish
   yourself, and just finish it instead.

2. **Group them.** Items that turn on the same underlying decision belong in
   one session, however many messages or comments they arrived as. Items that
   have nothing to do with each other belong in separate ones.

3. **If the grouping is genuinely ambiguous, ask before splitting.** Use
   AskUserQuestion and propose the concrete groupings, not an open question.

4. **Write the branch request** to `.asb/branches/requests/`, one file
   covering all the branches. Put real context in every `brief`: each
   sub-session starts blank and sees nothing from this conversation.

5. **Say what you split and why**, one line per branch, and stop. The app
   creates and starts the sessions itself.

If nothing here needs the human's separate attention, say so and do not
branch. One item is not a branch, it is just this conversation.
