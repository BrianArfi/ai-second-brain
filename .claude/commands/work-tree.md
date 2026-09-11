---
description: Tracking - Show the shape of your work, what sits under each part, and what still has no home
argument-hint: "[node id, or 'add', or nothing for the whole tree]"
---

Work with `journal/state/work_tree.json`, the single taxonomy every tracked item
in this repo files itself against (see `## Every Ticket Belongs To A Work-Tree
Node` in `CLAUDE.md`). The tree is driven by `work_tree.py`, never hand-edited.

What to do depends on `$ARGUMENTS`:

**Nothing given. Show the tree and its coverage.**

```bash
python3 .agent/scripts/work_tree.py coverage
python3 .agent/scripts/work_tree_link.py --check
```

Print the tree with the count of records filed under each node. End with two
things: the nodes with nothing under them (stalled work, or a candidate for
`status: archived`), and the count of records still on `unfiled`. Every unfiled
record is triage work, so name them rather than reporting a number.

**A node id given. Show that part of the work.**

```bash
python3 .agent/scripts/work_tree.py show <id>
```

Print the node, its children, and every commitment, decision, waiting-on, chase
and todo line filed under it or under any child, grouped by ledger. This is the
payoff of the whole scheme: it answers "what is going on with this" from what
was filed on purpose, not from whatever happens to share a word with it. The
generated view is [`journal/work_tree_index.md`](../../journal/work_tree_index.md).

**`add` given. Propose a node.**

Ask what the new area of work is, propose an id (short, lowercase, permanent)
and its parent, then create it once the owner agrees:

```bash
python3 .agent/scripts/work_tree.py add-node --id <id> --label "<label>" --kind item --parent <parent>
```

Never restructure the rest of the tree while you are in there.

**Not sure which node something belongs to?**

```bash
python3 .agent/scripts/work_tree.py find "<search string>"
```

Filing a record under a plausible-but-wrong node is worse than not filing it: it
reports as tracked and never surfaces again. Batch every ambiguous record from
the turn into ONE `AskUserQuestion` at the end, each with two or three candidate
nodes plus "new node".

Two rules that hold in every mode:

- **Ids are permanent.** Renaming one orphans every record carrying it. Change
  the label and leave the id alone, and say that is what you did. Retire a node
  with `status: archived`, never by deleting it.
- **Never bulk-refile on a guess.** Offering "these 14 records look like they
  belong under `apple-uat`, shall I move them?" is right. Moving them and
  reporting it afterwards is not. Refile one at a time with
  `<ledger>.py refile <ID> --node <id>`.
- **`refs` is derived, never curated.** `work_tree_link.py` rebuilds it from the
  records. Hand-curating refs is what let 659 records drift out of the tree.
