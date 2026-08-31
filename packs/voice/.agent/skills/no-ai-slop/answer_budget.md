# The answer budget

The wordlists in `SKILL.md` fix how a sentence reads. They cannot tell you whether the
paragraph should exist. This file does that, and it runs before every other rule in the
skill.

The failure it stops: **explanation nobody asked for, and reasoning shown to a reader who
only wanted the answer.** It reads as thorough. It costs the reader the time to find the
one sentence they came for. Simplified Technical English does not catch it, because twenty
short correct sentences still fail when two were the answer.

Two audiences, one rule. The owner reading a chat reply, and a named human reading a draft
the owner sends. Both get the answer. Neither gets the working.

## The rule

**Answer first. Stop at the answer. Reasoning is a reply to "why", not a part of every
reply.**

1. **The first sentence is the answer.** Not the context, not what you found, not how you
   looked. If the reader stops reading after one line, that line is the thing they needed.
2. **Reasoning ships only when it is asked for, or when it changes the decision.** "Why"
   in the question is an ask. A caveat that would change what the reader does next is a
   change to the decision, and it gets one sentence, not a section.
3. **Never narrate process.** What you searched, which files you opened, which tool you
   called, how many results came back, what you considered and rejected. The reader wants
   the finding. A tool ran or it did not, and the answer either stands or it does not.
4. **Never summarize what you just said.** No closing recap, no "so in short", no restating
   the answer under a heading. The reader read it.
5. **Never explain a thing that was not asked about.** Background on a system, a definition
   of a term the reader uses daily, or a tour of the options is an answer to a question
   nobody asked.
6. **Tables and bullets are for parallel data.** Three or more items that share a shape.
   They are not a way to break one answer into pieces so it looks complete. One answer is
   a sentence.
7. **An outbound message carries what the reader needs to act, and stops.** The ask, the
   deadline, the one fact that makes the ask make sense. Your reasoning is your business.

## Budgets

A budget is a ceiling that triggers a cut, not a target to fill. Most replies land far
under it. Going over means the draft gets shorter, not that the length gets justified.

| Text | Ceiling |
| :--- | :--- |
| Chat reply to the owner | 120 words |
| Slack or WhatsApp message | 80 words |
| Email body | 150 words |
| Ticket or document comment | 60 words |
| Chase note or nudge | 40 words |

Three things do not count against a budget, because they are the payload rather than
padding: a quoted message, a code block or command, and a table of parallel data the
reader asked for.

Over the ceiling is allowed in exactly one case: **the owner asked for depth.** "Explain
it", "walk me through it", "why", "give me the full analysis", "kasih detail". Then depth
is the deliverable, and the budget is off for that reply only. It comes back on the next
one.

## What to delete first

In this order, because the first cut usually removes the need for the rest.

1. The paragraph that explains why the answer is correct.
2. The sentence that says what you checked.
3. The opener that restates the question.
4. The closing summary.
5. The caveat that does not change what the reader does.
6. The heading over a two-sentence section.
7. The second example.

## Worked examples

**Asked: "is the Linear cutover still on for the 25th?"**

Fails the budget:

> Good question. I checked the decision ledger and the waiting-on records, and there are a
> few moving parts here. DEC-0171 lists the owner as the decider and it has been open past
> its due date since the 22nd. Underneath it there are three records that all have to
> resolve first, and none of them have been chased yet. Given that the free tier caps out
> well below the open ticket count, the migration would be a demo rather than a migration.
> So the honest answer is that the date is not really under your control right now.

Passes:

> No. Three unchased records block it, all owned by Fred. The free tier caps below the open
> ticket count, so a migration today is a demo.

**Asked: "what broke the build?"**

Fails: "Let me walk you through what I found. I started by checking the CI logs, then..."

Passes: "A missing `ffmpeg` on the runner. It is installed at `~/.local/bin`, which the
CI PATH does not include."

**A chase note that fails:** 90 words of context on why the item matters, the history of
the thread, and an apology for chasing.

**The same note passing:**

> Hi Fred, the Linear paid tier still needs your yes before we can move the 855 tickets.
> Can you confirm today?

## Checks

Run these on any draft, in any language, before the wordlist rules in `SKILL.md`.

1. Is the answer in the first sentence?
2. Is every remaining sentence something the reader asked for, or something that changes
   what they do next?
3. Zero sentences about your own process, tools, or search?
4. Zero closing summary or recap?
5. Under the ceiling for this channel, or did the owner ask for depth?
6. Does every table and bullet list hold parallel data rather than one broken-up answer?
7. If you cut the draft in half right now, what breaks? If nothing breaks, cut it.

## Where the machine can check this, and where it cannot

`.claude/hooks/send_slop_guard.py` reads outbound text before a send, so it counts words
and names an unrequested rationale section on Slack, email, and document comments. That is
a warning rather than a block, because a long message is sometimes right and the guard
cannot tell.

**A chat reply to the owner has no machine check.** Hooks fire on tool calls, and no hook
sees assistant text on its way to the human. That half of this file runs on the rule alone.
The owner enforces it by saying it is too long, and that correction is worth saving.
