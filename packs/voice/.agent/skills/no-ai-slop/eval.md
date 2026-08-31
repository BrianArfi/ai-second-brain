# No AI Slop eval

Run this after the edit, before handing the draft to the `draft-reviewer` subagent. Answer
each check pass or fail. If any check fails, fix the draft and run the checks again.

For detect requests, confirm the response names each pattern with a quoted line and a short
fix, without rewriting the draft.

## Answer budget (run this section first)

1. Does the draft pass all seven checks at the end of [`answer_budget.md`](answer_budget.md)?
2. Is the answer in the first sentence, with no opener that restates the question?
3. Is the draft under the ceiling for its channel, or did the owner ask for depth?
4. Zero sentences describing your own process, tools, or search?
5. Zero closing summary that restates what the draft already said?
6. Does every table and bullet list hold parallel data, rather than one answer broken into
   pieces to look complete?

## Workspace overrides

1. Zero em-dash and en-dash characters in the output?
2. Is the draft still in the reader's language, with no translation introduced by the edit?
3. On a non-English draft, were the English wordlists skipped and only the patterns
   applied?
4. Does the output stop at the draft, with no send performed and no approval assumed?
5. Does every claim about a completed action correspond to something that actually ran?
6. Does the draft pass all eight checks at the end of `ste.md`?

## Editing principles

1. Does the edit preserve the point without adding claims, examples, stats, dates, ticket
   ids, or opinions that were not in the draft?
2. Does it preserve the writer's vocabulary, cadence, bluntness, humor, and uncertainty?
3. Does it leave strong human sentences alone instead of rewriting them for consistency?
4. Is the cutting proportional to the actual slop, with no compression that strips
   character?
5. Does the draft lead with what the reader needs?
6. Do sentences earn their place, with concrete facts, protected details, and direct verbs?
7. Does every generic sentence pass the portability test, or was it cut or made specific?
8. Is the draft in active voice with human subjects where possible?
9. Does it keep useful edge, so a direct chase still reads as direct?

## Words and patterns

1. Are banned words, filler phrases, empty adverbs, and chat-specific filler removed unless
   quoted as examples?
2. Are binary contrasts, negative listings, preference framing ("I would rather X than Y"),
   rhetorical setups, and throat-clearing openers removed?
3. Are faux-insight setups, colon reveals, superficial analysis, fake-strong verbs, synonym
   cycling, dramatic fragments, and robotic rhythm fixed?
4. Are importance puffery and weasel attribution replaced with plain facts and named
   sources, or flagged when no source exists?
5. Is interpretive metadiscourse removed?
6. Are fake-profound kicker lines deleted rather than rewritten into better metaphors?
7. Are summary-recap endings cut so the message ends on the ask or the next action?
8. Is formatting slop removed: emoji headings, decorative bold, bullets that should be
   prose, headers over tiny sections?

## Final read

1. Was the edit checked directly against this file, without spinning up a separate
   evaluator agent?
2. Does the draft avoid robotic symmetry and stacked punchy fragments?
3. Would the owner recognize the draft as their own voice?
4. Would it sound natural read aloud to the person it is addressed to?
5. Is the draft ready to hand to the `draft-reviewer` subagent as the next gate?
