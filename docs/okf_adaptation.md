# Open Knowledge Format (OKF): what to steal for this harness

**Source, verified 4 Aug 2026:** [GoogleCloudPlatform/knowledge-catalog/okf](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf), spec [SPEC.md](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md) at **v0.2**. Announced by Google Cloud [12 June 2026](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing).

**Status of this doc:** research + proposal. Nothing below is implemented yet.

---

## What OKF actually is

A vendor-neutral spec for representing knowledge as a directory of plain markdown files with YAML frontmatter. No SDK, no runtime, no compression scheme. A bundle lives in git, renders on GitHub, and is readable by humans, agents, or any static tool.

Conformance for v0.2 is deliberately tiny. A bundle conforms if:

1. every non-reserved `.md` file has parseable YAML frontmatter,
2. every frontmatter block has a non-empty **`type`**, and
3. the reserved files `index.md` and `log.md`, if present, follow their defined structure.

`type` is the **only** required field. Everything else is optional, and the spec is explicit that consumers "MUST NOT reject a bundle because of missing optional frontmatter fields, unknown `type` values, unknown additional frontmatter keys, broken cross-links, or missing `index.md` files."

---

## Correction to my earlier read

I previously described our memory files as using `metadata.type` nested under a `metadata` key, and said we had "independently converged on a similar pattern."

That was wrong in a way that matters. The actual files use a **flat `type:`** at the top level:

```yaml
---
name: Operating Rules - PM & Content Partner
description: How Claude must operate as the owner's PM and content partner
type: feedback
---
```

That is not similar to OKF. That is OKF's one required field, spelled exactly the way the spec spells it. Checked all three files (`user_brian_profile.md`, `feedback_operating_rules.md`, `project_you_content.md`): every one has parseable frontmatter with a non-empty `type`.

**The memory bundle is already OKF v0.2 conformant today, with zero changes.** `MEMORY.md` is not named `index.md`, but the spec forbids rejecting a bundle for that. So this is not a migration project. It is a question of which optional field families are worth adopting.

(Note the CLAUDE.md memory instructions describe the nested `metadata.type` form. The files on disk use the flat form. Worth reconciling that separately, since it is a live inconsistency independent of OKF.)

---

## The one idea worth taking most seriously

Buried in OKF's consumer requirements is this line:

> Consumers SHOULD surface, not silently drop, a failing attestation.

That is a precise statement of the exact failure class this harness keeps hitting, and it is currently sitting in `journal/todo.md` twice as an open P0 and P1:

- **`ME-MOM-RECONCILE-CALENDAR`** (P0): `mom_reconcile` reported exit 0 for 30 Jul, a day where 7 substantial meetings happened and only 5 had notes. A weak MOM passed the suspect threshold because it was 2204 bytes, just over the 2KB gate.
- **`ME-MOMRECONCILE-UNDERCOUNT`** (P1): the same script reported "4/4 meetings minuted, 0 missing, exit 0" on a day with 7 minuted Work meetings. Coverage was fine that night, but the guard whose entire job is catching missing MOMs was looking at four of seven.

In both cases the attestation failed and the harness reported success. The OKF framing names why this is a category error rather than a bug: **a guard that cannot verify must report "unverified," never "pass."** That is the same reason OKF derives a trust tier (`unverified` → `machine-confirmed` → `human-reviewed`) instead of a boolean.

This is worth adopting as a harness principle regardless of whether we touch a single frontmatter field.

---

## What to steal, ranked

### 1. Trust tiers on memory files (`generated` / `verified`)

Right now a memory file cannot distinguish "Claude inferred this" from "the owner confirmed this out loud." Both look identical, so both get weighted identically on recall, which is exactly wrong for the `feedback` type where the whole point is that corrections carry more authority than inferences.

OKF's actor convention: `<producer>/<version>` for agents, `human:<id>` for people, `process:<id>` for automation. Trust classification keys off the `human:` prefix.

```yaml
---
name: Operating Rules - PM & Content Partner
description: How Claude must operate as the owner's PM and content partner
type: feedback
generated: { by: claude-opus-5, at: 2026-08-04T08:30:00Z }
verified:
  - { by: "human:you", at: 2026-08-04T08:30:00Z }
---
```

**The discipline this imposes is the actual value:** `verified` gets stamped only when the owner genuinely confirms, never on write. An unstamped memory then reads as "I believe this, he has not said so," which is honest and currently unrepresentable.

**Cost:** two lines per memory file. No tooling required, the tier is derived at read time.

### 2. `stale_after` on project-type memories

Our own memory instructions say project memories decay, and require converting relative dates to absolute. Nothing marks *when* a memory expires, so a stale project fact is indistinguishable from a live one until it causes a wrong recommendation.

```yaml
type: project
stale_after: 2026-09-30
```

Semantics are simply `today >= stale_after` means stale. This directly serves the standing instruction to verify that a memory's named files and flags still exist before recommending them.

**Best fit:** anything with a real horizon. Sprint targets (all MSP/MBA/STOR sprints target 13 Aug), the SAB Merchandise API review window closing 13 Aug, the Secondary transition.

### 3. Attested Computation for Work metric definitions

The highest-value steal for client work, and the one with a concrete target already in the working tree.

`type: Attested Computation` carries `runtime` (required, e.g. `bigquery`, `python`), plus optional `parameters` (typed named holes), `computation` (path to the code, or a `# Computation` fence in the body), `executor` (with a `receipt` list naming what a run must return, e.g. `[job_id, executed_sql, result]`), and `attester` (deterministic verification code that inspects receipts and returns a verdict).

The point is that a metric definition ships with the runnable query and a way to re-check it, so an agent cannot paraphrase "how commission is computed" from prose.

Where this bites for Work: commission and GMV figures currently travel as prose across weekly reports, the B2B dossier, and PRDs, and drift silently. `DEC-0003` is live evidence, the treatment of GMV versus commission was ambiguous enough that the ~$869.2K/month headline meant two different things depending on who read it. `Commission_Impact_Automatic_Supplier_Selection.md` is the natural first target.

**Caveat, stated plainly:** the Metabase cookie has been dead since 20 Jul (`ME-METABASE-COOKIE`), so an attester for anything Metabase-backed cannot actually run today. Build the format against a source that works, or accept that the first attestation returns `unverified`, which is at least the honest answer.

### 4. Bundle-relative links (`/path/...`)

OKF recommends links beginning with `/`, resolved from bundle root, because they survive file moves. Our relative links break constantly given how much churn there is in `journal/` (dated `reply_drafts_*`, `chase_queue_*` files daily) and how heavily `Dashboard.md`, `master_followup_tracker.md`, and the People pages cross-link.

Worth noting the spec also says consumers "MUST tolerate broken links," which is the right posture for our generated views.

### 5. `log.md` per client folder

Reverse-chronological update history, ISO dates as headings, newest first. `Clients/Work/log.md` would answer "what changed in Work this week" more cheaply than scanning `git log` or grepping dated filenames, and it composes with the existing `activity_log.py`.

---

## What not to steal

- **The reference CLI and agent tooling** (`enrich`, `visualize`, the Cytoscape graph viewer). Built for data-catalog bundles: BigQuery tables, GA4 exports, crypto datasets. Our knowledge is PM and client-relationship shaped, not schema shaped.
- **Conformance as a goal.** There is no value in being able to say we are OKF-conformant, and we already are anyway. The value is in the specific field families that solve problems we actually have.
- **Migrating the JSON ledgers.** `journal/state/{commitments,decisions,waiting_on,chase_queue}.json` are lock-protected, written by 7 cron jobs plus live sessions, and queried programmatically. OKF has no equivalent and markdown would be a downgrade. This is a gap in OKF relative to us, not the reverse.

---

## Suggested sequencing

| # | Change | Effort | Risk |
| :--- | :--- | :--- | :--- |
| 1 | Adopt "a guard that cannot verify reports unverified, never pass" as a harness rule; apply to `mom_reconcile` | Small rule, real code change | Low, fixes two open items |
| 2 | Add `generated` / `verified` to memory files, stamp `human:` only on real confirmation | Two lines per file | None |
| 3 | Add `stale_after` to project memories with real horizons | One line per file | None |
| 4 | Reconcile CLAUDE.md's `metadata.type` against the flat `type:` on disk | Doc edit | None |
| 5 | Draft one Attested Computation concept for a Work metric | Half a session | Blocked on a working data source |
| 6 | Bundle-relative link convention, then `log.md` per client | Ongoing convention | Low |

Items 1 through 4 are independent of each other and of item 5.

---

## Limits of this research

- Read the spec and README, not the reference implementation source. Claims about the CLI's shape come from the repo listing and the announcement, not from reading its code.
- v0.2 is current as of 4 Aug 2026. The format is two months old and the spec is explicitly versioned, so field families may move.
- I have not tested whether any of our existing tooling chokes on extra frontmatter keys. Worth checking `dashboard_updater` and anything that parses memory files before adding fields broadly.

Sources: [OKF spec v0.2](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md) · [okf directory](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf) · [Google Cloud announcement](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing)
