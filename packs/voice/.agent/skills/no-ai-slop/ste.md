# ASD-STE100 (Simplified Technical English) house rules

**STE applies to every communication by default.** Replies to the owner in chat, Slack
messages, emails, comments, chase notes, PRDs, MOMs, briefings, weekly reports, and repo
prose. There is no "internal writing is exempt" carve-out.

STE is a controlled-language spec written for aircraft maintenance manuals. Two parts of it
carry over cleanly and two do not, so the scope is stated up front rather than left to
guess.

**STE runs after [`answer_budget.md`](answer_budget.md).** STE makes a sentence readable. It never asks whether
the sentence belongs. Twenty short correct sentences still fail when two were the answer,
so cut first and shorten second.

## What is enforced

**Sentence mechanics**

1. **Procedural sentence: 20 words maximum. Descriptive sentence: 25 words maximum.** Count
   before shipping. A long sentence gets split, not compressed.
2. **One instruction per sentence.** Two actions means two sentences.
3. **Active voice.** Name the actor. Passive is allowed only when the actor is genuinely
   unknown.
4. **Present tense.** Use the past only for something that already happened, and the future
   only for a commitment that carries a date.
5. **Keep the articles.** "The build failed", never "Build failed". Telegraphic style is
   banned.
6. **No `-ing` form doing a verb's job.** "Using the new key, the app builds" becomes "The
   app builds with the new key." Gerunds survive only as ordinary nouns ("the meeting",
   "testing").
7. **Noun clusters: three words maximum.** "Seller portal payout reconciliation report"
   becomes "the reconciliation report for seller portal payouts".
8. **Paragraph: six sentences maximum.** Steps and conditions go in a vertical list, one
   item per line.
9. **Warning first, then the instruction.** State the risk or the blocker before the action
   it applies to.

**Word choice**

10. **One word, one meaning. One meaning, one word.** Pick the term and repeat it. Synonym
    cycling is already banned by the parent skill; this is the same rule with teeth.
11. **The simplest word that is still exact.** "Start" over "commence", "use" over
    "utilize", "before" over "prior to", "about" over "with regard to".
12. **A word keeps one part of speech per draft.** If "test" is the noun, the verb is
    "check" or "run the test". No "to test the test".
13. **No idiom, metaphor, slang, or figurative language.** "Bandwidth", "circle back",
    "moving the needle", "on the same page", "low-hanging fruit" all go.
14. **Define an abbreviation on first use,** then use it consistently. Never invent a short
    form.

## What is NOT enforced, and why

- **The ~900-word STE approved dictionary is not applied verbatim.** It has no vocabulary
  for product, commercial, or org work, so enforcing it would ban "sprint", "invoice",
  "roadmap", and every client term. Rules 10 to 14 above carry the dictionary's intent
  instead.
- **Domain and product nouns are always allowed:** ticket keys, tracker terms, client
  names, feature names, system names, and the owner's own vocabulary.
- **Quoting someone is exempt.** A verbatim quote keeps its original wording, including its
  idioms.

## Language scope

English drafts get everything above. On a non-English draft, apply rules 1 to 3, 8, 9, 10,
11, and 13 (sentence length, one instruction, active voice, paragraph length, warning
first, one term per thing, simplest word, no idiom). The article and gerund rules are
English grammar and do not transfer.

## Precedence

STE governs structure: sentence length, voice, tense, one term per thing, no idiom. The
parent skill's "preserve the owner's voice" governs tone and word choice inside those
limits. Where they collide, STE wins on the mechanics and the draft stays blunt rather than
becoming formal. A short, direct, unhedged sentence satisfies both.

Two things outrank this file. `answer_budget.md` runs first and decides what stays. The
workspace overrides in `SKILL.md` still win on the rest: no em-dash, no translation, no
unexecuted action claims, no sending without approval.

## Checks

1. Longest sentence at or under 25 words, and every instruction sentence at or under 20?
2. One instruction per sentence, active voice, present tense?
3. Articles present, no `-ing` form used as the main verb, no noun cluster over three
   words?
4. Every recurring thing named with the same word every time?
5. Simplest exact word chosen throughout?
6. Zero idioms, metaphors, and slang outside quotes?
7. Every paragraph at or under six sentences, with steps in a vertical list?
8. Warnings and blockers stated before the action they apply to?
