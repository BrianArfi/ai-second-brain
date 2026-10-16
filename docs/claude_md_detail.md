# CLAUDE.md detail (moved verbatim, 2026-10-15)

> Blocks moved out of the always-on `CLAUDE.md` on 15 Oct 2026 to shrink the frozen prompt prefix. Each block below is the original text, unchanged. `CLAUDE.md` keeps a condensed statement of every rule and links here by anchor. Map: [`claude_md_slim_2026-10-15.md`](claude_md_slim_2026-10-15.md).

---

## header-note

*Header blockquote (was `CLAUDE.md` lines 3-3)*

> Lean core: always-on principles, routing, and hard rules. Full tool detail (Google Workspace matrix, per-skill CLIs, folder IDs, platform proxying, agy-bridge economics, dedicated-tools catalog) lives in [`docs/harness_reference.md`](docs/harness_reference.md) -- Read the matching section there when a task actually needs it.

---

## workflow-routing-table

*Workflow Routing table (full rows) (was `CLAUDE.md` lines 20-40)*

| the owner asks for... | Follow |
| :--- | :--- |
| PRD (draft/revise) | `.claude/commands/prd.md` |
| Meeting notes / MOM | `.claude/commands/mom.md` |
| User stories / backlog breakdown from a feature or PRD | `.claude/commands/user-story.md` -- INVEST plus Gherkin, every `Then` measurable |
| Create or edit a Jira or Linear ticket | `.claude/commands/ticket.md` -- Linear for new Work work, Jira for existing `MP-`/`MPS-`/`MSP-`/`MBA-`/`STOR-` keys; approval-gated |
| HTML explainer, report, technical response or proposal | `.claude/commands/artifact.md` -- self-contained, published to `work-artifacts.pages.dev`, handed over as a URL |
| Clickable mockup, prototype or demo of a flow | `.claude/commands/mockup.md` -- presenter controls, built to be screen recorded |
| Slide deck to present from | `.claude/commands/deck.md` -- one self-contained HTML file, keyboard driven |
| Diagram inside a PRD, BRD or Doc | `.claude/commands/diagram.md` -- Mermaid, validated by render before it is shown |
| Slack message (any) | [`.agent/protocols/slack_send.md`](.agent/protocols/slack_send.md) -- approval-gated, enforced by the `--approved` flag on `slack_client.py` |
| Any You/personal-brand ask (social content, LinkedIn post, SEO, growth/ads, posting, video) | **Wrong repo** - open the You repo (macOS: `~/You/You`, WSL: `~/antigravity-projects/You`); its commands: `/social-plan`, `/social-draft`, `/linkedin-post`, `/seo-check`, `/growth-report`, `skills/post/`, `skills/video-suite/` |
| Weekly progress report | `.claude/commands/weekly-report.md` |
| Morning/evening/daily briefing | `/daily-update` (picks morning or evening by WIB time; force with `/daily-update morning`) |
| Weekly review + planning | `.claude/commands/weekly-planning.md` |
| Incident postmortem / RCA | `.claude/commands/rca.md` -> `.agent/protocols/incident_rca.md` (Iron Law: no root cause, no "resolved"; 4 phases; 3 owned prevention actions) |
| Publish the harness to the public template repo | `.claude/commands/sync.md` -- scrub + leak audit before any push; never hand-edit the public repo |
| Persist a lesson/correction | `.claude/commands/learn.md` |
| "Look at / continue from the other session" | `.claude/commands/session.md` -- brief of a past session by id, `last`, or keyword; `SendMessage` for one still running |
| Anything acted on that a ledger, tracker, or todo.md is tracking | `## Ledger Discipline` below -- update the record in the SAME turn, via the CLI (never by hand) |
| Any message addressed to a named human (Slack, email, doc/Jira comment, chase note) | `.agent/skills/no-ai-slop/SKILL.md` -- runs automatically before `draft-reviewer`, see Quality Gates |

---

## bias-to-doing

*Bias to DOING, not reporting (standing rule) (was `CLAUDE.md` lines 42-51)*

**Bias to DOING, not reporting (standing rule, overrides any instinct to ask first).** Every task, surfaced need, or gap defaults to **produce the artifact**, so the owner's job is review-and-approve, never assign-and-wait. Concretely:
- Analysis asked for -> **run it on real data and write the document**, don't hand back a plan for how it could be analysed or a list of what data would be needed. If the data is partial, do the analysis on what exists and state the limits in a "Limits of this analysis" section at the end.
- Message needed -> **write the full draft**, don't ask whether to draft it.
- Doc/PRD/BRD gap, even in another team's domain -> **write the proposal** for their sign-off (`feedback_missing_block_draft_dont_defer`).
- Decision needed from the owner -> present it **with a recommendation and the draft already built** on that recommendation, so approving costs one word.
- Blocked or ambiguous -> do every part that is NOT blocked, deliver it, then ask the ONE question that is actually blocking. Never stop with nothing produced.
- **Never ask a question you can answer yourself** by reading a file, running a query, or checking a ledger.
- **Reversible and cheap -> just do it, then say what was done.** A question costs the owner more than a re-run costs me. Ask first only for the three things that are not cheap to undo: anything that reaches an audience, anything that cannot be undone, anything expensive (paid API batches, metered image generation, long compute).
- **Something is broken -> fix it.** A broken script, a stale link, a wrong count, a failing hook: repair it in the same turn instead of reporting it. Reporting an issue that could have been fixed turns my work into the owner's to-do list.
Approval gates are untouched and still block: Slack/WhatsApp sends, Drive writes, anything client-facing, anything destructive. The rule changes what exists **before** the gate, not the gate. Full rule: memory `feedback_bias_to_doing_not_reporting`.

---

## never-claim-unexecuted-action

*Never claim an action that has not happened (standing rule, hard) (was `CLAUDE.md` lines 53-57)*

**Never claim an action that has not happened (standing rule, hard).** Describe an action in the past or present tense only once it has **actually executed**. Intent is not execution: a draft is not a sent message, an open ledger record is not a chase, a plan for today is not "I am doing it".
- **Banned until the action is real:** "I've sent it", "I am chasing him on it", "I've raised it with X", "this is with Finance", "already flagged". The recipient acts as though the thing exists, then asks where it is.
- **Say the true state**, with owner and time: "Not sent yet, going out in your thread within the hour", "Drafted, waiting on your approval".
- **Verify before writing the claim.** Sent means a permalink exists. Chased means `last_nudge_at` is set. Published means a Drive link came back. No evidence in hand, no sentence.
- **A commitment about the future is the correct replacement**, so long as it carries a time and a venue. Full rule and the 12 Aug 2026 incident: memory `feedback-verify-before-reporting`.

---

## question-not-work-order

*A question is a question, not a work order (standing rule, hard) (was `CLAUDE.md` lines 59-63)*

**A question is a question, not a work order (standing rule, hard).** When the owner asks a question, answer it. Do not implement it.
- "Why is this failing?" is not "make it stop failing". "Should we use X?" is not "migrate everything to X". "Can we do Y?" is not "go build Y".
- When in doubt, assume it is a question. Answer first, act when the owner says go.
- This does not weaken bias-to-doing, which governs **work the owner asked for**. This rule decides **whether work was asked for at all**. A question carrying an instruction ("why is this failing, fix it") is an instruction.
- Answer with the next step pre-built where that is cheap: the diagnosis plus the one-line fix ready to apply. Building it is fine; applying it uninvited is not.

---

## done-means-done

*Done means done (standing rule) (was `CLAUDE.md` lines 65-65)*

**Done means done (standing rule).** Five things asked means five things delivered. If the fifth is genuinely blocked, finish the other four and name the blocker in one sentence, with who unblocks it. "I'll continue in the next message" is not a state this repo has, and neither is a plan handed back in place of the artifact. Scope is the owner's to cut, not mine: quietly narrowing a task and reporting it complete only surfaces when someone downstream needs the missing part.

---

## doing-is-not-deciding

*Doing is not deciding (companion, standing) (was `CLAUDE.md` lines 67-72)*

**Doing is not deciding (companion, standing).** Done-means-done governs **artifacts**, so every surfaced need still becomes a finished draft here. Branching governs **the owner's decision queue**: once the artifacts exist and two or more independent things still need the owner himself, split. Mechanics: `## Branching Into Sub-Sessions` below.
- **Standing pre-approval, no offer needed**, in these flows only: chase queue, reply queue, `/daily-update`, `/sweep`, `/follow-ups`. the owner approved this once on 3 Aug 2026. The app-managed protocol below says never to branch unprompted; **this paragraph is that prompt**, for those flows and nowhere else. Do not re-litigate it mid-run.
- **Everywhere else, offer and stop.** Last line of the reply, nothing after it:
  `Branchable: <n> independent items -- <title>, <title>, <title>. Split into their own sessions?`
- **Never branch what you can finish.** Branching splits the owner's attention, not my effort.
- **Sending stays gated** inside the sub-session. Branching creates sessions, it never sends.

---

## every-meeting-mom

*Every meeting -> MoM + action items (standing rule) (was `CLAUDE.md` lines 74-74)*

**Every meeting -> MoM + action items (standing rule).** Whenever a new local recording, transcript, or MoM draft appears (meeting-recorder watcher, on cron work-window only) or the owner points at a meeting, produce/finalize the MoM (`mom.md`) and draft its action items. the owner's own action items are auto-captured into the commitment-ledger by the local pipeline (don't duplicate). Other people's action items get logged into the waiting-watchdog ONLY when they block the owner or belong to an initiative the owner actively tracks (`waiting_watchdog.py add`); everything else stays in the MoM, untracked. Never guess unresolved "Speaker N" owners. Full rule: memory `feedback_every_meeting_mom_action_items`.

---

## gather-synthesize-rules

*Standing rules for any gather->synthesize task (was `CLAUDE.md` lines 76-79)*

Standing rules for ANY gather->synthesize task (reports, briefings, summaries):
- **Never synthesize while harvesting.** Delegate bulk reading to the `harvester` subagent; complete all collection before drafting.
- Recency is NOT importance -- weight by delivered milestones > unblocked blockers > active risks > ongoing work; cross-reference todo.md P0/P1.
- Synthesis/planning work (/weekly-report, /weekly-planning, /prd) runs in the main loop: make sure the session is on a flagship model (opus or fable); mechanical work is delegated to haiku/sonnet agents, and complex planning can be delegated UP to a fable agent. See `## Subagents`.

---

## outgoing-language-default

*Every outgoing message defaults to English (was `CLAUDE.md` lines 92-92)*

**Every outgoing message defaults to English** (set 19 Sep 2026). Slack, WhatsApp, email, Jira and Doc comments, chase notes, drafts. The language the owner typed his request in does not change the language of the output: he asks in Indonesian, the draft is still English. Indonesian only when he asks for it on that message, or when the recipient needs it (ClientB). **Chat replies to the owner are English too**, with the `Ringkasan` block still in Indonesian per Quality Gates. This does not weaken the rule below on never working from a machine translation: understand him in the language he spoke, then write the output in English.

---

## YourManager-julie-approval-steps

*Double approval for YourManager and Julie: steps (was `CLAUDE.md` lines 102-104)*

1. **First approval, the content.** Show the draft. the owner approves what it says.
2. **Second approval, the send.** Show the final text again, unchanged, with the venue (channel or DM) and say it is about to go out. the owner approves again, in a separate message.
Two approvals means two separate turns. A single "ok" never covers both, and approval of an earlier version never carries to an edited one: any change after the first approval restarts the pair. If either approval is missing, the message does not go out. `--approved` goes on the command only after the second one.

---

## third-party-email-approval

*Double approval for every email to a third party (was `CLAUDE.md` lines 106-106)*

**Double approval for every email to a third party (hard rule, 5 Oct 2026).** Any email with a recipient outside `yourcompany.com` follows the same two-turn approval as YourManager and Julie above: content first, then the final text with To/Cc shown again before `gmail_manager.py send`. The script refuses `send` without `--approved` (15 Oct 2026); the flag goes on only after the second approval. A batch "send" counts as the first approval only. Sent email cannot be recalled. Memory: `feedback-third-party-email-double-approval`.

---

## send-as-owner

*Send AS the owner, never the Claude bot (was `CLAUDE.md` lines 108-108)*

**Send AS the owner, never the Claude bot.** All Slack sends go out through `slack_client.py --action post` using the owner's user token (`SLACK_USER_TOKEN`, xoxp), which the `post` action now selects by default. This posts as the owner with no "Sent using @Claude" footer. **Do NOT use the MCP Slack send tools** (`mcp__*Slack*__*send/post/reply`): those post via the Claude bot and add the footer. `--approved` is mandatory on every send: it is the only signal to the script that the owner has actually signed off on that specific draft (there is no environment-variable bypass), so omit it only while the draft is still pending review, and add it once approval is in hand. For a thread reply: `--thread-ts <parent_ts>`. For long messages: `--text-file <path>` (avoids shell escaping). The command prints the message permalink on success (include it per [[feedback-slack-sending-and-drafts]]).

---

## reply-draft-shows-original

*Every reply draft shows what it answers (was `CLAUDE.md` lines 110-110)*

**Every reply draft shows what it answers** (29 Sep 2026): the original message in full, with sender and time, and the thread so far when it is a thread reply, above the draft, in chat and in files. One block per message: `**Name** · time` on its own line, then the full text as a `>` quote, blank line between messages. `reply_draft_guard.py` blocks a `journal/drafts/` reply without them. **Drafts and originals are `>` quotes, never code fences**: a fence does not wrap, so the owner scrolls sideways to read (24 and 29 Sep 2026). The same hook blocks any draft file with message text in an untagged fence.

---

## github-estate-index

*Technical ground truth: the GitHub estate index (was `CLAUDE.md` lines 127-131)*

Before a PRD, ticket, BRD, or technical answer names a specific service, repo, or stack, check it against [`Clients/Work/work_github_estate_index.md`](Clients/Work/work_github_estate_index.md) (83-repo survey, 2 Oct 2026) rather than inferring from memory or a client-facing product name. Three things it will save you from getting wrong:
- **No repo is literally named "Example Program" or "Marketplace."** Those run through `storefront-*`/`work-ecommerce-core` (Example Program tenant) and `work-ecommerce-core`/`seller-portal-*` (Marketplace) -- confirm the actual tenant mapping before citing a repo for either.
- **GitHub is a 5-minute mirror of Bitbucket, not the source of truth.** Treat it as read-only; don't propose pushing to it directly. Deep code reading still goes through the Bitbucket clones in [[reference_work_examplevendor_bitbucket_repos]] for the 4 actively-developed repos.
- **Branch naming is inverted org-wide**: pushing to `test` deploys to the develop environment; `develop` deploys nowhere. Default branch is NOT consistently `develop` (29 are, 29 are `main`, the rest vary) -- check the index or the repo before assuming.
If a node you're resolving under `journal/state/work_tree.json` covers New World platform/product work, cross-reference the index for the actual repo(s) in play so the node's summary cites real service names -- this is a sourcing aid, not a schema change; there is no CLI field for it, so don't hand-edit the tree's JSON to add one.

---

## you-repo-detail

*You separate repo: paths and hard data separation (was `CLAUDE.md` lines 137-139)*

All personal-brand work (social content, AI Circle, podcast, classes/Goakal, TLN, Suami Qawwam, posting pipeline, video suite) lives in the **You repo**: `github.com/you/You`, cloned at `~/You/You` (WSL runtime for its cron/automation: `~/antigravity-projects/You`). It is a self-contained Claude Code harness (own CLAUDE.md, commands, agents, skills). Redirect the owner there for any You ask instead of doing it here.

**Hard data separation (both directions):** the You repo uses its OWN connectors/tokens (Repliz, Meta, TikTok, YouTube, personal Drive) and NEVER touches Work's Mixpanel/Metabase/Drive/tokens. Conversely, this repo does NOT run You social/SEO/growth work. Dual-use skills (`agy-bridge`, `gemini-image`, `make-pdf`, `personal-drive-connector`, `google-ads-connector`) exist as independent copies in both repos.

---

## repo-platform-checkouts

*Repo & Platform: checkouts, auto-commit, memory, ledger lock (was `CLAUDE.md` lines 151-176)*

**No machine is the source of truth. `origin/main` is.** Work exists once it is committed **and pushed**; until then it lives on one laptop and no other session, agent, or cron job can see it. Enforced at session start by `.agent/scripts/assert_canonical_checkout.sh` (warns, never blocks): it flags a checkout that is behind origin, and unpushed commits left sitting.

Each machine has one job:

| Machine | Path | Role |
| :--- | :--- | :--- |
| **WSL (Ubuntu)** | `.` | **Automation host.** The repo cron jobs run here and nowhere else. |
| **macOS** | `.` | **First-class interactive checkout.** Full hook/skill/command parity. No cron. |
| **Windows native** | UNC into the WSL files | Retired. Proxy-only, `wsl.exe`. |

- **Never install harness cron on a second machine.** Two automation hosts means duplicate chases, duplicate ledger writes, and auto-commits racing each other. macOS stays current through the SessionStart git sync plus `ledger_freshness.py`, not through its own sweeps. This is why a macOS session sees a ledger the moment it opens but never writes one on a timer.
- **Push before it counts.** Anything produced on either machine must be committed and pushed **before** it is published anywhere. On 3 Aug two PRD/BRD Google Docs were published from a stale copy and had to be re-published.
- **Never write `journal/state/*.json` from Windows.** The next WSL sweep overwrites it, silently.
- **Beware `$(...)` and backticks in `wsl.exe bash -c '...'`** -- Git Bash expands them on the Windows side first, so `git log` silently reports the *Windows* repo while appearing to describe WSL, and a quoted heredoc loses its backticked text. Put the commands in a script file, written with the Write tool.
- **A file the owner will open is written to the checkout THIS session runs in**, not to whichever checkout this file calls canonical. The app resolves a link from the session's own workspace root. If it must exist in both, copy it.
- **Hand the path over with forward slashes.** `C:/Users/you/.gemini/.../file.md`, never `C:\Users\...`: a backslash path can reach the renderer as a UNC root, the drive letter is discarded, and a file that is present reports as missing.
- **`link_guard.py` validates against the host that runs it, not the host that reads.** Invoked through `wsl.exe` it checks the WSL filesystem and passes a link that is broken for the owner. Run it from the session's own checkout, or treat a pass as meaningless.

### Auto-commit, memory, session root, ledger lock

- **The whole working tree auto-commits on Stop** (`worktree_commit.py`); credential-shaped files are screened out and named instead.
- **Auto-memory lives in the repo**: `~/.claude/projects/<slug>/memory/` is a symlink to `journal/memory/`.
- **Sessions start from the repo root**, never from the ASB template workspace.
- **Any new writer to `journal/state/*.json` takes the lock**: `hold_ledger_lock('<ledger>')` from `.agent/scripts/ledger_lock.py`. Atomic writes alone lose overlapping updates.

Mechanics, commands and the incidents behind each: [`docs/harness_reference.md#repo-mechanics`](docs/harness_reference.md#repo-mechanics).

---

## changelog-rule

*Skill behavior changes an entry in CHANGELOG.md (was `CLAUDE.md` lines 180-202)*

A skill, command, agent, or hook whose **behavior** changes (not a comment
or doc typo) is not done until `CHANGELOG.md` has a new bullet under
`## [Unreleased]`, in the same turn as the code change -- not deferred to
release day. Same spirit as the ledger discipline below: act on it, record
it together. Auto-commit (`worktree_commit.py`) already gets the code to
`origin/main`; this rule is the one auto-commit cannot do for you, because
it does not know *why* the diff matters to a fork.

What counts as public, and what does not:
- **This repo's own `CHANGELOG.md` + `sync.py`** is the only place a skill
  update needs to reach. `sync.py` already carries the `Unreleased` section
  to the public `github.com/BrianArfi/ai-second-brain` repo and cuts a
  GitHub release from it (see [`docs/VERSIONING.md`](docs/VERSIONING.md)) --
  that pipeline is not new, it just silently does nothing if the entry was
  never written here first.
- **You** (`~/antigravity-projects/You`) is a fully
  separate repo for the owner's personal-brand content engine, with its own
  `CHANGELOG.md` and its own hard rule. Never write a changelog entry in
  the wrong repo -- see "Pemisahan Data" in your own `CLAUDE.md`.
- **you.com** reads this product's changelog live from the public
  `ai-second-brain` GitHub repo at `/changelog` (owner-arfi-website repo,
  `src/pages/Changelog.tsx`) -- nothing to push here for that page to
  update; it is automatic once `sync.py` has run.

---

## ledger-intro

*Ledger Discipline: why (was `CLAUDE.md` lines 208-208)*

The lock above stops records being *lost*. This section stops them going *stale*. Several Claude sessions and every repo cron job read the same four ledgers out of the same repo, and none of them can see this conversation. Whatever is not written into a record does not exist for anyone else, and a record written but not propagated does not exist for anyone else either.

---

## ledger-trigger

*Ledger Discipline: the trigger and the hooks (was `CLAUDE.md` lines 222-224)*

The trigger is the **action**, not the conversation about it. Sending the Slack message, publishing the doc, transitioning the Jira ticket, filing the PRD: each of those is the moment the record changes, and the record change belongs in the same turn. `.claude/hooks/ledger_watch.py` watches for exactly those actions and `.claude/hooks/ledger_guard.py` blocks the end of the turn once if one happened with no record behind it. The watcher counts an action only when the script actually ran at a command position **and** the call succeeded, so a probe, an `echo`, or a commit message that merely quotes a send command no longer files a phantom discharge, and a send that errored no longer marks the record as handled (fixed 12 Aug 2026, after three phantoms landed in a single session).

If an action genuinely maps to no tracked item, say so in one line and move on. The gap to avoid is silence.

---

## ledger-propagation

*Ledger Discipline: propagation, links, commands, keeping small (was `CLAUDE.md` lines 228-237)*

One ledger CLI call does the whole chain: write under the lock, re-render the indexes and the tracker, commit, push. So:

- **Never hand-edit `journal/state/*.json` or `journal/master_followup_tracker.md`.** Use the CLI. If a hand edit must stand, run `ledger_sync.py sync --reason "<what>"` right after.
- **Every ledger id written for the owner is a link**: [`WAIT-0274`](http://localhost:3737/#find/WAIT-0274). Never in outbound messages; Jira keys stay Jira links. `ledger_link.py` linkifies a file.
- **A ledger read after a while may be behind**: `ledger_sync.py refresh`.
- **`work_tree.json` is not one of the four**: update a node's `moved` bullets only when something moved at initiative level.

Propagation chain, commands, kill switches, and the cross-machine deletion guard: [`docs/harness_reference.md#ledger-mechanics`](docs/harness_reference.md#ledger-mechanics).

**Keeping the ledgers small** (added 26 Sep 2026, after the pile grew back within a week of the 19 Sep cleanup): a record needs an owner, a date and a node; every record leaves within 30 days; at most 5 chases a day in the morning update; the Saturday audit closes what has evidence and hands the owner one review list. Full rules, targets and commands: [`docs/ledger_hygiene.md`](docs/ledger_hygiene.md).

---

## worktree-node-taxonomy

*Work-tree node rule: single taxonomy and portfolio alias (was `CLAUDE.md` lines 245-247)*

`journal/state/work_tree.json` is the **single taxonomy for tickets**. Not `portfolio.json` initiative ids, not `initiatives.json`, not the free-text `project` string. Where those disagree, the work tree wins.

`portfolio.json` keeps its own ids for the Portfolio tab, joined to the work tree by `journal/state/work_tree_alias.json` (`portfolio initiative id -> node id`), maintained by `work_tree.py alias` and validated on every `work_tree_link.py --check`. No third id space.

---

## worktree-node-ask

*Work-tree node rule: when the node is not obvious (was `CLAUDE.md` lines 260-266)*

This is the part that carries the rule. Filing a record under a plausible-but-wrong node is worse than not filing it, because it reports as tracked and never surfaces again.

- **Confident** (the source names the client, drop, or initiative, or the Jira key already sits under a node): file it, say the node in one line, move on. No question.
- **Not confident**: file nothing yet. **Batch every ambiguous record from the turn into ONE `AskUserQuestion` at the end of the turn**, each with 2 or 3 candidate nodes plus "new node". Never one question per record, and never a question mid-flow that stalls the work.
- **`unfiled` is a triage queue, not an escape hatch.** It is allowed only when the owner is unreachable in-session (cron, sweep, subagent), requires `--why`, and every `unfiled` record appears in the next morning update until it is filed.

Enforced three ways: the CLI (`--node` required on every `add`), the Stop hook (`ledger_guard.py`), and the sweep (`work_tree_link.py --check`). Linkage lives on the record, never hand-curated on the tree. Commands: [`docs/harness_reference.md#work-tree-mechanics`](docs/harness_reference.md#work-tree-mechanics).

---

## google-workspace-bullets

*Google Workspace: versions, overwrite, publish checks, email, sharing, verification (was `CLAUDE.md` lines 274-281)*

- **Version numbers only ever go up, and the number comes from the Doc, not the repo copy.** Read the Doc's revision table and bump from ITS last version; a local mirror under `Clients/` is a copy and anyone can edit the Doc without it. `gdoc_surgical.py insert-row`/`set-cell` refuse a version at or below one already there (`--allow-version-regression` to override). For whole-file writers, check first: `python3 .agent/scripts/doc_version_guard.py check --id <doc> --new-version vX.Y --mirror <local.md>`, which also reports a mirror that is behind.
- **Read the Doc before an `update` that replaces it.** `gdocs_writer update` overwrites the whole document, so an edit made in the Doc and not mirrored locally is destroyed, and the version guard does not catch it because it reads the revision table, not the body. `drive_update` refuses when the Doc's `modifiedTime` is newer than the local file (`GDOCS_ALLOW_STALE_OVERWRITE=1` once the Doc has actually been read). Recover with `python3 .agent/scripts/gdoc_revisions.py <DOC_ID> --export <dir>`; only `work-drive-connector/token.json` can read revisions, the others return zero, which reads as "no history" when it means "no permission".
- **Column widths run themselves.** Every `create-doc` and every `--convert` ends with the pageless plus content-aware width pass, inside the writer. Off for one call: `--no-format-pass` (gdocs-create) or `GDOC_FORMAT_PASS_DISABLE=1` (Drive connectors). By hand: `python3 .agent/skills/gdocs-create/format_pass.py <DOC_ID> --account work`.
- **Publish checks R1 to R4 (25 Sep 2026).** Every Doc passes four checks, enforced by `readability_gate.py` inside `publish_prd.sh`. R1: internet links only, no local paths or localhost. R2: sized tables. R3: every promised diagram or screenshot exists and renders; build a mockup, never leave a `[[SCREENSHOT: ...]]`. R4: one header field per line. Detail: `.agent/skills/gdocs-create/SKILL.md`.
- **Email is read through the repo connector, never the Gmail card** (5 Oct 2026). `python3 .agent/skills/gmail-connector/gmail_manager.py list --query "..."` and `get <id>`, Work account. The app's Gmail card (`mcp__gmail__*`) calls Google's preview-gated Gmail MCP and fails until project 352863487418 is enrolled. Sweep and daily updates already use the connector. If it fails with `invalid_client` or `invalid_grant`: `python3 .agent/scripts/oauth_client_resync.py check`.
- Create real Google Docs with `gdocs-create` (`--account work|personal|secondary`), never MCP text/plain (shows raw `#`).
- **Sharing defaults to the client domain, not the public.** Writers call `apply_visibility` in `.agent/scripts/file_utils.py`: `domain` (`yourcompany.com`) for work accounts, `private` for personal, and it **revokes** any pre-existing anyone-with-link on every write. Pass `--visibility public` (or `--share`) to publish deliberately. `drive_permissions.py restrict` remains for auditing older files.
- After every create/update/upload: output the file ID + Drive link and confirm, or treat as failure. Enforced in-process by the `assert_drive_result` helper (`.agent/scripts/file_utils.py`), called from every writer script before it reports success -- not by a PostToolUse hook.

---

## answer-budget-core

*Answer Budget: wordlist note and first three bullets (was `CLAUDE.md` lines 292-296)*

The wordlists in `no-ai-slop` fix how a sentence reads. They cannot say whether the paragraph should exist. This gate does, so it runs first: a tidy paragraph nobody asked for is still slop, and STE only makes it a tidy paragraph in shorter sentences.

- **First sentence is the answer.** Not the context, not what was found, not how it was found.
- **No process narration.** Which files were opened, which script ran, which subagent was spawned, what was considered and rejected. The one exception is the routing block in `## Subagents`, which is one line and required.
- **No closing recap**, no restating the answer under a heading, no explaining a thing the owner did not ask about. One exception, below.

---

## answer-budget-coverage

*Answer Budget: not covered and machine check (was `CLAUDE.md` lines 302-304)*

Not covered: briefings, weekly reports, MOMs, PRDs, and Dashboard sections. Those have their own templates and the `report-auditor`. Apply the no-narration, no-recap, and no-fake-table rules to their prose and leave the structure alone. Reply drafts keep their three-part format per [[feedback-slack-sending-and-drafts]]; the budget applies inside each part.

`send_slop_guard.py` warns when an outbound draft goes over its ceiling or carries a rationale section nobody asked for. **A chat reply to the owner has no machine check**, because no hook sees assistant text on its way to him. That half runs on this rule alone.

---

## no-ai-slop-intro

*No AI Slop: scope and order (was `CLAUDE.md` lines 307-309)*

**Every message written for a named human to read passes `.agent/skills/no-ai-slop/SKILL.md` first.** Slack messages, DMs and thread replies, Gmail replies and outbound email, Google Docs comments, Jira comments, meeting follow-ups, chase notes, and reply drafts in `journal/`. Edit in the main loop, self-check against `.agent/skills/no-ai-slop/eval.md`, then show the owner the finished version only. No before-and-after unless he asks; the raw draft was never the deliverable.

Order matters: **answer budget cuts, then no-ai-slop edits, then `draft-reviewer` verifies.** Running the reviewer on an unedited draft wastes the pass, and editing a draft that should be half the length wastes the edit.

---

## ste-rule

*ASD-STE100 on by default (was `CLAUDE.md` lines 311-311)*

**ASD-STE100 is on by default, on every communication.** Chat replies to the owner, Slack, email, comments, chase notes, PRDs, MOMs, briefings, weekly reports, repo prose. Rule set: [`.agent/skills/no-ai-slop/ste.md`](.agent/skills/no-ai-slop/ste.md). The short form: instruction sentences at 20 words or fewer, other sentences at 25, one instruction per sentence, active voice, present tense, articles kept, no `-ing` form doing a verb's job, noun clusters of three words maximum, one word per meaning every time, simplest exact word, no idiom or metaphor, paragraphs of six sentences or fewer, warnings before the action. The STE approved dictionary is not applied verbatim, because it has no vocabulary for product work; Work and client terms stay. Quotes are exempt. Indonesian drafts get the structural rules only. STE wins on structure, the owner's voice still wins on tone, and the repo overrides (no em-dash, no translation, no unexecuted-action claims) outrank both.

---

## never-work-from-translation

*Never work from a translation (standing rule, hard) (was `CLAUDE.md` lines 313-315)*

**Never work from a translation (standing rule, hard).** the owner's voice input sometimes arrives machine-translated, and a translated instruction is not the instruction. On 25 Aug 2026 "hold" arrived as "pemerintah" and the release job as "keberuntungan", and acting on it wrote the wrong requirement into a PRD and a Jira ticket.
- **Input that reads translated or garbled is a stop, not a guess.** Read the understanding back to him in the original language, numbered, name the one thing still ambiguous, and wait.
- **This gate comes before any spec, document, ticket or draft is changed**, and it outranks the bias to doing above. Understand him in the language he spoke; the output still goes out in English per Document language above. Full rule: `.agent/skills/no-ai-slop/SKILL.md` override 7, memory `feedback-writing-voice-and-language`.

---

## owner-voice-rule

*Anything sent as the owner is written in the owner's measured voice (was `CLAUDE.md` lines 319-319)*

**Anything sent as the owner is written in the owner's measured voice.** [`.agent/skills/no-ai-slop/brian_voice.md`](.agent/skills/no-ai-slop/brian_voice.md) is the model, built from 1,530 Slack messages he typed himself. Read it before writing a Slack or WhatsApp message or a Jira/Doc comment, not after the draft exists. Headline is length: **median 61 characters, 75% under 200**. One message, one thing. Lowercase is normal. Every ask carries a softener: a sentence-final "ya", a question mark, or "right?". **"ya?" is not the softener.** The question mark makes it a request to confirm, so it belongs only on a sentence whose whole point is a yes/no confirmation ("so we go live next monday ya?"). On a statement, a hand-over or an instruction it reads as unsure or passive-aggressive: the softener there is bare "ya", no question mark. No heading, no table, no bold lead-in, no bullet list under three parallel items, no sign-off, no closing recap. The automated drafters (`reply-queue`, `chase-queue`, `inbox-hub`) load the same model from `.agent/scripts/brian_voice.py`, so editing the skill file updates all of them.

---

## plain-language-permission-asks

*Permission asks and reports on my own actions are plain human language (was `CLAUDE.md` lines 323-323)*

**Permission asks and reports on my own actions are plain human language (hard rule, 14 Oct 2026).** Anything I say to the owner about the system doing something on his computer (a permission ask, a status report on a command, script, git step or file edit) says what it is trying to do and what permission it needs, in one plain sentence: no commands, file paths, flags or terminal text, on screen or aloud. Exception: when the owner is discussing requirements, client work or technical subject matter, technical words are fine. Talk Mode follows the same rule (`prompt_core.md`). Memory: `feedback-plain-language-permission-asks`.

---

## slop-gate-tail

*No AI Slop: not covered, no-send, hooks (was `CLAUDE.md` lines 327-331)*

Not covered: PRDs, MOMs, weekly reports, briefings (templated documents with their own gates), and internal repo files. Apply the editing judgment to their prose, but never restructure a templated document to satisfy this skill. Repo overrides live in the skill: em-dash stays banned outright, client language is never translated, and on Indonesian drafts only the patterns apply, not the English wordlists.

This gate changes what the draft looks like. It does not send anything. Slack, WhatsApp, Gmail, Drive, and anything client-facing still wait for the owner's explicit approval.

**Hooks back this up**: `emdash_guard.py` on the way in, `send_slop_guard.py` on the way out (blocks an em-dash and a `fathom.video/calls/` link; warns on slop and length). **Write a draft in one Bash call and send it in the next**, or the guard never sees it. Overrides and coverage: [`docs/harness_reference.md#quality-gate-mechanics`](docs/harness_reference.md#quality-gate-mechanics).

---

## link-resolves-rule

*Every Link Resolves, And Every File Named Is Linked (was `CLAUDE.md` lines 335-335)*

**A document that names a file links it, and the link must open.** Local paths are absolute, for the machine this session runs on, with forward slashes. No relative links in `journal/`, `Clients/` or `inbox/`, and never a link to a folder. Check with `python3 .claude/hooks/link_guard.py <file|dir>`; `--fix` repairs the safe classes. Per-machine roots and the 19-20 Sep incidents: [`docs/harness_reference.md#quality-gate-mechanics`](docs/harness_reference.md#quality-gate-mechanics).

---

## draft-review-gate

*Draft Review (before showing to the owner) (was `CLAUDE.md` lines 338-340)*

Before presenting any PRD, MOM, Slack, weekly report, or LinkedIn draft: spawn the **`draft-reviewer`** subagent (haiku -- `.claude/agents/draft-reviewer.md`, SOP in `.agent/skills/draft-reviewer/SKILL.md`). If PASS -> present. If issues -> fix first.

Before delivering any briefing or weekly report: spawn the **`report-auditor`** subagent (pinned to `sonnet` / effort `high` in its own frontmatter, it does NOT inherit the session model -- `.claude/agents/report-auditor.md`). Include its scorecard; fix anything NOT READY first.

---

## handing-over-files

*Handing Over Files (was `CLAUDE.md` lines 344-350)*

**An HTML deliverable under `Clients/` goes to the owner as an artifact-host URL, never as a file path.** The live host is **Cloudflare Pages** (`publish_cf.py`); `deploy_host.py` is superseded. Publishing is two steps: add the artifact to the `ARTIFACTS` list in `.agent/skills/artifact-host/scripts/publish_cf.py` (name, one-line description, repo-relative path), then

```bash
python3 .agent/skills/artifact-host/scripts/publish_cf.py deploy
```

The stable URL is `https://work-artifacts.pages.dev/<slug>`, where `<slug>` is the name slugified. Ignore the per-deployment `https://<hash>.work-artifacts.pages.dev` URL the deploy prints; it pins to one build and goes stale. Republishing the same name replaces the content and preserves the URL. Publishing is a Drive-equivalent external write, so it stays approval-gated: ask, publish, then hand over the link. Enforced as a warning by `.claude/hooks/artifact_publish_guard.py`. Every other local file (image, PDF, export) goes out as the link for **this** machine: run `bash .agent/scripts/artifact_link.sh <path>` and hand over what it prints (a `\\wsl.localhost\...` UNC path from the WSL host, a `file:///Users/...` link from macOS). Never hardcode the UNC form; it is unopenable from a Mac.

---

## drive-operation-verification

*Drive Operation Verification (was `CLAUDE.md` lines 353-353)*

After every Drive `create`, `update`, or `upload`: output the **file ID** + **Drive link**, confirm ("File updated: [title] -> [link]"), and if no file ID came back treat it as failure. Enforced by `assert_drive_result` (`.agent/scripts/file_utils.py`), called inside every writer script before it reports success. The old `drive_verify` PostToolUse hook was a no-op and is unregistered (23 Sep 2026); the script stays for history.

---

## subagents-intro-note

*Subagents: pick the cheapest row (was `CLAUDE.md` lines 371-371)*

Pick the cheapest row that fully covers the task; mechanical -> delegate, judgment -> keep in main loop.

---

## subagents-announce-plan

*Subagents: announce the plan, don't gate on it (was `CLAUDE.md` lines 373-379)*

**Announce the plan, don't gate on it.** Before any task that will spawn subagents or run a Workflow, emit ONE compact block, then start work in the SAME turn without waiting for approval:

`Plan agent: <1-line what> | <tier>: <who does what> | main loop: <what stays>`

Example: `Plan agent: MOM 3 meeting | haiku x3: pull transcript + raw facts | main loop: tulis MOM + action items`.

Skip it for single-tool or trivial work. This is a notification, not a checkpoint. Approval gates that already exist (Slack sends, Drive writes, anything client-facing) are untouched and still block. If the plan changes mid-task (an agent fails, scope grows), say the new routing in one line and keep going. Surfaced each session by `.claude/hooks/routing_mode.sh`, which also prints the session model.

---

## subagents-haiku-100k

*Subagents: Haiku 100k rule (was `CLAUDE.md` lines 381-385)*

**Haiku 100k rule.** Claude Haiku 5.5 (what the `haiku` alias resolves to on the Anthropic API) costs 5x more per token on any single request whose prompt is over 100,000 tokens (cache reads and writes count). Keep every Haiku agent under that line:
- Give a Haiku agent one chunk, at most 85k tokens of source (about 10 transcripts or 15 MOMs), never an open-ended list.
- For more than one chunk, run `python3 .agent/scripts/chunk_work.py init` and spawn one fresh Haiku agent per chunk. The agent reads its chunk and the earlier findings with `next`, writes findings with `done`, and stops.
- The parent reads the findings from the state file, never from the agent's chat reply, and synthesizes after the last chunk.
- Pin the alias to the intended model with `ANTHROPIC_DEFAULT_HAIKU_MODEL`; on Bedrock, Vertex and Foundry `haiku` is still 4.5 (flat price, no threshold).

---

## subagents-delegation

*Subagents: delegation goes both ways (was `CLAUDE.md` lines 387-392)*

**Delegation goes both ways.** The Agent tool takes an explicit `model` (`haiku` | `sonnet` | `opus` | `fable`), so the tier of a spawned agent is independent of what the main loop is running. Match the model to the WORK, not to the session:
- **Delegate DOWN (bulk / mechanical).** Whatever the main loop is on, push harvest, lookup, routine drafts, and format conversion to `haiku`/`sonnet` subagents. This is the default and applies just as hard when the session is on fable: a flagship main loop is for holding the deliverable together, not for reading 12 transcripts.
- **Delegate UP (hard thinking).** When the session is on opus and the job is complex decomposition or planning, spawn `Agent(model: "fable", effort: "xhigh")` for the plan, then execute and synthesize back in the main loop. Same move in reverse if the session is on fable and a task needs a different flagship lens: spawn `opus` as a second opinion.
- **Main loop keeps final synthesis + judgment + the owner-facing output.** An up-delegated agent returns a plan or an analysis, never the finished client deliverable.
- Multi-step fan-out with mixed tiers -> Workflow tool with explicit per-stage `model` + `effort` (e.g. plan stage `fable`, execute stages `sonnet`, verify stage `opus`).
- The main loop still cannot swap its OWN model/effort. If the *whole* session is on the wrong tier, say so and ask the owner to `/model` or `/effort`; spawning is the workaround for a single task, not for a whole session mismatch.

---

## subagents-you-separation

*Subagents: You and Work data separation (was `CLAUDE.md` lines 394-394)*

**Hard rule: You and Work data are fully separated.** The personal-brand specialist agents (`social-producer`, `seo-specialist`, `perfmarketing-analyst`) live in the You repo with their own connectors/tokens and never touch Work connectors; this repo never runs You work.

---

## subagents-cross-model

*Subagents: cross-model offload, GLM mode, router, activity log (was `CLAUDE.md` lines 396-396)*

**Cross-model offload + GLM mode + Router + activity log + observability:** `harvest`/`critic`/`research` MAY route to a non-Claude model via `agy-bridge` (honor the `claude_fallback` sentinel); `synthesize`/`strategize` NEVER leave Claude. On completing a tracked task, append one event via `.agent/scripts/activity_log.py`. GLM offload mode is a flag file, `.agent/glm_mode.flag`; ask for it in words ("offload on") and see [`OFFLOAD_MODE.md`](.agent/skills/agy-bridge/OFFLOAD_MODE.md). The main loop is the Router (classify -> pick cheapest specialist -> spawn -> synthesize; multi-step fan-out uses the Workflow tool). Details: [`docs/harness_reference.md#subagents`](docs/harness_reference.md#subagents) + agy-bridge economics in [`#dedicated-tools`](docs/harness_reference.md#dedicated-tools).
