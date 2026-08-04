# Adopting the Open Knowledge Format (OKF)

**Source:** [GoogleCloudPlatform/knowledge-catalog/okf](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf), spec [SPEC.md](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md), v0.2. Announced by Google Cloud [12 June 2026](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing).

OKF is a vendor-neutral spec for representing knowledge as a directory of plain markdown files with YAML frontmatter. No SDK, no runtime, no compression scheme — a bundle lives in git, renders on GitHub, and is readable by humans, agents, or any static tool. Conformance is deliberately small: every non-reserved `.md` file needs parseable frontmatter with a non-empty `type` field, and that's it. Everything else — `generated`, `verified`, `status`, `stale_after`, `sources`, `Attested Computation` — is optional.

## The memory system is already conformant

This harness's persistent memory (`memory/*.md`, indexed by `memory/MEMORY.md`) already uses flat YAML frontmatter with a `type` field (`user`, `feedback`, `project`, `reference`). That is OKF's one required field, spelled the way the spec spells it — no migration needed.

Two optional OKF field families are worth layering on top, because they solve a real problem: a memory file has no way to distinguish "the agent inferred this" from "you explicitly confirmed this," and both get weighted identically on recall.

- **`generated: { by: <actor>, at: <ISO8601> }`** — records who/what produced the content and when. Stamped automatically whenever a memory file is written.
- **`verified: [{ by: "human:<you>", at: <ISO8601> }]`** — records actual confirmation. This should be stamped **only** when you explicitly confirm something in conversation, never speculatively. OKF derives a trust tier from its presence: no `verified` key → `unverified`; only non-human actors → `machine-confirmed`; at least one `human:` actor → `human-reviewed`. An unstamped memory then honestly reads as "believed, not confirmed" instead of looking identical to something you actually said.
- **`stale_after: YYYY-MM-DD`** is worth adding to project-type memories that carry a real horizon (a deadline, a launch date, a milestone). If a memory genuinely has no dated horizon, don't invent one — note that explicitly in the file body instead of leaving the gap silent.

## The idea worth adopting even without touching a single field

Buried in OKF's consumer requirements is one line that matters more than any frontmatter key:

> Consumers SHOULD surface, not silently drop, a failing attestation.

That is a precise name for a specific failure class: **a guard that cannot verify something must report "unverified," never "pass."** A boolean pass/fail collapses "I checked and it's fine" and "I couldn't check" into the same green checkmark, and the second one is a silent lie.

`meeting-recorder/mom_reconcile.py` is the concrete example. It exists to catch meetings that never got minutes — and it used to have two ways of quietly reporting "clean" when it wasn't:

1. **Coverage was enumerated from meeting-recording data alone.** If a meeting happened but nobody ever hit record, it produced zero rows for that meeting — indistinguishable from no meeting having happened at all. The script now enumerates the day a **second time from the calendar** and cross-references: any substantial calendar event with no matching recording lands in a new `uncounted` bucket, which trips the same non-zero exit as a missing or bad MOM. A calendar-fetch failure is logged and swallowed rather than blocking the primary (recording-based) check — it's additive, not a new single point of failure.
2. **A minutes file counted as covered purely by size.** A file above a byte threshold read as "done" even if it captured nothing. The script now also scans MOM content for actual decisions (a heading, or a ticket-style reference) and action items (a task-list checkbox, or a bulleted "Action Items" section). A file that clears the size check but contains neither is downgraded to suspect, with its own distinct reason string, independent of the size check.

Neither fix required a new framework. Both are the same move: replace an implicit "big enough / present enough" pass condition with an explicit check for the thing the guard actually exists to verify, and make "I couldn't verify this" a first-class, loudly-reported outcome rather than a fallthrough to success.

## What we didn't adopt

- **The OKF reference CLI/tooling** (enrich, visualize, the graph viewer) — built for data-catalog bundles like BigQuery tables and analytics exports, not the PM/knowledge-work shape this harness's memory and journal system already fits.
- **Full spec conformance as a goal.** There's no value in being "OKF-conformant" for its own sake — only in the specific optional fields and the verification discipline that solve problems this harness actually has.
- **Migrating structured state** (`journal/state/*.json` ledgers) to markdown. OKF has no real equivalent for lock-protected, cron-written, programmatically-queried state — that's a gap in OKF relative to a harness like this one, not a reason to move away from JSON there.
