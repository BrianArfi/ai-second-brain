# Proposal: PSB Knowledge Graph Layer (in-house, deterministic)

> Status: **Phase 1 BUILT and running**, 2 Aug 2026. Sections 1 to 5 are the
> evaluation that led here; section 6 records what actually shipped.
> Trigger: evaluation of [Graphify-Labs/graphify](https://github.com/Graphify-Labs/graphify) (Apache-2.0, 100k stars).
> Verdict: do not adopt the tool. Reimplement the retrieval half in-house, feed it from our own typed data.

---

## 1. Why not adopt graphify as-is

Graphify is built for **codebases**. Its headline property, deterministic tree-sitter AST parsing at zero token cost, applies to code. PSB is 2.285 markdown files and 8 JSON ledgers. The half that would serve us is the half that costs money and accuracy.

Three findings from reading the source (55.210 LOC, cloned at commit of 1 Aug 2026):

**Finding 1. The markdown extractor is deterministic but shallow.**
`extractors/markdown.py` is 194 lines of regex. It emits: one node per file, one node per heading with `contains` nesting, and `references` edges from `[text](link)`, `[[wikilink]]`, and reference-style link definitions. Code fences are skipped. That is the entire deterministic yield for a markdown corpus.

We already have that. Our 847 internal markdown links would become 847 edges and nothing new would be learned.

**Finding 2. The semantic layer is not reproducible.**
Everything beyond headings and links comes from `llm.py`. Temperature is pinned to 0 for every backend (`llm.py:109-212`) but **no seed parameter is used anywhere**. Idempotence comes from the content-hash cache, not from the model. Invalidate the cache and the same file can yield a different graph.

For a corpus where a wrong edge becomes a wrong status claim to YourManager, that is disqualifying.

**Finding 3. Freshness does not apply to documents.**
`--watch` rebuilds immediately for code extensions. For markdown and docs it calls `_notify_only()`, which writes a `needs_update` flag file and prints "Run `/graphify --update`" (`watch.py:1567-1575, 1666-1667`). Document freshness is manual and token-priced. The "always-on, by the minute" promise is the paid platform, not this repo.

**Additional landmine.** Provenance defaults fail-open. An edge with no `confidence` key is treated as `EXTRACTED` by the report, analyze, export, and callflow paths (`report.py:93`, `analyze.py:218`, `export.py:303`, `callflow_html.py:210`). Absence of provenance is read as ground truth. Our harness rule is the exact inverse.

**Also:** the query stopword list covers English, German, French, Spanish, Portuguese, Italian. No Indonesian (`serve.py:215-244`). the owner's queries are Indonesian-English mixed.

---

## 2. Why in-house wins on both of the owner's criteria

the owner's constraints: **accuracy first, freshness second, source data changes by the minute.**

Our data has something a general-purpose tool cannot assume: **the relationships are already typed and carry stable IDs.** We do not need to infer them, and therefore we do not need an LLM, and therefore rebuild is free and instant.

Measured, right now:

| Source | Records | Typed relations already present |
|---|---|---|
| `commitments.json` | 383 | 383 `source.ref`, 251 `portfolio`, 89 `to_slug` |
| `waiting_on.json` | 193 | 193 `owner_slug`, 73 `escalate_to` |
| `decisions.json` | 67 | 67 `decider_slug`, 67 `sources`, 44 `stakeholder_slugs` |
| `people.json` | 35 people | 37 aliases, Slack IDs, person-page paths |
| markdown corpus | 2.285 files | 847 internal links |

That is roughly **2.000 edges that are 100% EXTRACTED**, derived from explicit fields, with zero inference and zero token cost.

Consequences:

- **Accuracy.** Every edge traces to a named JSON field or a literal markdown link. Nothing is guessed. `INFERRED` is reserved for exactly one edge type (see §3) and is visibly tagged.
- **Freshness.** Full rebuild is reading 8 JSON files and walking ~1.100 markdown files. Seconds, not minutes, and no API call. It can run on every ledger write, in the existing cron, or as a git post-commit hook. "By the minute" becomes real rather than aspirational.
- **Cost.** Zero.

---

## 3. Design

### Nodes

| Type | ID scheme | Source |
|---|---|---|
| `person` | `people.json` slug | `people.json` |
| `commitment` | `COM-xxxx` | `commitments.json` |
| `waiting` | `WAIT-xxxx` | `waiting_on.json` |
| `decision` | `DEC-xxxx` | `decisions.json` |
| `ticket` | Jira key | `tickets.json` |
| `initiative` | portfolio slug | `portfolio.json` |
| `meeting` | Fathom recording ID | `fathom_registry.json` + MOM header table |
| `document` | repo-relative path | markdown walk |
| `channel` | Slack channel ID | `slack_mention_ledger.json` |

### Edges

All `EXTRACTED` unless marked otherwise.

| Edge | From typed field |
|---|---|
| `commitment --owed_to--> person` | `to_slug` |
| `commitment --belongs_to--> initiative` | `portfolio`, `project` |
| `commitment --sourced_from--> meeting` | `source.ref` |
| `waiting --blocked_on--> person` | `owner_slug` |
| `waiting --escalates_to--> person` | `escalate_to` |
| `decision --decided_by--> person` | `decider_slug` |
| `decision --involves--> person` | `stakeholder_slugs` |
| `decision --sourced_from--> meeting` | `sources[].url` |
| `document --references--> document` | markdown link |
| `meeting --minuted_in--> document` | MOM header `Recording` row |
| `person --mentioned_in--> document` | **INFERRED**, alias match only |

The last row is the only inferred edge in the design, and it matches strictly against the explicit `aliases` array in `people.json`. No fuzzy matching, no name resolution by similarity. This is deliberate: `feedback_no_guessing_names` forbids resolving identity by resemblance.

### The MOM gap

80 MOM files, zero with YAML frontmatter. But they are not unstructured: every one carries a header table with `Date`, `Time`, `Duration`, `Facilitator`, `Subject`, `Recording`, plus a `**Participants**:` line. That table is regex-parseable today, no backfill required and no LLM.

Going forward, `/mom` should also write YAML frontmatter so parsing stops depending on table formatting.

---

## 4. What we take from graphify, and what we refuse

### Take (adapt, ~700 LOC of ideas, not vendored code)

| What | Where in their source | Why |
|---|---|---|
| Query pipeline | `serve.py:247-1056`, ~677 LOC | Tokenize, IDF, tiered scoring (exact 1000 / prefix 100 / substring 1, each × IDF), pick ≤3 seeds, BFS depth 2, render to a token budget. No LLM, no vectors. |
| Hub avoidance | `serve.py:872-900` | Refuse to traverse through nodes at or above the p99 degree. Stops every query landing on YourManager and Work. |
| Seed-per-term guarantee | `serve.py:627-716` | One seed forced per query term so a dominant term does not starve the others. |
| Ambiguity refusal | `cli.py:1333-1340` | `explain` exits 1 when candidates span multiple source files rather than picking one. This is our rule already. |
| Cache key design | `cache.py:324-404` | SHA256 of content plus relative-path salt. For `.md`, hash only the body below frontmatter so metadata edits do not invalidate. Stat fast-path on size + mtime before re-hashing. |
| Fail-closed eviction | `watch.py:534-596` | Drop a node only when its source file is actually gone from disk, never merely because it fell out of the current scan. |
| Deterministic clustering | `cluster.py:34-45, 67-77` | Sort nodes and edges before partitioning; networkx Louvain with `seed=42`. Run-to-run stable. |
| Confidence vocabulary | `validate.py:5` | `EXTRACTED` / `INFERRED` / `AMBIGUOUS`, **with the default inverted**: a missing tag is `AMBIGUOUS`, never `EXTRACTED`. |

### Refuse

| What | Why |
|---|---|
| `extract.py` (5.805 LOC AST) | Code only. Irrelevant to a document corpus. |
| `llm.py` semantic pass | Non-reproducible, token-priced, sends Work content to a third-party model. |
| Fuzzy dedup (`dedup.py`, 762 LOC) | MinHash plus Jaro-Winkler at threshold 92 merging entities by name similarity. Our entities have stable IDs and an explicit alias table. Merging "Teammate" into "Abdullah" by string distance is the precise failure mode our memory warns about. |
| Leiden via `graspologic` | Extra dependency. Louvain with a fixed seed is already deterministic and ships with networkx. |
| Git merge driver | Union-composes two graphs while loading but ignoring the merge base (`cli.py:2074-2079`). Our graph is a build artifact, not a tracked file. Regenerate, do not merge. |
| `graphify claude install --strict` | Blocks the first raw source read of a session and forces the graph. In a repo whose rules demand primary-source verification, that inverts the safety property. |

---

## 5. Hard boundaries

1. **The graph is a discovery index, never a citation.** It answers "which files should I open", never "what is the status". Any status claim still gets verified against the primary source in the same turn. This preserves `feedback_verify_state_at_report_time`.
2. **Ledgers stay the SSOT.** The graph is generated *from* them and is disposable. `commitments.json` and friends are never written by the graph builder.
3. **The build artifact is gitignored.** No third index to drift out of sync with Dashboard and master_links.
4. **Zero network calls at build time and at query time.** If a future feature needs an LLM, it ships as a separate opt-in command, not inside the builder.

---

## 6. Proposed build, phased

**Phase 1: DONE, 2 Aug 2026.**

Shipped: [`.agent/scripts/kg_build.py`](../.agent/scripts/kg_build.py) and [`.agent/scripts/kg_query.py`](../.agent/scripts/kg_query.py). Stdlib only, no new dependency.

Measured on the live repo:

| Metric | Result |
|---|---|
| Full rebuild | **0.14 s** |
| Nodes | 2.346 (1.282 document, 383 commitment, 193 waiting, 117 person, 115 ticket, 114 meeting, 71 initiative, 67 decision, 4 team) |
| Edges | 2.141 |
| Provenance | **1.979 EXTRACTED (92,4%)**, 153 INFERRED, 9 AMBIGUOUS |
| Network calls | 0 at build time and at query time |
| Files written | exactly 2, both gitignored. No ledger is opened for writing. |

Two things had to be retuned away from graphify's defaults, both found by testing against real data rather than by reading their README:

1. **Hub threshold.** Graphify floors it at `max(50, p99)`, tuned for large code graphs. This graph has a median degree of 1 and a max of 70, so a floor of 50 left `b2c` (49), `todo` (43) and `Your Name` (43) traversable, and one query returned 63 nodes of mostly noise. Floored at 12 instead, so the live p99 of 18 governs. The same query now returns 16 nodes, all relevant.
2. **Placeholder nodes.** An empty `portfolio` field became an initiative literally named `unknown` with degree 93, the single most connected node in the graph. Placeholders are now dropped at build time.

Added beyond the original spec, because the same class of bug kept appearing:

- **Split-identity warning on `explain`.** An exact match on a truncated person slug is unambiguous but incomplete. The command now says so and lists the sibling ids.
- **Initiative alias detection.** `portfolio` stores lowercase slugs and `project` stores display names, so one initiative lands as two nodes. 22 candidate pairs found. Reported, never auto-merged.
- **Page disambiguation.** A person and that person's own `People/` page share a label. Resolved through the `page` field in `people.json`, which is data rather than a guess. Genuine ambiguity still refuses: `explain "Work"` matches 809 nodes and returns nothing.

### Phase 1 evaluation, 2 Aug 2026

Run with `scratchpad/kg_eval.py`: provenance audit, retrieval recall, a grep
comparison, robustness cases, and coverage gaps.

**Provenance: clean.** 736 edges were re-derived from the source JSON
independently of the builder (`owed_to` 89, `blocked_on` 193, `decided_by` 67,
`involves` 110, `sourced_from` 204, `escalates_to` 73). **0 mismatches.** Only
one relation is ever labelled INFERRED, `attended_by`, as designed.

**Ground truth: 5 of 5.** Five questions whose answers are independently
verifiable put the correct node in the **seed set** every time, at depth 1.

**The grep comparison in the first report was wrong.** It used a grep that
matched ANY term, which is a strawman. Against a strict AND-grep over the same
terms, depth 2 returned **258 nodes vs grep's 166 files, a loss**. Depth 1
returns **104**, a genuine win, and keeps ground-truth accuracy at 5 of 5.
Depth 1 is now the default.

The honest read: the win is **ranking**, not volume. Grep hands back 30 to 45
files in arbitrary order. The graph puts the right item at the top and shows
what it connects to. Volume reduction is real but modest.

**Four defects found and fixed during evaluation:**

| Defect | Effect | Fix |
|---|---|---|
| Documents had no body text indexed | all 1.282 findable by filename only | headings and bold lead-ins as `search_text`, capped at 3.000 chars. Now 1.271 of 1.282 carry text. |
| Stopword-only and junk queries | `'x'` returned 48 nodes, `'yang di ke dari'` returned 56, all confident-looking | dropped graphify's never-return-nothing fallback. Junk now returns nothing and says why. |
| 1.029 isolated nodes | 795 documents and 115 tickets connected to nothing | `filed_under` path edges plus ticket `owner`/`project`/`initiative_id`. Now 132 isolated. |
| Hub threshold drifted | adding 103 folder nodes pushed p99 from 18 to 34, re-opening mid-degree nodes as through-routes | folders are containers: never traversable, and excluded from the p99 calculation. Sharing a folder is not a relationship. |

### Adversarial trap tests, 2 Aug 2026

The recall numbers above measure how good the tool is when it is right. These
measure how convincing it is when it is wrong, which matters more. Two of three
traps failed on first run.

**Trap 1, things that do not exist. FAILED, now fixed.** Every invented topic
returned confident results: `Bank Mandiri integration` returned PRD FINA docs,
`blockchain NFT loyalty pilot` returned the Q3 B2C Superapp Plan at score 801,
`Zalando marketplace onboarding` returned 115 nodes. Cause: the scorer answered
from whichever terms happened to match and ignored the one term that proved the
subject was absent. Fixed with an unmatched-term gate: a query naming something
with zero occurrences now returns `NOT FOUND: 'zalando' appears nowhere` and
refuses to answer from the remaining words. `--loose` overrides. All four
invented queries now refuse; all control queries still work; ground truth held
at 5 of 5.

**Trap 2, split identity. FAILED, now warned.** `Teammate` surfaced 3 of 7
person ids for the same human, `Your Name` 3 of 6, silently. The prefix rule
used by `explain` missed it, because `Teammate-yuda` and `Teammate-analytics`
are not prefixes of each other. The sibling rule is now: prefix match, OR same
first name token where not both ids are registered in `people.json`. Query and
explain both warn that a result may be partial.

The residual false positive is accepted and documented: `mohammad-ali` flags
`mohammad-Teammate`, who is a different human. No string heuristic can
separate that from `owner-arfi` versus `owner-arfi-you`. The tool warns and
refuses to decide; `kg_audit.md` section 2 is where it gets resolved once.

**Trap 3, staleness. PASSED as data, FAILED as behaviour, now fixed.** No ledger
was newer than the graph at test time, but nothing checked. Every command now
compares the graph's `generated_at` against the mtime of all six source ledgers
and prints a `[!] STALE` banner naming the files that moved.

**Known limits, unresolved:**

- 114 commitments remain isolated: no `to_slug`, no portfolio, no meeting.
- Non-Latin queries return nothing. Labels are Latin script.
- Self-retrieval recall of 90 of 90 is a weak test. It proves the scorer can
  find an item from its own words, not that it answers a real question well.
  Only the 5-question ground-truth set speaks to that, and 5 is a small n.
- **The Phase 2 kill criterion is still untested.** Nothing here proves the
  graph beats grep inside a real evening harvest.

**Phase 2 (~200 LOC).** Wire into the existing evening harvest. Measure against the real criterion: on the same evening update, does query-first beat grep-first on **files opened** and on **facts missed**. Accuracy is judged by rechecking every surfaced fact against the primary source.

**Phase 3, only if Phase 2 wins.** Rebuild hook on ledger write, MCP server so subagents query it as a tool, and an HTML view on the localhost dashboard.

Kill criterion: if Phase 2 does not reduce files opened without losing a single fact, stop. A worse index is worse than grep.

---

## 7. Licensing

Apache-2.0. Reimplementing the algorithms from reading the source is unrestricted. If any of their code is copied verbatim, the `NOTICE` and license header requirements attach. Recommendation: reimplement, and cite graphify as prior art in the script docstring.
