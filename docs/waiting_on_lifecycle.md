# Deprioritise and discard: how a waiting-on record ends

You asked four things. This is the answer to all four, in your order, with the
hardest one last because it is the one the rest depends on.

## 1. Time-sensitive items, re-checked

An item tied to a date dies when the date passes, and it should not need a
human to notice. Today's cleanup dropped eight of these by hand: a UAT invite
due 19 July, a demo on 30 July, a Jordan workshop that ran on 23 August.

`answer_hunt.py` now re-checks these automatically before anything is dropped,
because "the date passed" and "the thing still matters" are different facts. An
Apple Store launch blocker does not stop mattering because its target slipped.

## 2. Over a month, no update, no longer valid, remove

Agreed, with one word changed. **Over a month, no update, no trace, and a notice
that went unanswered.** Age alone cannot carry a delete. A record can be 54 days
old and answered, or 54 days old and load-bearing, and nothing about the number
54 tells you which.

## 3. Over three weeks with no response, deprioritise and prep for discard

This is the right instinct and it is now a real stage, not a feeling. Five
stages, and a record has to earn each one:

| Stage | Trigger | What it means |
| :--- | :--- | :--- |
| `ACTIVE` | under 21 days | Chased normally |
| `DEPRIORITISED` | 21 days, no response | Stops competing with live work in briefings. Still visible, still real. |
| `NOTICE_DUE` | deprioritised and the hunt found no trace | A final notice is owed to the owner |
| `NOTICE_SENT` | the notice went out | A 7 day clock starts |
| `DISCARD_READY` | 30 days, notice sent 7+ days ago, still nothing | Only now may it be dropped |

Deprioritised is not a waiting room on the way to the bin. Most records should
sit there and get answered. It exists so the 297 breached records stop shouting
at the same volume as the eight that are actually blocking a launch.

## 4. Escalation before any of that

A record does not get dropped without the owner being told, once, plainly, that
it is about to be. The notice is deliberately not another chase:

- **One message per person, not per item.** The point is that they see the whole
  list in one place. Teammate has 25 open items. Twenty-five separate pings is why
  none of them got answered.
- **It carries a date.** "If nothing comes back by 26 September, I will drop
  these." That is not a threat, it is the thing that makes the list actionable:
  saying "not needed any more" is now a valid, cheap answer.
- **It is approval-gated** like every other outgoing message. The script drafts
  it and stops.

```bash
python3 .agent/scripts/deprio_discard.py notices --hunt <hunt.json>
python3 .agent/scripts/deprio_discard.py mark-notice-sent --owner "Teammate Meer"
```

`mark-notice-sent` starts the grace clock and stamps `last_nudge_at`, so the
chase queue stops re-chasing something that has already had its final notice.

---

## 5. The hard one: how do I know they did not reply, and that I did not miss it

Short answer: **the old check was not good enough to carry a delete, and I would
not have trusted it either.**

The chase queue's verifier looks in exactly one place: the ask's own Slack
thread, plus the channel it was posted in. That is deliberately narrow, and
narrow is right for deciding whether to send a chase, because a loose check
silently buries real breaches. But it is nowhere near enough to delete on. Its
"no reply" means no reply *in one place*. It said "could not verify" for 300 of
363 records, and for the 199 with no Slack permalink it cannot say anything at
all.

So `answer_hunt.py` searches six sources, split by how much weight each carries.

**Strong sources. A hit here is attributable to the owner, on this ask.**

| Probe | What it reads |
| :--- | :--- |
| `thread` | the ask's own thread, any non-the owner reply after the ask |
| `channel` | the ask's channel, an owner message carrying two or more of the record's distinctive terms |
| `dm` | the direct message with that person, same two-term bar |

**Weak sources. A hit here is a lead, never a verdict.**

| Probe | What it reads |
| :--- | :--- |
| `search` | Slack search across the whole workspace, requiring two distinct terms in the same message |
| `meetings` | a MOM dated after the ask carrying every term |
| `decisions` | a decision recorded after the ask carrying every term |

Verdicts:

- `ANSWERED_LIKELY` a strong probe hit. Read it, then close the record.
- `LEAD` only weak hits. A person reads the lead before anything happens.
- `NO_TRACE` everything that could run found nothing, and at least three ran.
- `UNVERIFIABLE` fewer than three probes could run. **Not a conclusion.**

### Why the weak and strong split exists, in one concrete failure

The first version of this script had no such split, and it counted any two hits
as an answer. It immediately declared `WAIT-0150` answered. The evidence was the
word "implementation" appearing in an unrelated Hero Banner CTA message in
`#exampleco-marketplace-tech`, plus three MOMs that happened to contain the same
generic words.

That is what a loose matcher looks like from the inside: confident, specific,
and wrong. It is also exactly the class of mistake that a delete rule turns from
an annoyance into lost work.

Three things changed after that:

1. **A generic-vocabulary stoplist.** `implementation`, `review`, `estimate`,
   `status`, `integration` and forty more are excluded from term selection.
   They appear in half the workspace, so a match on one is noise that reads
   like evidence.
2. **Two terms in the same message**, not one term anywhere.
3. **Weak sources cannot produce a verdict.** `WAIT-0150` now reports `LEAD`,
   which routes it to a human instead of to the bin.

### The four gates

Nothing is discarded unless all four hold:

1. **Age**: 30 days or more since the ask
2. **Evidence**: the hunt returned `NO_TRACE`. `LEAD` means read it first.
   `UNVERIFIABLE` means the check could not run, which is not permission.
3. **Notice**: a final notice naming this record went to the owner
4. **Grace**: 7 days passed since that notice with no response

Gate 2 is the one that answers your question. A record is never discarded
because a timer ran out. It is discarded because six sources were checked, at
least three of them could actually be read, none of them found a reply, and then
the person was told directly and still said nothing.

### Where it is still weak, and I would rather say so

- **No email and no Jira probe yet.** An answer given in a Gmail thread or a
  Jira comment reads as `NO_TRACE`. Both are reachable through connectors this
  repo already has, and both should be added before the discard step runs
  unattended.
- **48 owners do not resolve to a Slack id**, so `channel` and `dm` cannot run
  for them. Those records land on `UNVERIFIABLE` and stay, which is the correct
  failure direction but means they never clear.
- **`search.messages` only reaches what your account can see.** A private
  channel you are not in is invisible, and reads as no trace.

Because of those three, the discard step stays manual: `discard` prints what
passes and does nothing without `--apply`. It should stay that way until the
email and Jira probes exist.

---

## Running it

```bash
# 1. hunt everything past the deprioritise line
python3 .agent/scripts/answer_hunt.py --older-than 21 --json /tmp/hunt.json

# 2. see what is in which stage
python3 .agent/scripts/deprio_discard.py report --hunt /tmp/hunt.json

# 3. draft the final notices, one per person
python3 .agent/scripts/deprio_discard.py notices --hunt /tmp/hunt.json

# 4. after a notice actually goes out
python3 .agent/scripts/deprio_discard.py mark-notice-sent --owner "<name>"

# 5. a week later, see what has earned a discard
python3 .agent/scripts/deprio_discard.py discard --hunt /tmp/hunt.json
```

Stage state lives in `journal/state/waiting_lifecycle.json`, which is this
script's own file. It does not add fields to `waiting_on.json`: that ledger is
written by seven cron jobs under a lock, and widening its schema to carry
workflow state would be a change to all of them.
