# voice

Removes AI voice from everything the workspace writes.

Install into a workspace that already exists:

```bash
python3 tools/pack.py check packs/voice      # see what it would write
python3 tools/pack.py install packs/voice    # write it, refusing on conflict
```

A fresh clone of this template already has all eight files. The pack exists for a
workspace that was set up before they landed, so nobody has to fork or merge by hand.

## What it installs, and which failure each part stops

AI writing fails in two ways, and most style guides only fix the first.

| File | Stops |
| :--- | :--- |
| [`answer_budget.md`](.agent/skills/no-ai-slop/answer_budget.md) | Explanation nobody asked for, reasoning shown to a reader who wanted the answer, process narration, closing recaps. Runs first. |
| `.agent/skills/no-ai-slop/SKILL.md` | Banned vocabulary, puffery, robotic rhythm, formatting slop, unexecuted action claims. |
| `.agent/skills/no-ai-slop/ste.md` | Long sentences, passive voice, idiom, synonym cycling. Simplified Technical English. Optional. |
| `.agent/skills/no-ai-slop/eval.md` | The self-check that runs after the edit. |
| `.agent/skills/no-emdash/SKILL.md` | The em-dash character. |
| `.claude/commands/no-ai-slop.md` | Nothing on its own. It is the manual entry point, `/no-ai-slop`. |
| `.claude/hooks/send_slop_guard.py` | The same rules, on outbound text, by machine. Em-dash blocks the send. Banned words, an over-budget length, and an unrequested rationale section warn. |
| `.claude/hooks/emdash_guard.py` | The em-dash character on its way into a repo file. |

Order matters. The budget decides what belongs in the draft, then the wordlists fix the
words that survive, then STE fixes the sentences. A tidy paragraph nobody asked for is
still slop.

## Two things the installer does not do

**It does not edit `.claude/settings.json`.** The hooks land on disk and do nothing until
you wire them. `postInstall` prints the two lines to add, for you to read and run.

**It does not edit your `CLAUDE.md`.** The rules run reliably only when they are standing
instructions rather than a command someone remembers to type, so copy the Quality Gates
section out of `CLAUDE.md.template` into your own file.

## Turning Simplified Technical English off

STE is the one opinionated part. It suits product and engineering writing and reads stiff
for a warm audience. To drop it, delete the STE subsection from the Quality Gates block and
delete `ste.md`. Everything else keeps working, and `eval.md` skips the check when the file
is not there.

## What no machine can check

A chat reply to you. Hooks fire on tool calls, and no hook sees assistant text on its way
to a human. Half of `answer_budget.md` runs on the rule alone, so the enforcement is you
saying it is too long.
