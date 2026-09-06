---
description: Tracking - Show the shape of your work, what sits under each part, and what still has no home
argument-hint: "[node id, or 'add', or nothing for the whole tree]"
---

Work with `work-tree.md`, the outline that everything tracked in this
workspace files itself against. What to do depends on `$ARGUMENTS`:

**Nothing given. Show the tree.**
Read `work-tree.md` and print it, and against each node the count of items
tagged `[node:<id>]` across `notes/` and `inbox/`. End with two things:
the nodes with nothing under them (candidates for pruning, or work that
stalled), and the count of items tagged `[node:unfiled]`.

**A node id given. Show that part of the work.**
Print the node, its children, and every item tagged with it or with any of
its children, grouped by file. This is the payoff of the whole scheme: it
answers "what is going on with this" from what was filed on purpose, not
from whatever happens to share a word with it.

**`add` given. Propose a node.**
Ask what the new area of work is, propose an id (short, lowercase, permanent)
and where it sits in the outline, and add it once the user agrees. Never
restructure the rest of their outline while you are in there.

**If `work-tree.md` does not exist yet**, offer to draft one: read `notes/`
and `inbox/`, propose two or three top-level areas with the threads you can
actually see underneath them, and write it only once the user has corrected
it. A tree they did not agree to is a tree they will not use.

Two rules that hold in every mode:

- **Ids are permanent.** Renaming one orphans every item tagged with it. If
  the user wants a different name, change the label and leave the id alone,
  and say that is what you did.
- **Never bulk-retag on a guess.** Offering "these 14 items look like they
  belong under `client-b`, shall I move them?" is right. Moving them and
  reporting it afterwards is not.
