# Changelog

All notable changes to the AI Second Brain harness. Newest first.

Entries are written in the private working repo and carried to the public template
(`ai-second-brain`) by the sync pipeline, which strips credentials, tokens, real client
names, and personal data. This file is copied verbatim, so it reads the same in both.

Dated headings below are the history. From v0.1.0 on, releases also carry a
version, and the rule is in [`docs/VERSIONING.md`](docs/VERSIONING.md).

## Unreleased

### Added
- **The `no-ai-slop` skill, which the template shipped a command for and never
  shipped.** `/no-ai-slop` pointed at `.agent/skills/no-ai-slop/SKILL.md`, and that
  folder did not exist here, so the command was dead in every clone. The skill is
  now in the template with its eval and its Simplified Technical English rules.
- **`answer_budget.md`, a new gate that runs before every wording rule.** The
  wordlists fix how a sentence reads. They cannot say whether the paragraph should
  exist, and the loudest AI tell is explanation nobody asked for: reasoning shown to
  a reader who wanted the answer, process narration, closing recaps. The budget
  answers first and stops, with a word ceiling per channel that triggers a cut
  rather than a justification. It covers replies to the owner as well as outbound
  drafts, because the owner is a named human too.
- **A real Quality Gates block in `CLAUDE.md.template`.** It was four checklist
  ticks, one of which asked for no em-dash and used one in the asking. It is now the
  four gates in order: budget, slop, STE, review, with what the machine checks and
  what it cannot. A command runs when somebody types it. A standing instruction runs
  every time.
- **`packs/voice`**, so a workspace that was set up before this can take the whole
  gate with `python3 tools/pack.py install packs/voice` instead of forking.
- **`tools/test_voice_pack.py`**, which fails when the pack copies drift from the
  workspace files they ship.

### Changed
- **`send_slop_guard.py` reads more than Slack.** It now covers
  `gmail_manager.py` (`--body`, `--body-file`) and `gdoc_comment.py` (the `text`
  fields inside `--items`), and it warns when outbound text goes over its channel
  budget or carries a rationale section the reader did not ask for. Quotes, code
  blocks and tables are excluded from the count, so a message that is mostly a
  quoted thread does not trip it. Em-dash still denies the send.
- **Every em-dash removed from `CLAUDE.md.template`.** Twelve of them, in the file
  that bans them.

## v0.1.0 - 2026-08-23

First tagged release of the public template. It has been usable for months; what
changed is that it is now legally usable, and that a machine checks it before it
ships.

### Added
- **Apache-2.0.** The repository was public with no LICENSE, which under
  copyright means all rights reserved. Nobody could legally fork it. The name
  and the artwork stay out of the grant, so a fork picks its own name.
- `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, and issue and pull
  request templates.
- `tools/repo_check.py`: one gate for personal data, script syntax, and skill
  frontmatter, with a self-test for its own rules. Findings that already exist
  are recorded in a baseline, so CI fails on new leaks only and the baseline can
  only shrink.
- CI that runs the gate and proves `install.sh` still produces a working
  workspace on Linux and macOS.

### Fixed
- The export scrub let a colleague's surname through inside an email local part,
  a client's product names, and six real Jira project keys. All three are now
  scrubbed at the source, in `.agent/skills/sync-public/sync.py`.
- `docs/UPDATING.md` and `/update-harness` both said `CLAUDE.md`, `journal/` and
  `Dashboard.md` were gitignored and could never conflict. None of them was.
  `CLAUDE.md` is now ignored, and the four tracked seed files are documented
  with the resolution to use. A fork used to hit a conflict on its first update
  while being told that was impossible.
- `/update-harness` finished conflict resolution with `git add -A`, which sweeps
  up whatever untracked personal files happen to be in the tree. It now stages
  only the files it resolved.

## 2026-08-14

### Breaking
- **Every ledger record must now name a work-tree node.** `--node <id>` is
  required on `add` in all four ledger CLIs (commitments, decisions,
  waiting-on, chase queue), and an id that does not exist in the work tree is
  rejected rather than stored, with the closest matching nodes printed. Any
  script, cron job, or habit that calls `add` without a node will fail until
  it passes one. Unattended callers that genuinely cannot know the node pass
  `--node unfiled --node-why "<reason>"`, which files the record into a triage
  queue instead of silently inventing a home for it. A live session may not
  use `unfiled`: when the right node is not obvious it asks the human, once,
  with candidates, at the end of the turn.

  The reason for the hard failure is that the alternative is worse. Before
  this, linkage lived on the tree as a hand-curated list of references, so a
  record could be created with no home and nothing noticed: of 729 records,
  61 were reachable from the tree. The other 668 were only findable by reading
  them one at a time.

### Added
- **The work tree became a schema instead of a report.** It began as a
  per-window reporting artifact, hand-maintained for the dashboard, which was
  safe while nothing pointed at it. Once every record names a node, those ids
  are permanent identifiers, and a node renamed during a weekly refresh would
  orphan everything under it. A new `.agent/scripts/work_tree.py` owns the
  invariants that make the ids safe to depend on: ids unique across the whole
  tree, retirement by an `archived` status rather than deletion or rename,
  writes taken under the same lock as the ledgers, and `find` / `show` /
  `add-node` / `alias` / `validate` / `coverage` commands. Ledger CLIs import
  its resolver directly, so the rule is enforced in one place.
- **A backfill for records that predate the rule.** `work_tree_backfill.py`
  maps existing records using four signals in order of strength: an id already
  referenced by a node, a ticket key that sits under exactly one node, a
  curated phrase table for the words people actually write, and the source
  document a record came from. That last one carries most of the weight, since
  the largest group of unmapped records were action items extracted from
  meeting minutes ("send the invitation for the UAT"), where the topic lives
  in the document rather than the line, and their confidently-filed siblings
  from the same document supply the missing context.

  Where two candidate nodes tie, it files the record under their nearest
  common ancestor rather than picking one, because a coarse node that is true
  beats a precise one that is wrong. Where there is no common ancestor, the
  record goes to a triage file with its candidates instead of a guess. A
  record filed under the wrong node reports as tracked and never surfaces
  again, which is the exact failure the rule exists to prevent.
- **A second id space is bridged rather than retired.** The portfolio view
  keeps its own initiative ids and its own tab; `work_tree_alias.json` maps
  those ids to nodes, alongside the phrase table. Two id spaces drift by
  default, so both halves are checked by `work_tree.py validate`.
- **Records show their home.** The deep-linked card for any tracker id now
  carries a "Work tree" line with the node's full path. `unfiled` prints as a
  real state rather than an empty field, because it means the record is
  waiting on a human to say where it belongs. The morning briefing surfaces
  the count of open unfiled records until they are filed.

## 2026-08-08

### Breaking
- **Drive writes no longer publish documents to anyone-with-link.** Every
  writer used to attach a public link-sharing permission unconditionally,
  regardless of the `--share` flag, so a limited-circulation document was
  public from the moment it was created. Writers now call a shared
  `apply_visibility()` in `.agent/scripts/file_utils.py`, which defaults to
  the account's own domain for work accounts and to private for personal
  ones, and which **revokes** any pre-existing anyone-with-link permission on
  every write rather than merely declining to add one. A document made public
  by an earlier upload therefore becomes private again on its next update.
  Pass `--visibility public` (or `--share`) to publish on purpose. Any script
  or habit that relied on a fresh document being world-readable by default
  needs that flag added.

### Added
- **Concurrency locking for the JSON state ledgers.** The commitment,
  decision, waiting-on, and chase-queue ledgers are written by several cron
  jobs plus live sessions. Atomic replacement prevented corrupt files but not
  lost updates: two overlapping read-modify-write cycles silently dropped
  whichever record landed first, which is how a handful of records
  disappeared without any error being raised. A new
  `.agent/scripts/ledger_lock.py` gives every writer an exclusive lock held
  for the whole read-modify-write cycle; read-only commands skip it so long
  reports cannot block writers. A reproduction test ships alongside it:
  without the lock 3 of 3 concurrent records are dropped, with it 3 of 3
  survive.
- **A publishing target for HTML deliverables.** Interactive artifacts now
  deploy to Cloudflare Pages, which serves the exact bytes and preserves CSS
  custom properties inside inline SVG. The previous Apps Script host rewrote
  and branded every page, which broke variable resolution in diagrams and
  made them unreadable in the dark palette. Republishing under the same
  artifact name replaces the content and keeps the URL, so a link already
  sent to someone keeps working.
- **Background CPU throttling for the cron fleet.** Cron jobs clustered on
  round minutes, each spawning its own interpreter, could saturate every core
  and leave the editor and interactive sessions with no scheduler time. Two
  reversible levers ship: a throttle that reschedules `*/N` jobs across
  deterministic offsets and drops the cron shell to low CPU and IO priority,
  and an optional systemd slice that hard-caps background CPU so interactive
  work always wins.
- **An idle-session reaper.** Long-lived editor AI sessions accumulate and
  hold hundreds of megabytes each. The reaper measures CPU time across
  several samples so it never kills a session mid-turn, and it defaults to a
  dry run; killed sessions are restorable in full from their transcript.
- **A repair command for lost executable bits.** Editing a shell script over
  a network mount from another OS drops its `+x` bit, which surfaces much
  later as an opaque permission error from cron. The repair script diffs the
  filesystem against the git index and restores the difference. The
  session-start check now also pins the file-mode setting per machine, so a
  checkout shared between two operating systems stops inventing permission
  changes for every tracked executable.

### Fixed
- **A truncated Slack API response no longer discards an entire harvest.**
  The user-name lookup is cosmetic, but an incomplete read from it raised
  past the error handling and killed the whole channel listing, throwing away
  hundreds of conversations that had already been fetched successfully. Three
  layers now hold: transient transport errors retry with backoff and report
  failure rather than exiting, the name map seeds from its cache and only
  rewrites that cache on success, and the call site tolerates a failed
  lookup. The Slack sender also warns when a draft contains text a shell
  would mangle, after a dollar figure was silently rewritten in a message.
- **The share gate now reads the published document, not the local source.**
  It was scanning the local markdown for unresolved image placeholders, but
  that file legitimately keeps its placeholder tokens as an image manifest,
  so the gate reported blocked forever once the images had actually been
  inserted into the published document. It now checks the document itself.
- **Local meeting capture no longer depends on the browser.** Audio is
  recorded from the system audio server outside the browser, into a
  dedicated null sink created before the browser launches. Recording the
  bridge sink instead could wedge the whole audio server and fail every later
  session. A stalled reader is now reported as a failed call rather than
  passing silently, a synthetic test tone is refused instead of being
  transcribed as if it were the meeting, and speaker names are read from a
  DOM label that still exists after the meeting UI dropped its participant
  attributes, which had been attributing every line to an unknown speaker.

### Removed
- **The knowledge-graph retrieval layer.** Four rounds of A/B testing against
  plain search never cleared the cost bar that was agreed before the
  experiment started. Recall trended in its favour but not consistently
  enough to justify a second index maintained on top of the existing ledgers
  and dashboard.

## 2026-08-04

### Added
- **`mom_reconcile.py` now cross-checks against Google Calendar and scans MOM
  content, not just size.** Coverage used to be enumerated from meeting
  recordings alone, so a meeting nobody hit record on produced zero rows and
  was indistinguishable from no meeting having happened. A second enumeration
  pass now runs against the calendar connector; any substantial calendar
  event matching no recording lands in a new `uncounted` bucket that trips
  the same non-zero exit as a missing or bad MOM (calendar-fetch failures are
  logged and swallowed rather than blocking the primary check). Separately,
  a minutes file used to count as covered purely by clearing a byte-size
  threshold, which let a hollow draft with zero decisions pass. `inspect_mom`
  now also scans content for decisions (a heading, or a ticket-style
  reference) and action items (a checkbox, or a bulleted section), and
  downgrades to suspect when both are absent, independent of size.
- **`docs/okf_adaptation.md`**: notes on adopting Google Cloud's Open
  Knowledge Format (v0.2). The harness's memory system already conforms to
  its one required field; the doc covers the optional `generated`/`verified`
  provenance fields worth layering on top, and states the principle behind
  the `mom_reconcile.py` fix above: a guard that cannot verify something must
  report "unverified," never "pass."

## 2026-07-18

### Breaking
- **`slack_client.py --action post/upload/invite` now refuses to send without
  explicit approval.** Both the primary (`slack-connector`) and secondary
  (`secondary-slack-connector`) connectors import a shared
  `require_send_approval()` from `.agent/scripts/file_utils.py` and call it
  before any network request for a send action. Pass `--approved` once the owner
  has signed off on the specific draft. There is deliberately no environment
  variable that unattended cron/automation can set to bypass it (an env flag
  is process-wide and permanent, so exporting it once would silently un-gate
  every later send in that process tree); a genuinely unattended caller must
  pass `--approved` explicitly at its own call site. Any existing script,
  cron entry, or operator habit that called `post`/`upload`/`invite` without
  `--approved` now exits nonzero instead of sending. Update those call sites
  before this ships.

### Changed
- **Drive write verification moved out of the `drive_verify.sh` PostToolUse
  hook and into the writer scripts themselves.** The hook matched the raw
  Bash command string for a writer script name plus `create-doc`/`upload`/
  `update`, which meant any command whose text merely *mentioned* a writer
  (a `grep` over this repo, a `cat` of documentation) could trip the same
  block as a real invocation. `drive_verify.sh` is now a documented no-op
  that stays registered only so the next person finds the explanation.
  Enforcement lives in a new `assert_drive_result()` helper in
  `.agent/scripts/file_utils.py`, called by all five Drive writers
  (`gdocs_create.py`, `gdoc_surgical.py`, `gdocs_writer.py`,
  `gdoc_comment.py`, `patch_doc_links.py`) right before they report success.
  It checks the actual API response for a file/document id and exits
  nonzero with a clear stderr message when one is missing, a stronger
  guarantee than the hook ever gave since it covers all five writers instead
  of two and reads the real response instead of guessing from text.

### Added
- **Runtime detection**: `.agent/scripts/detect_runtime.sh` (sibling of the
  existing `detect_platform.sh`) plus a `.agent/scripts/harness_config.py`
  consumer that caches the result in a gitignored `.agent/harness.json`. The
  point: the harness now branches on capability (can it spawn subagents,
  does it have hooks, what model tier) instead of on vendor ("am I Claude"),
  so it degrades correctly on a runtime that is not Claude Code.
- **`.agent/scripts/ai_call.py`**, a backend-agnostic runner that replaces
  three hardcoded `claude` binary call sites (`command_queue.py`,
  `dashboard/server.py`, `evals/run_behavioral.py`) with one resolver: run
  Claude if present, fall back to `agy-bridge` if not, and only fail if
  neither is available, so automation no longer hard-fails on a machine
  without Claude Code installed.
- **`work-hours` now reads Antigravity conversation history** (from the
  local SQLite conversation store) in addition to Claude Code transcripts,
  so the Hours tab produces real numbers on a non-Claude runtime instead of
  going empty.
- **Claude-only mode is now first-class.** The optional model bridge (`agy-bridge`)
  detects instantly when no non-Claude backend is configured and emits its standard
  Claude-fallback signal, so the whole harness runs on Claude alone at full capability.
  `run.py --doctor` explains your mode and what each optional backend needs; the
  `/setup` wizard now asks which subscriptions you have (including "none") and skips
  token setup accordingly. New optional backend: Kimi Code (Anthropic-compatible).
- **Token-efficiency loop** (`.agent/scripts/token_efficiency.py` +
  `.agent/protocols/token_efficiency.md`): weekly self-audit of tokens, cost, and
  offload share per task type from real usage logs; a `log-change` ledger records every
  optimization so the next report shows each change next to its observed effect. New
  dashboard panel (`/api/token-efficiency`) renders the trend, top hotspots, and the
  what-changed log; weekly planning picks at most one hotspot to optimize.
- **PRD publish chain**: `scripts/publish_prd.sh` (gate, convert, embed, format,
  share, restrict, verify, update-in-place, hard-fails loudly at every step) and
  `scripts/readability_gate.py` (wall-of-text lint before publish, post-publish verify,
  `--allow-public` for intentionally public docs).
- **Meeting-coverage tripwire** (`meeting-recorder/mom_reconcile.py`): enumerates the
  day from the live recorder API, never a stale local registry, tri-state exit
  (covered / gaps / cannot-verify), grace window for just-ended meetings, skips
  recordings that belong to other workspaces, designed to run on its own cron.
- **Inbox hub** (dashboard): conversations with context-aware reply drafts and an
  approve-before-send flow; drafts can never send themselves. Command-queue workers are
  draft-only by tool policy, not just by prompt.
- **GA4 connector** (`.agent/skills/ga4-connector/`): read-only Google Analytics 4 CLI
  for AI agents. Actions: `snapshot` (KPIs, % deltas vs previous period, top
  pages/sources/events/countries/devices, daily trend, new-vs-returning, one call),
  `report` (custom dimensions/metrics/filters), `realtime`, `top` presets, `meta`
  (dimension/metric discovery incl. custom definitions), `accounts`, `property`.
  Two-step headless OAuth helper reuses the shared work Google OAuth client
  (`analytics.readonly` scope); token auto-refreshes, cron-safe. Set your property id
  in `config.example.json` to `config.json` or via `set-default`. Design adapted from
  the official `googleanalytics/google-analytics-mcp` tool surface and
  `Bin-Huang/google-analytics-cli` (CLI-first JSON output). SKILL.md includes an
  analysis SOP: snapshot first, drill anomalies with segmented reports, weight by
  revenue/conversion over raw traffic, every insight ends in an owned action item.
  Prereq: enable Google Analytics Data API and Admin API on the Cloud project that
  owns your OAuth client.

### Security
- **Dashboard access control**: per-request client-IP allowlist (loopback, auto-detected
  WSL gateway, `DASHBOARD_ALLOWED_IPS`) enforced for every route, including the
  send-capable endpoints. 403 for everything else.
- **Headless AI runs de-fanged**: background inbox/digest/enrichment tasks now get
  narrowly scoped tool allowlists (no unscoped shell) since their prompts embed
  untrusted inbound message content; sends stay approval-gated at the server.
- **Scrub pipeline hardened**: runtime prompt snapshots (`*_prompt.txt`, which can carry
  real message text) and retired code are now blocklisted from ever syncing here.

### Fixed
- **The public mirror shipped `tab-hours.js` and `tab-inbox.js` while
  omitting the `work-hours`, `inbox-hub`, and `command-queue` skills that
  feed them**, so a fresh public clone rendered two permanently empty tabs.
  The `sync-public` manifest now carries those skills and warns when a
  dashboard tab ships without its data-producing skill behind it.
- **Four rules in GEMINI.md contradicted CLAUDE.md.** A merged "PM +
  Content Partner" domain scope and a duplicate "Content Partner Rules"
  section both had the Antigravity runtime doing personal-brand work inside
  this client repo instead of redirecting to the You repo; a missing
  send-transport rule let a send silently fall back to the bot token and
  post as the bot instead of as the owner; and a "Slack-to-ClickUp" protocol
  reference proposed ClickUp, a task tracker CLAUDE.md says is not used for
  Work or Secondary. GEMINI.md is now realigned with CLAUDE.md on all four.
- Premeeting-card enrichment always goes through the dedicated bridge script (the
  headless cron path had silently grown a divergent inline re-implementation that could
  overwrite the card audit trail).
- Meeting-note sync no longer aborts the whole batch when one note targets a path
  outside the repo; per-note failures are isolated and reported.
- Commitment-ledger duplicate adjudication (`dedupe`) now runs on cron; cron log lines
  carry timestamps; duplicate-assignment cleanup.
- Weekly-report/PRD registration exits nonzero when the local markdown update fails.
- Various small honesty fixes: publish verify no longer fails intentionally-public docs,
  mermaid-embed failures are no longer masked, stale cron times corrected in SOPs, the
  Claude binary fallback fails loudly instead of silently using a broken wrapper.

## 2026-07-16

### Added
- **Hours tab + `work-hours` skill**: reconstructs the owner's actual working hours per day
  from digital traces alone, no manual timesheet. Sources: Claude Code session
  transcripts (interactive streams vs. automated cron runs kept separate), meetings from
  the Fathom registry merged with Google Calendar, and git commits as corroborating
  markers. A workday runs 04:00 WIB to 04:00 WIB the next day so late-night work counts
  to the day it started on.
- **Leverage, the productivity multiplier.** Three numbers per day, kept honestly
  distinct: `actual_h` is the union of all blocks, the real hours worked; `effective_h`
  sums every parallel stream, so running three agents at once counts three times;
  `leverage` is `effective_h / actual_h`. The dashboard's "Methodology & sources" card
  labels `actual_h` and `effective_h` as measured from real session data, and the
  AI-speed multiplier used to estimate manual-equivalent hours as an assumed constant,
  overridable via `--ai-speed` or `WORK_HOURS_AI_SPEED`, so nobody mistakes an estimate
  for a measurement.
- State lives in `journal/state/work_hours.json` (served at `/api/work-hours`) with an
  incremental per-file parse cache; CLI is `work_hours.py sweep [--backfill N]` and
  `work_hours.py show`.

## 2026-07-13

### Added
- **Command-queue draft-only dispatch + dashboard approval surface.** The `command-queue`
  skill scans ticket-comments addressed to the owner, routes each to a model and effort tier
  by the CLAUDE.md subagent table, and dispatches a detached headless `claude -p` worker
  per comment. Workers are locked to `read` plus `Write` under `journal/ai_drafts/**`
  only, no `Edit`, `Bash`, ticket mutation, send, or Jira/Drive writes, so an unattended
  worker cannot touch client state. A finished worker lands in a `review` state; the
  dashboard's "Commands awaiting your approval" card on Today opens each draft in a
  drawer, and an ack endpoint clears it once the owner has looked at it.
- Doc links inside dashboard cards now render as drawer openers for relative-path
  markdown, so a BRD or PRD referenced from a card reads in place instead of needing a
  separate open.
- Fixed a headless-auth trap in WSL: `which claude` was resolving to the Windows binary
  on `/mnt/c`, which reads Windows-side config and reports `loggedIn:false`. Both
  command-queue and the dashboard AI-task spawner now prefer the npm-global WSL install.

## 2026-07-12

### Added
- **PM ledger suite + trackers** (completeness pass): `commitment-ledger` (things you owe
  others), `decision-log`, `waiting-watchdog` (things others owe you), `outcomes-loop`,
  `premeeting-cards`, `reply-queue`, `token-tracker` (usage + cost), `harness-health`
  (cron-job truthfulness checks), and `slack-tracker` (stateful mention ledger). These are
  the state machines the dashboard visualizes; their data stays local under `journal/state/`.
- **More skills**: `fathom-frame-grab`, `gemini-image`, `google-ads-connector`,
  `proactive-assistant`, `interview-assistant` (hiring toolkit: CV parser, interview plan
  and assessment templates), and `work-link-sync`.
- **Document templates** (`templates/`): meeting-minutes and PRD skeletons used by the
  meeting recorder and PRD pipeline.
- **Integration wizard** (`setup/connect.py` + `integrations.json`): interactive CLI that
  wires MCP servers into your Claude Code settings from a catalog.
- **Curated helper scripts** (`scripts/`): registry sync, Google Docs image/table helpers,
  collaborator sharing, audio transcription, weekly-report tabs, doc indexer, maintenance.
- **Meeting recorder** (`meeting-recorder/`): record and transcribe meetings locally on
  your own machine, with an automatic minutes draft. Cross-platform capture (macOS
  avfoundation, Windows WASAPI, Linux PulseAudio), local GPU transcription via whisper.cpp
  with a Gemini API fallback, and an optional advanced Vexa auto-join bot. A private
  alternative or complement to a cloud recorder. Guide: `docs/MEETING_RECORDER.md`.
  Ships with `config.example.json`; runtime state and API keys stay local.
- **Visual dashboard** (`dashboard/`): a local, stdlib-only web cockpit at
  `http://localhost:3737` over your notes, calendar, projects, to-do tracker, meeting
  health, routines, and token usage. Start with `python3 dashboard/server.py`. Guide:
  `docs/DASHBOARD.md`. Panels fill in as you use the brain; a fresh clone shows an empty
  shell by design.
- **`/setup` guided onboarding command.** Type `/setup` after cloning and the AI interviews
  you about who you are, your work contexts, your track record, and your rules, then requests
  access to your tools and assembles your `CLAUDE.md` for you. Phase-based, resumable
  (`/setup resume`), and it never asks you to paste a secret into the chat. It drives the
  mechanical steps in `docs/SETUP.md` rather than duplicating them.
- **Indonesian connection kit** in `docs/workshop/`: `MULAI_DARI_SINI.md` (start here),
  `PANDUAN_KONEKSI.md` (step-by-step tool connection guide), matching PDFs, and illustrated
  screenshots (`img/`) for the Google, Slack, and Jira setup flows. Token values in every
  illustration are masked; no real credentials are shown.

### Fixed
- `daily_update_runner.py` shipped with a syntax error (an over-eager scrub step cut a
  generated-markdown f-string in half). The scrub is now markdown-scoped and every published
  Python/JS file is syntax-checked.
- `token-tracker`: flattened the API payload, made the display table token-first, and
  hardened task-type classification.

### Changed
- Harness refresh synced from the working repo: morning/evening update workflows, the MOM and
  weekly-planning commands, the daily-update quality rubric, and the Google Calendar, Drive,
  and make-pdf connectors.
- Workshop deck (`docs/workshop/2026-07-11/`) expanded with the full capability showcase and
  talk track.

## 2026-07-11

### Added
- **Visual dashboard redesign, v1 through v4.** Four same-day passes that took the
  dashboard from a 13-tab prototype to a 4-tab glanceable cockpit:
  - **v1**: full frontend rewrite into Today / Work / Meetings / System with hash
    routing, progressive disclosure, a small component library, a validated dark
    palette, staleness dimming, and expansion state that survives a reload. Server
    grew a shared `/api/overview` payload. Dead data sources (`insights.json`, a
    58 KB `Dashboard.md` on a 60-second poll loop) stopped being rendered as if live.
  - **v2**: Portfolio to Initiative to Task to Subtask drill-down with breadcrumbs, an
    internal-first task model (`initiative_id` plus `jira_key` plus `parent_id`), Jira
    chips that link straight to `yourcompany`/`examplevendor` by prefix, one-click
    "Chase" from a blocker into `waiting-watchdog`, cost-savings visibility, and
    meeting action items surfaced directly on their cards.
  - **v3**: an always-on visual identity (spectrum hairline, brand glow, tile shimmer,
    alarm red reserved for real alarms), the Portfolio drill became a slide-over that
    preserves context, auto-detected reference chips for tasks/decisions/commitments,
    real charts (priority distribution, project donut, activity sparkline, meetings per
    week), and a Harness Map that replaced a plain inventory list.
  - **v4**: an AI task runner reachable from the dashboard itself. `/api/ai-task` spawns
    a detached headless `claude` run per commitment ("AI kerjain"), per failing or
    reauth job ("AI solve"), or across the whole commitments ledger ("AI verifikasi
    semua"); every run only ever drafts to `journal/ai_drafts/`, never sends, and status
    pills poll through to a result link that survives a page reload.
- **Token usage tracker tab.** The `token-tracker` skill incrementally parses Claude
  Code transcripts (roughly 1000 files across 500+ MB in about 1 second full, tens of
  milliseconds incremental), dedupes by `message.id` since naive row-summing overcounts
  by 2 to 3x, classifies by task type (command, subagent, workflow-agent, `ai-<kind>`,
  interactive), and prices from the bundled `claude-api` skill reference. Surfaced on the
  System tab as average tokens and cost per task type.
- **Cron repair alongside the redesign**: `maintenance.sh` execute bit and `/bin/bash`
  invocation fixed, moved to 13:00 WIB with a heartbeat health ratio; premeeting,
  outcomes, harness-health, and commitments crons rescheduled inside the 12:30 to 22:00
  WIB machine-on window; Vexa empty-transcript runs reclassified as
  `skipped_not_admitted` with an hourly idle heartbeat, which killed a class of false
  silent-cron alerts.
- **8 new PM components** (ledgers, watchdog, stakeholder pages, cards, health checks)
  landed the same day as the redesign, feeding the new dashboard tabs directly.

## 2026-07-09

### Added
- **Vexa bot health strip** on the Meetings tab: `/api/vexa-health` live-probes the
  container, the API on port 8056, the storage backend, and whisper on port 8083, plus
  last cron and last meeting, cached 15 seconds. An auto-refreshing ONLINE / DEGRADED /
  DOWN strip surfaces the storage-backend failure mode that used to produce silently
  empty transcripts.
- A MinIO chip was added to the same strip once Vexa moved to a real MinIO object store
  for persisting browser-session userdata across redeploys; MinIO health now factors
  into the overall verdict.

## 2026-07-07

### Added
- Daily-use showcase and the one-recording content pipeline in the README.

### Changed
- README polish: header, badges, learning section, and capability catalog.
- Workshop deck expanded to a full capability showcase with real pricing math.

## 2026-07-06

### Added
- **Public template v2.** Fresh history, deep-scrub sync pipeline, easy install path
  (`install.sh`), and the first Indonesian workshop kit.
- Conversational-brain quick start: a smart local companion in 15 minutes with no API keys or
  OAuth, then connect real tools when you are ready.
- Connector skills for Google Workspace (Drive, Docs, Calendar, Gmail), Slack, Fathom, Figma,
  Mixpanel, Metabase, Jira, and ClickUp, plus the multi-agent harness (commands, agents, hooks).

## 2026-02-22 to 2026-07-05 (backfilled)

The dashboard and the multi-model cost layer both trace back further than the public
template's fresh history. Grouped by theme rather than by commit:

### The dashboard's origin
- **2026-02-22**: the dashboard was born as a local web app alongside the daily
  briefing, with a calendar display and a project file browser behind a content modal.
- **2026-04-13**: Google Drive file retrieval wired into the dashboard sync path, plus a
  round of UI updates.
- **2026-04-30**: seller-portal upload tracking surfaced as status and progress
  indicators in the UI.
- **2026-07-05**: Portfolio tab Phase 2 rollout. Four Work teams live with
  workflow-verified data (Marketplace, Platform, and B2C initiatives seeded from Jira
  epics plus harvest, 25 initiatives across Ecomsol and Sharaf DG), 43 tickets tagged
  with their initiative, 27 corrections applied from a 4-agent adversarial verify pass,
  and the legacy "Select Data" dropdown removed now that the tab reads live data by
  default.

### Multi-model cost telemetry and the agy-bridge
- **2026-06-24**: `agy-bridge` introduced as a non-Claude co-processor. Harvest, critic,
  and research work can route to GLM 5.2 or to Gemini/GPT-OSS through the `agy` CLI,
  each call ending in a Claude fallback so quality never silently drops. The main
  session always stays on Claude. Shipped with real cost telemetry from the start: per
  call tokens, latency, and per-Mtok cost for every model including subscription
  backends, a Claude counterfactual for comparison, a capability matrix, time-of-day
  routing tuned to the verified GLM peak window, and a "Cost / Savings" dashboard tab
  backed by `/api/agy-cost`.
- **2026-06-25**: `agy-bridge` gained a `draft` capability (GLM 5.2 with a Sonnet
  fallback), a Router doctrine for the main loop, and heartbeat observability writing
  to `dashboard-data/agent_heartbeat.jsonl` and a new Routines dashboard tab.

### Dashboard hardening and live data
- **2026-06-25**: switched from a single-threaded `HTTPServer` to `ThreadingHTTPServer`
  with daemon threads, since one slow or keep-alive connection was freezing every tab
  including Cost/Savings. Added a curated Active Projects tab reading
  `journal/active_projects.md` directly instead of scanning the whole `Clients/` tree,
  and a header data-freshness clock replacing a single ambiguous "Updated" label.
  Calendar tab wired to the real Work calendar merged with personal events, each event
  tagged by account; Insights, Slack, and Changes tabs repointed from static snapshots
  to live sources (`fathom_registry.json`, the latest daily-update harvest, and `git
  log` respectively).

### Activity OS: the Work tracker
A same-day cluster of changes on 2026-06-26 that turned a static ticket list into a
working triage surface:
- A GLM toggle and a ticket Tracker plus Follow-ups view, both reading from JSON state.
- Inline edit of status, priority, and note directly in the Tracker, no modal.
- A required comment or reason on every change, giving each ticket a per-change history.
- Follow-ups gained a clickable detail modal; Insights gained a summary header and
  GLM-generated takeaways condensed from Fathom meeting summaries.
- Tracker and Follow-ups merged into a single Work view; a Today focal view was added
  on top, along with a direct Meeting-to-Ticket action.
- Click-to-cycle priority, a Cmd+K command palette, and a waiting-days badge on stale
  items.
- Work reshaped as a filterable list with a project filter and a keyboard-driven Triage
  mode.
- You and Work items got distinct colors throughout, and a Routines tab proof of
  concept showed each cron job's intended schedule against its last actual run.

---

*How releases are cut: edit this file, `CHANGELOG.md`, in the private working repo as
part of the change it describes. The sync pipeline then carries it verbatim to the
public mirror the same way it carries every other root file, scrubbed for personal
data. There is no separate release step; this file is the source of truth for both
repos.*
