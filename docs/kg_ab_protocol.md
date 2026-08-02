# A/B Protocol: knowledge graph versus grep

> Pre-registered 2 Aug 2026, BEFORE any arm was run. Criteria in section 5 are
> fixed. Changing them after seeing results invalidates the test.
>
> Context and Phase 1 build: [`graph_layer_proposal.md`](graph_layer_proposal.md)

## 1. What this test exists to settle

Phase 1 measured the graph against itself and it passed, which proves very
little: the tool's author wrote the exam. This protocol removes him from three
places at once. He does not write the questions, does not run either arm, and
does not know which arm is which when the numbers are computed.

The question is narrow and answerable: **for the retrieval half of a harvest,
does query-first find the same facts as grep-first for materially less
reading?**

## 2. Tasks, and why they are not rigged

Built by [`kg_ab_gold.py`](../.agent/scripts/kg_ab_gold.py) from evening updates
written **before the graph existed**, by a different process, for a different
purpose.

- One task = one paragraph of a past evening update.
- **Topic** = that paragraph's heading plus its opening two sentences, with
  every ledger id, Jira key, and markdown link stripped out.
- **Gold** = every ledger id and repo document path that paragraph cited.
- Tasks with fewer than 3 gold items are dropped as too thin to score.
- A task whose topic still contains any gold id, or a gold document's basename,
  is **discarded outright**. The exam must not contain its answer key.

Current set: **34 tasks, 202 gold items, median 4.5 gold per task, 0 leaks**
(leak audit is re-run before every session).

Honest limitation: some paragraphs are scorecards whose gold is a grab-bag of
ids no query could reasonably recover. Absolute recall will be low for both
arms. That is acceptable because the comparison is **paired**: both arms face
the identical task, and only the difference is interpreted.

## 3. The two arms

Both run as separate subagents with isolated context. Neither sees the other's
prompt, output, or existence. Neither sees the `gold` field, ever.

**Equal budget is the design.** Rather than trusting each arm to self-report
how much it read, both get the same hard ceiling of tool calls and we compare
what they recovered within it. Same cost, compare quality.

Shared rules, given to both:

```
You are answering a retrieval question about the owner's PM repo at
..

For each task you are given a TOPIC. Identify the source records that topic
refers to: ledger ids (COM-xxxx, WAIT-xxxx, DEC-xxxx), Jira keys, and
repo-relative paths to .md documents.

HARD RULES
- Maximum 15 tool calls for the whole task. Stop at 15 even if unfinished.
- Report only sources you actually saw in a tool result. Never infer an id
  from a pattern, never guess a number, never invent a path.
- Output ONLY this JSON, nothing else:
  {"<task-id>": ["COM-0123", "Clients/Work/.../Doc.md"], ...}
```

**Arm GREP** additionally gets:

```
You may use ONLY Grep, Glob, and Read. You may NOT run kg_query.py or read
journal/state/graph.json. This is the current way of working.
```

**Arm GRAPH** additionally gets:

```
You may use ONLY Bash running .agent/scripts/kg_query.py, plus Read to open
files it points you to. You may NOT use Grep or Glob.

  kg_query.py query "<question>"      find entry points and their neighbours
  kg_query.py explain "<id or name>"  one record and its connections
  kg_query.py path "<a>" "<b>"        how two things connect

Run kg_build.py first if the tool warns the graph is stale.
```

## 4. Scoring

[`kg_ab_score.py`](../.agent/scripts/kg_ab_score.py), mechanical, blind.

- Arms are supplied as `arm1` / `arm2`. The mapping lives in a separate key
  file which is opened only after every number is computed.
- `recall = |found ∩ gold| / |gold|` decides the outcome. A miss is a fact the
  evening update would have lost, so recall is not tradeable against cost.
- `precision` is reported but does not decide.
- Path matching compares basenames, so a harmless directory-prefix difference
  between arms is not counted as a miss.
- **Paired sign test** over the 34 tasks. With n this small, an eyeballed
  average is not evidence and a two-point gap is noise.

A judge subagent reads both arms' outputs unlabelled and writes a qualitative
read (what each arm missed, and whether the misses look systematic). The
numbers come from the scorer, not the judge.

## 5. Pre-registered criteria

| Outcome | Condition |
|---|---|
| **PASS** | graph recall >= grep recall, AND read-cost down >= 30%, AND sign test p < 0.05 |
| **FAIL** | graph loses any recall, at any cost saving |
| **FAIL** | the difference is not significant. Added complexity unpaid. |
| **PARTIAL** | recall equal and significant, cost saving between 10% and 30%. Keep the graph as an audit tool, drop the query-first claim. |

Since both arms are capped at the same 15 tool calls, "read-cost down" is
measured as the count of distinct files each arm actually opened, reported by
the arms and verifiable against their transcripts.

## 6. Threats to validity, stated in advance

- **Small n.** 34 paired tasks. The sign test is the guard; a near-miss p value
  gets reported as inconclusive, not spun as a trend.
- **The topics come from one author's writing style.** They read like the owner's
  evening updates because they are. That favours neither arm, but it does mean
  the result generalises to harvest, not to every question type.
- **Gold is what a past briefing happened to cite**, not everything that was
  actually relevant. An arm surfacing a genuinely relevant source that the old
  briefing missed is scored as a precision loss. This penalises the better arm,
  so it is conservative in the right direction.
- **Both arms are the same model.** Differences are attributable to tooling
  only if the two prompts are otherwise identical, which is why the shared
  block above is copied verbatim into both.

## 7. Run sequence

```bash
python3 .agent/scripts/kg_build.py --audit          # fresh graph
python3 .agent/scripts/kg_ab_gold.py --out journal/state/kg_ab_tasks.json
# spawn both arms with the prompts in section 3, collect arm1.json / arm2.json
python3 .agent/scripts/kg_ab_score.py --arm1 a1.json --arm2 a2.json
# read the numbers, THEN:
python3 .agent/scripts/kg_ab_score.py --arm1 a1.json --arm2 a2.json --key key.json
```

Result gets appended to [`graph_layer_proposal.md`](graph_layer_proposal.md)
whichever way it goes, including a FAIL.
