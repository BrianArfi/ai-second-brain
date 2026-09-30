---
description: Comms - Sweep every connected source (Slack threads, Gmail, Jira, Docs, calendar, GitHub) for what needs the owner, minus the noise
---

Sweep every connected source and turn what is waiting on the owner into notes in
`inbox/`, one note per item. Never act on anything found; only capture it and
ask before doing more.

Engine: `.agent/skills/sweep-engine/` (sweep v2, design in
`docs/sweep_v2_design.md`).
It replaces the hand-rolled passes that 29 Sep 2026 needed three times over:
threads the owner posted in, Jira, and Doc comments are adapters now, not extras.

0. **Run the sweep engine. It is the sweep, not a supplement.**

   ```
   python3 .agent/skills/sweep-engine/run_sweep.py --refresh-ledger
   python3 .agent/skills/access-watch/scripts/access_watch.py report --days 90
   ```

   The first line runs seven adapters, each in its own process with its own
   timeout: `slack_ledger` (open items in the mention ledger), `slack_threads`
   (every thread the owner posted in over 10 days where someone replied after him),
   `gmail` (inbox, 3 days, with Jira, Confluence, Docs-comment and share-request
   notification mail routed into the ticket or doc it is about), `jira` (both
   sites: assigned, reported, watched, plus any key a notification mail named),
   `calendar` (invites without an RSVP, 14 days, recurring series collapsed),
   `linear` (unread notifications) and `github` (review requests). Then
   `sweep_core.py` collapses each thread, ticket or doc into one item, closes
   what the owner already answered, and drops noise by rule.

   `--refresh-ledger` runs the mention ledger sweep first. If the cron holds its
   lock, the run goes on with the last ledger state and says so.

   The second line is the access pass. It verifies Drive share requests against
   live permissions, so anything already granted drops off by itself. Treat its
   output as blocking work: those people cannot do their job until the owner acts.

   **Read the Sources block of the report first.** Any adapter marked `FAILED`
   or `skipped` is a hole in this sweep. Name it in the summary. Never report a
   clean sweep with a failed adapter.

   **Do not substitute `slack_client.py --action search` for this.** On 5 Aug 2026
   a sweep did exactly that and returned 7 items while the ledger had 76. A
   mention search is blind to 1:1 DMs and to replies in threads the owner started.

1. **Label what survived.** Spawn a `haiku` subagent with
   `.agent/skills/sweep-engine/label_prompt.md`, input
   `journal/sweep/latest/survivors.json`, output
   `journal/sweep/latest/labels.json`. Then:

   ```
   python3 .agent/skills/sweep-engine/sweep_core.py label --state journal/sweep/state.json --labels journal/sweep/latest/labels.json --filter .agent/skills/sweep-engine/filter.json --out journal/sweep/latest
   ```

   `journal/sweep/latest/report.md` is now ranked: `blocking_someone` and `needs_decision` first, VIPs (YourManager,
   Julie, Teammate) first within that, then age. Items older than 7 days sit in a
   collapsed "Older" list; the filtered-out list names the rule behind every drop.

2. **Check the filtered-out list for a false drop** before writing notes. If a
   real ask was dropped, surface it anyway and teach the filter
   (`sweep_core.py teach --filter .agent/skills/sweep-engine/filter.json --allow <sender>`).
   Noise that reached the top: `--deny <sender>`. The filter file is committed,
   so every session and cron run learns it.

3. **For each item in "Needs you", write one note in `inbox/`** with:
   - a short, descriptive filename (kebab-case, dated, e.g.
     `inbox/2026-07-<YOUR_DRIVE_ID>.md`)
   - the source, sender, and the link back to the original
   - a one- or two-line summary of what it asks, in your own words
   - what looks like it needs to happen next, if anything is obvious

   Read the full message for anything you intend to act on; the report
   truncates. One FYI digest note carries the `fyi` items.

4. **Don't file duplicates.** If something very similar already has a note
   in `inbox/` or `notes/`, mention the overlap instead of creating a
   second note for it. `dismiss` an item the owner says is not his
   (`sweep_core.py dismiss --state journal/sweep/state.json --key <key>`); it
   comes back only when something new arrives in it.

5. **Never reply, react, archive, RSVP, or mark anything as read.** This command
   only reads and captures. If something looks urgent enough that it should
   be answered right away, say so and ask; don't draft or send anything
   here, that's what `/follow-ups` is for, and only with explicit approval.

6. **Summarize the sweep** when done: items per source, how many were new
   versus already captured, how many the filter dropped, any adapter that
   failed, and what most needs the owner's eyes first.

7. **Split what needs the owner, one sub-session per thing.** A sweep is the
   case branching exists for: several unrelated people are waiting, and
   answering them in one chat means answering all of them at once. This is a
   standing pre-approval, so branch directly and do not offer first. Once the
   notes are written, follow the **Branching Into Sub-Sessions** protocol in
   `CLAUDE.md` for the items that genuinely need the owner's own reply or
   decision, with `conversationKey` set to the item's `key` from the report.
   Items that only needed capturing stay as notes and do not branch, and
   neither does anything you can simply finish yourself.

   Each `brief` must carry the item's real context: who is asking, what they
   asked, the link back, and what you think the answer is. A sub-session
   starts with no memory of this sweep.
