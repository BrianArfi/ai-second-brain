# Changelog

All notable changes to the AI Second Brain harness. Newest first.

Entries are written in the private working repo and carried to the public template
(`ai-second-brain`) by the sync pipeline, which strips credentials, tokens, real client
names, and personal data. This file is copied verbatim, so it reads the same in both.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). From
v0.1.0 on, every release is a `## [x.y.z] - YYYY-MM-DD` section with Added, Changed
and Fixed lists, and the version rule is in [`docs/VERSIONING.md`](docs/VERSIONING.md).
The GitHub release body for a version is its section here. The dated headings below
v0.1.0 are the history from before versions existed.

You can also read this file inside the product: open the local dashboard and pick the
**What's new** tab.

## [Unreleased]

### Added
- **`gmail_manager.py send` now refuses without `--approved`.** Same gate and wording as `slack_client.py` (the shared `require_send_approval`, now labelled Gmail), checked before any network call and before the text gates, with no environment bypass. `send_email()` takes `approved=False`, so a programmatic caller is gated too. Every documented send command carries the flag, to be added only at the post-approval step; a recipient outside the work domain still needs the two-turn approval first. `docs/gates_inventory.md` gap 1 is closed.
- **The Slack handle gate now runs inside `slack_client.py`.** A named, resolvable person with no `<@ID>` is refused on `post` and `update` before the network call, using the same logic as the hook (`gates/handles.py`, override `SLOP_GUARD_ALLOW_PLAIN_NAMES=1`, also barred from `token.env`). Two live accounts for one name warn and pass. The chase queue and the dashboard click-send stay warn-only: they print the finding and pass the same override to the child, so a cron send cannot start failing.
- **Em-dash gate on Linear and Jira writes.** `linear_client.py` (create, update, comment) and `jira_client.py` (create, edit, comment) refuse an em-dash or en-dash in the title, summary, description or comment, after the `--approved` check and before the network call. Override `SLOP_GUARD_ALLOW_EMDASH=1`. `docs/gates_inventory.md` records exactly what is left for WhatsApp, which lives outside this repo.
- **Send gates now live in the sending scripts, not only in Claude Code hooks.** The em-dash ban, the internal Fathom link block and the reply-router conversation claim moved into a shared package, `.agent/scripts/gates/`. `slack_client.py` (both connectors), `gmail_manager.py send` and `gdoc_comment.py` run those checks before their first network call, so the rules hold under agy-bridge, opencode, cron or a member's setup. The hooks import the same functions and behave as before. Overrides are the existing ones (`SLOP_GUARD_ALLOW_EMDASH=1`, `SLOP_GUARD_ALLOW_FATHOM_CALLS=1`, `CLAIM_GUARD_DISABLE=1`); `slack_client.py` refuses to read them from `token.env`. `gates/outbound.py check` gives WhatsApp helpers outside the repo the same check. `docs/gates_inventory.md` lists every hook as in-tool, moved, or hook-only with a reason.
- **A read-only checkout health check.** `one_host_check.py` reports which checkouts hold unpushed commits, which machine last wrote each `journal/state` file, and whether the ASB app opens a non-canonical checkout. Nothing was changed on any machine.
- **A prompt hook recalls the right memory files.** `memory_recall.py` (UserPromptSubmit) matches the prompt against the names and descriptions of the files in `journal/memory/` that the always-loaded `MEMORY.md` index does not list, and prints at most five lines (`Relevant memory: <name> - <description> (<path>)`). Kill switch: `MEMORY_RECALL_DISABLE=1`.
- **Routines keep a note between runs.** `routine_memory.py recall <routine>` prints the last note and `retain <routine> --text ...` stores a new one (last 20 kept in `journal/state/routine_memory/<routine>.jsonl`). The daily update, the sweep and the chase queue now start with `recall` and end with `retain`, so a run does not repeat the searches the last run settled.
- **Sessions can find, warn and message each other.** Say "sesi lain", "yang tadi" or "the other session" and a prompt hook (`session_mention.py`) adds one line with the three session cards that best match your words. `claims.py` records which session holds a Google Doc, Jira or Linear key, file or branch (a claim goes stale after 12 hours, or when its session is done), and the Doc writers now warn when another live session holds the Doc and claim it for the current one otherwise (`resource_claim_guard.py`; it warns and never blocks). `session_notes.py` leaves a note for a session or for a work-tree node, and the target gets it at its next start (`notes_inbox.py`, five at most).
- **Every Claude session leaves a card, and new sessions see who else is running.** At the end of each turn a Stop hook (`session_card.py`) writes one small JSON file per session to `journal/sessions/`: a spoken name, a one-line purpose, the status (active, or waiting on you when the last reply ends in a question), the links, ledger ids and work-tree nodes it touched, and up to three open items. The card points to the transcript and never copies it. Because the folder is in git, a Windows, WSL or macOS session can see the others. At session start `also_running.py` lists up to five sessions active in the last 24 hours, one line each. `session_cards.py` adds `list`, `show`, `active-brief` and `set-status`, and `/session` (`session_ref.py`) now reads cards first and opens a transcript only when the card is missing or the detail is needed.
- **Session cards get a better purpose and name over time.** A card starts with the first prompt as its purpose. `session_cards.py refine [--max 10]` takes cards that still carry that raw text and have at least three user turns, asks `claude -p --model haiku` for one plain English line under 90 characters plus a 2 to 4 word spoken name, and stores `purpose_refined`. It runs from a temp directory with the card hook off, so it writes no card of its own, and it never runs inside the Stop hook. At session start `also_running.py` launches `refine --max 5` in the background with no output and no wait, at most once every 30 minutes (stamp file `journal/sessions/.refine_stamp`, gitignored). Off switch: `SESSION_REFINE_DISABLE=1`.
- **Every work-tree node now shows the records filed under it.** Commitments, waiting-on and decisions already named their node; now the node names them back. `work_tree_rollup.py` builds the reverse link from the records on every ledger sync, so it never drifts from them: a node's live records, counts for everything below it, last activity, and a health worked out from the records (red only when something slipped in the last 7 days, amber for older overdue, due soon, or live past 30 days). A parent takes the worst health below it. You can set a node's health by hand, but only with a reason, and with an end date if you want it to lapse. `work_tree.py show <node>` prints all of it, and the dashboard Work tab shows it next to the node. `work_tree.py set-next` ties a node's next step to the records it waits on, and the rollup flags record ids in a node's notes that have since closed.
- **Waiting-on records tell you when they move.** The waiting watchdog checks every hour whether the person you are waiting on replied in the thread, and runs "done-checks" for asks that finish as an action instead of a reply (first one: a GitHub repository now exists). When a record you watch moves, by any path, one Slack DM from the bot lists it with the link, so you can continue straight away. New records are watched by default; older ones only after `watch <id> --on`. The morning update opens with "Moved since yesterday" from the new `moved` command.
- **The daily update can keep a read-only Sheet copy of a git ledger.** A team that records decisions and blockers as files in a git repository can let people without git still read them. When the workspace has a ledger mirror script, `/daily-update` runs it once a day in Step 4: it pulls the ledger repository, rewrites a Google Sheet that nobody edits, and does nothing on a second run the same day. Creating the Sheet the first time still needs an explicit go.
- **Haiku agents stay under 100,000 tokens of context.** Claude Haiku 5.5 charges five times more per token once one request's prompt passes 100,000 tokens, cache reads included, and every turn re-reads the whole history. Splitting 400k of work into five fresh agents of 100k or less costs about a tenth of one long run. New `chunk_work.py` splits a work list into chunks of about 85k tokens and keeps findings in a state file, so each fresh `harvester` reads one chunk plus the earlier findings and stops. `harvester` gets `maxTurns: 20` as a backstop, and `CLAUDE.md` Subagents has the rule.
- **Ledger records lose priority when nobody raises them.** `priority_decay.py` calculates
  each open commitment, waiting-on and decision's priority on every read: one level down
  after 14 days with no signal, another after 30, and at 30 days it goes on the Saturday
  review list. A signal is a reply in the record's Slack thread, a meeting note or decision
  citing it, a ticket update, a chase, or `priority_decay.py signal <ID> --why "..."`. Your
  own briefings do not count, so listing a record does not keep it alive. Records due within
  7 days, or owed to or by the two named VIPs, never decay; records under a live work-tree
  node drop one level at most. The tracker opens with a Relevance section, the morning
  carry-over shows each record's level, and the chase list picks by level.
- **Linear admin access can be cut back in one command.** `linear_client.py restrict-admins --keep a@x.com,b@x.com`
  shows who keeps admin and who drops to member, and changes nothing until `--approved`.
  `linear_client.py members` lists every active user with their role.
- **Calendar events can repeat.** `gcal_manager.py create --recurrence 'FREQ=MONTHLY;BYMONTHDAY=5'`
  makes one recurring event, for checks like a monthly earnings review.
- **A weekly report shows which sessions spent the most, and on which model.** `token_usage_report.py`
  ranks sessions by price-weighted tokens, splits parent and subagent spend, and shows the share of
  subagent spend on Opus. It runs every Monday at 12:55 WIB.
- **Subagents with no model run on Sonnet, and costly spawns get a warning.** `.claude/settings.json`
  sets `CLAUDE_CODE_SUBAGENT_MODEL=sonnet`, so a spawned agent no longer inherits an Opus session.
  The new `agent_spend_guard.py` hook warns, and never blocks, when Opus is named for QA or lookup
  work, a session passes 15 agents, or an agent spawns another agent. It is safe to also register
  globally: two copies of the hook on one spawn log and warn once.

### Changed
- **`CLAUDE.md` is smaller.** The always-on file went from about 83 KB to about 61 KB (about 30 KB outside the app-managed sections). Long mechanics moved verbatim to `docs/claude_md_detail.md`; every hard rule keeps a one-line statement and a link. The map of what moved where is `docs/claude_md_slim_2026-10-15.md`.
- **`MEMORY.md` is split by scope.** It keeps the user and feedback lines (always loaded). Project and reference lines moved to `INDEX_project.md` and `INDEX_reference.md` in the same folder, each linked from `MEMORY.md`.
- **The Dashboard no longer loads into every session.** `dashboard_context.py` now injects a 2-line pointer; the trimmed Dashboard block loads at session start only for cron or daily-update runs (`AGENT_MODE=1`, `PSB_ROUTINE`, or a headless `claude -p` ancestor) or with `DASHBOARD_CONTEXT_FULL=1`. Otherwise `dashboard_prompt_context.py` (UserPromptSubmit) injects it once, the first time a prompt looks like Work or PM work.
- **Records can no longer sit on a top-level area.** Filing a commitment, waiting-on or decision on an area like "E-Commerce Solution" or "B2C Super App" told you nothing about which piece of work it moves, so the ledger tools now refuse it and list the nodes underneath. A top-level node with nothing under it still takes records. `work_tree.py coverage` and `validate` report any record still sitting on one.
- **A record's squad now comes from its work-tree node.** `portfolio_tagger.py` reads the node first and only falls back to guessing from the record's text when the node does not say which squad owns the work.

### Fixed
- **Session card refine no longer pops up a terminal window on Windows.** `also_running.py` started `session_cards.py refine` with `DETACHED_PROCESS`, so the child had no console, and its `claude -p --model haiku` call got a new visible console (shown by Windows Terminal) at most every 30 minutes. The hook now uses `CREATE_NO_WINDOW | NEW_PROCESS_GROUP`, and `summarize_with_claude()` passes `CREATE_NO_WINDOW` as well.
- **The ledger watcher now sees `gmail_manager.py send --approved` as a sent email, and ignores a refused one.** The old pattern only matched `gmail_connector` or `gmail.py`, so a real send by the connector script filed no "Email sent" reminder. The `draft_lint_stop.py` hook also takes `DRAFT_LINT_LOG`, so tests no longer write fake events into `journal/draft_feedback/lint_events.jsonl`.
- **Draft jobs fall back to GLM when the Gemini quota runs out.** When z.ai came back on 21 Sep, GLM was re-added to the reasoning and cross-lineage chains but not to the draft chain. So when the Antigravity Gemini quota ran out, every pre-meeting card skipped its cheap writer and went to Claude. This happened on three mornings in a row. `glm-5.3-flash` now sits third in the `draft` chain, after the two Gemini models, which matches the 22 Jul ordering decision.
- **Unattended Claude jobs find Claude again when only the editor extension has it.** On a machine where Claude Code exists only inside the VS Code (or Cursor) extension, `ai_call.py` found no Claude and handed every job to the non-Claude bridge. Jobs that need tools refused to run, so the Saturday ledger audit skipped itself with "tools-unavailable" and the waiting-on pile grew unchecked. It now falls back to the newest extension binary, which shares the same sign-in. The audit also stopped forcing an old stored token over that sign-in: the token had expired, and every run failed on "Invalid bearer token" in two seconds.
- **A deferred or cancelled ticket no longer closes a record.** The daily automatic check closed any commitment or waiting-on whose ticket Jira files as "done", and Jira files Deferred, Won't Do and Cancelled there too. Two records closed as answered because their ticket was deferred. Those states now go to the model and the weekly review as evidence instead, and Linear's Canceled is treated the same way.
- **Published artifacts now show up in the app's Outputs panel.** `publish_cf.py deploy` printed only the per-build `https://<hash>.work-artifacts.pages.dev` address, which Outputs skips on purpose, so no published HTML artifact ever reached Outputs. A deploy now ends with one `Name -> https://work-artifacts.pages.dev/<slug>` line for each artifact it added or changed, and the new `publish_cf.py links <slug>` prints the stable link of an artifact that is already live, for handing it over.
- **The public sync survives a sync from another machine.** When the public repo already had a newer sync commit, the push was rejected as non-fast-forward and the release never ran. The sync now moves onto the remote commit and keeps the freshly built tree, so nothing published there is lost.
- **The waiting-on sweep closes a record only when its owner replies.** Before, any reply in the
  thread from someone other than the owner closed it, so one person's answer could close another
  person's open ask. When the owner's Slack id is unknown, the old behaviour stays.
- **The calendar sweep shows the right "Today" before 07:00 WIB.** `gcal_manager.py sweep`
  took the date in UTC, so an early morning update listed yesterday's meetings. It now uses
  the host's local timezone.
- **`link_sync.py` no longer crashes on a Windows console.** Its tick-mark output raised a
  `UnicodeEncodeError` under cp1252, so the run looked failed. Output is now UTF-8.

### Added
- **Migration backlog rows show where the work really is.** `afm_linked_sync.py`
  reads the delivery tickets each backlog row cites (MSP, STOR, MBA, MP, MPS),
  writes them as clickable links with their live status, and fills the PRD
  column. It proposes a row status change only from the row's own delivery
  ticket, never from a legacy or dependency ticket. A second mode rewrites each
  tracking ticket's description from its sheet row: what, context, engineering
  questions and answers, blockers, related tickets, PRD and backlog link.
  Both modes are dry runs until `--approved`.
- **Backlog rows without a ticket get one, with a one-pager inside it.**
  `afm_create_tickets.py` turns a one-pager per backlog row (story, why, scope,
  acceptance criteria, dependencies, open questions, what is missing before
  estimation) into an AFM Jira ticket: epics first, stories under them, every
  description linking its PRD section and the sheet row. A dry run writes one
  review file to read before anything is created.
- **Long Jira batches survive slow responses, and a ticket rewrite never loses
  text.** The AFM Jira helper retries reads and updates after a timeout (never
  creates, which may have landed). Rewriting a ticket description keeps the
  text it replaces under a "Previous description" heading.
- **Reply drafts get a review page with pinned comments.** `draft_review.py publish`
  turns a draft file (the original thread plus the draft) into an unlisted page on
  the artifact host with shared comments on, so the owner marks the exact line to
  change instead of retyping it in chat. Revisions republish to the same URL,
  `comments` reads the pins back, and pages expire from the site after 30 days.
  Sending still needs approval in chat.

### Fixed
- **Mixpanel raw export reaches the EU data host.** The connector called
  `data.eu.mixpanel.com`, which does not resolve, so every `export` failed with
  a DNS error. It now calls `data-eu.mixpanel.com`.
- **The public sync blocks timestamped token backups.** A file named
  `token*.json.bak-<date>` slipped past both `token*.json` and `*.bak`. The leak
  audit stopped the push, so nothing leaked, and the blocklist now covers the name.

## [0.9.0] - 2026-10-05

### Added
- **`/session` refers one Claude session from another.** Name a session by
  id prefix, `last`, or any keyword it contained, and the current session
  loads what that one was asked, which files it wrote, the links and ledger
  ids it produced, and where it ended. No argument lists recent sessions;
  `/session here` prints this session's reference to paste elsewhere; several
  references and a trailing task work in one message. Backed by
  `.agent/scripts/session_ref.py` (stdlib, reads local transcripts).
- **Weekly planning and follow-ups read email through the Gmail connector.**
  The Gmail card depends on a Google preview program and fails until the
  project is enrolled, so these commands now name the connector directly.
- **`oauth_client_resync.py` recovers every Google connector on one OAuth
  client after its secret is reset.** `apply` copies a new client secret into
  each token and test-refreshes it. If the reset also revoked the grants,
  `reauth-url` and `reauth-save` re-consent all of them with one sign-in.
- **`CLAUDE.md` now states the changelog rule as a hard rule.** A skill,
  command, agent, or hook whose behavior changes is not done until this
  file has a new `Unreleased` bullet, written in the same turn as the code
  -- not deferred to release day. The `sync.py` pipeline to the public
  `ai-second-brain` repo already existed; this just makes feeding it
  non-optional.
- **Drafts shown in chat are checked before you answer them.** A new Stop
  hook, `draft_lint_stop.py`, reads the draft in the reply and blocks once
  when it finds a correction you keep making: an added offer or walkthrough
  line, a name in lowercase, or a message full of system terms. The
  corrected draft is what you see next. Off switch: `DRAFT_LINT_DISABLE=1`.
- **`draft_lint.py`, one set of draft checks for every drafter.** The same
  checks run in the chat hook, on draft files, on sends, and inside the
  automated drafters. `draft_lint.py replay` measures the catch rate
  against your past corrections.
- **`draft_context.py` gives a reply draft its context in one call.** For a
  Slack permalink it says whether you already replied or reacted, whether
  another session drafted or sent into that thread, and prints the whole
  thread plus your last three messages in the channel. The chat hook asks
  for it before a reply draft is shown.

### Changed
- **Automated drafters cut the line you always cut.** Reply queue, chase
  queue and inbox drafts now drop offer, walkthrough and history sentences
  and capitalise names before you see them.
- **The reply queue reads more before it drafts.** It skips a thread that
  another session owns, and gives the model your last three messages in
  the channel so a draft cannot contradict what you said.
- **The shared voice prompt lists your five most frequent corrections**, so
  every drafter that loads it avoids them.
- **The send guard and the draft-file guard use the shared checks.** The
  send guard also warns on a lowercase name or a very technical message.
- **The weekly draft-feedback report catches up after a missed Saturday.**
  It runs hourly from Saturday to Monday and does the work once per week,
  reports the week that ended, and counts how often the new checks fired.
- **The draft-feedback classifier keeps working when the cheap models are
  out of quota.** It falls back to Haiku through the Claude CLI, uses short
  ids the model cannot garble, and a stricter prompt so document comments
  and plain approvals are not counted as corrections. A week with replies
  still unclassified is flagged in the report and retried.
- **Reply sessions and the Slack send protocol start with `draft_context.py`**
  instead of `draft_need_check.py`, so every reply draft begins with the
  whole thread and your recent messages in that channel.

### Fixed
- **A sweep sees a reply posted outside the thread.** When someone answers
  your post with a new message in the channel or DM and does not tag you,
  the Slack thread reader now finds it. It reads each channel after your
  last top-level post: everything after it in a DM, the next 24 hours in a
  channel.
- **An item the label pass skipped stays in "Needs you".** It used to drop
  out of the report as soon as any other item had a label. It is now listed
  after the labelled items, with a warning to label it and run `label` again.
- **The label prompt asks for every item to be labelled**, and treats a
  helper's "done" on something you asked for someone else as needing your
  reply, because the person who first asked still has to hear it.

## [0.8.1] - 2026-10-01

### Changed
- **The README tells you what the template does for you first.** It opens with the shift, the gap and the fix, a diagram of the daily loop and a table of what is inside, then the quick start. The deeper reference moved to `docs/ARCHITECTURE.md`, `docs/DASHBOARD.md` and `docs/SETUP.md`, so nothing was dropped.
- **`/daily-update` reads your timezone from `CLAUDE.md`.** It no longer assumes WIB.
- **Domain sharing reads `WORK_DOMAIN` from your environment or `.env`**, so a fork shares Google files to its own domain without editing code.

### Fixed
- **Runtime files that a demo session committed by mistake are removed:** `.agent/harness.json`, a ledger session file, a dashboard cache and a sample commitment.
- **The ledger watcher counts a Google Doc update only when `gdoc_surgical.py` runs a command that writes.** Reading its source or running `read` no longer blocks the end of a turn.

## [0.8.0] - 2026-09-30

<!-- source: private repo commits after the v0.7.0 tag (2026-09-28 14:41 WIB): 1b82be1ce, 77bff0375, b0d9966fb, 0fd4e97c7, plus this change set -->

### Added
- **What's new tab in the dashboard.** The local dashboard renders this changelog,
  newest release first, from `/api/changelog`. The text is escaped before it is
  rendered, HTML comments are dropped, and links to repository files show as plain
  text.
- **Reply drafts show what they answer.** A new `reply_draft_guard.py` hook blocks a
  reply draft under `journal/drafts/` that does not quote the original message, with
  sender and time, and the thread so far. It also blocks draft text inside a code
  fence, because a fence does not wrap.

### Changed
- **Release notes come from this file.** `sync.py` builds the GitHub release body
  from the changelog section for the version being cut. When that section is
  missing it uses the Unreleased section, and when that is empty too it falls back
  to the commit titles, as before. Until now every release said "Harness sync from
  the private repo".
- **Versions v0.2.0 to v0.7.0 have their own sections.** They were backfilled from
  the git tags, the public repo's diff per tag range, and the private commit log.
  Each section names its sources in an HTML comment.
- Release headings use the `## [x.y.z] - YYYY-MM-DD` form. `tools/pack.py` reads
  both this form and the older `## vX.Y.Z` form.

### Fixed
- **The meeting recorder survives a trimmed `common.py`.** The pipeline helpers moved
  to `meeting-recorder/pipeline_common.py`, and `common.py` re-exports them, so a
  copy of `common.py` without the helpers no longer stops the watcher.
- The conversation claim guard no longer matches a top-level post against open
  threads in the same channel.

## [0.7.0] - 2026-09-28

<!-- source: tag v0.7.0 (public commit 06b8af9, 2026-09-28 14:41 WIB); GitHub release v0.7.0; `git diff v0.6.0..v0.7.0` in the public repo (14 files); private commits 6b18e8a59, a9a52b950, 7c8b4ba61, 615832efd, a9b132e1d, 23a8aaa52, 895a2db9d, 670cc326e -->

### Added
- **Draft feedback tracker.** `draft_feedback.py` measures how the owner edits the
  drafts the harness writes, and a weekly job commits the report so weekly planning
  can read it. `draft_need_check.py` runs before a reply is drafted: it skips a
  message the owner already answered, and suggests a reaction instead of a reply for
  a plain acknowledgement. It is wired into the reply queue, the reply router brief
  and the Slack send protocol.
- **Stale-status guard.** `unanswered_status_check.py` cross-checks calendar RSVPs
  and Slack threads, so an item the owner already handled is not reported as
  unanswered. The daily update runs the calendar check on every run. Identity is
  resolved at runtime (a new `gcal_manager.py whoami` action and Slack `auth.test`),
  with environment variables to override it.
- **Read-only JQL search** in the Jira connector, on every configured site.
- **Record one window.** The meeting recorder can record the window you were last in,
  or a window you pick, instead of every monitor (`list_windows`, `active_window`,
  `WindowsCapture`). It picks the video encoder that the local ffmpeg actually has.

### Changed
- `send_slop_guard.py` warns when a draft adds lines past what the message needs.

### Fixed
- The Windows recorder GUI imported window helpers that `recorder.py` did not ship
  in v0.3.0 to v0.6.0. They ship now.

## [0.6.0] - 2026-09-27

<!-- source: tag v0.6.0 (public commit 0b7e320, 2026-09-27 08:46 WIB); GitHub release v0.6.0; `git diff v0.5.0..v0.6.0` in the public repo (4 files); private commits 8919ddc99, 3b3d666c1 -->

### Changed
- **The cheap-model chain adapts to the machine.** `cheap_llm.py` builds its chain
  from the backends the machine actually has. The default order stays Gemini Flash,
  then GLM, then Haiku.
- **Idle work-tree nodes archive themselves.** A node with no activity for 30 days
  is archived by `work_tree_stale.py`. `work_tree.py add-node` records the creation
  time, so a new empty node is never counted as idle. `docs/ledger_hygiene.md`
  describes the rule.

## [0.5.0] - 2026-09-26

<!-- source: tag v0.5.0 (public commit 5519c9f, 2026-09-26 21:41 WIB); GitHub release v0.5.0; `git diff v0.4.0..v0.5.0` in the public repo (22 files); private commits a351f6f20, cb8bf600d, ab5d1714c, d0e9c6184, 87ff96f4a, 52eca0a17, 8926b854c, 9266291ef -->

### Added
- **Ledger verification by script first.** `cheap_llm.py` and the ledger CLIs check
  a record against its evidence with a script first, a cheap model second, and
  Claude last, so a chase is not sent for work that is already done.
- **Ledger hygiene rules** in `docs/ledger_hygiene.md`, with `work_tree_stale.py`
  for nodes that have gone quiet.
- **Google Doc publish checks R1 to R4** in `readability_gate.py`: internet links
  only, sized tables, every promised image exists, and one header field per line.
  `publish_prd.sh` can embed screenshots.
- **Text-drawn diagrams are refused in Google Docs.** Use a rendered Mermaid image.
  Override once with `GDOC_ALLOW_ASCII_DIAGRAM=1`.
- **Work hours per machine.** Each host writes its own shard under
  `journal/state/work_hours_hosts/`, and the tracker merges them, so every machine
  can sweep.

### Fixed
- A meeting recorder run with `--once` writes a heartbeat that stays valid until the
  next cron tick, so a recording is not reported as "nothing is transcribing it".
- `slack_client.py` reads `--text-file` as UTF-8, so bullets do not post as mojibake
  on Windows.
- `meeting-recorder/common.py` is the full module again, after a trimmed copy
  replaced it.

## [0.4.0] - 2026-09-23

<!-- source: tag v0.4.0 (public commit efbf184, 2026-09-23 16:54 WIB); GitHub release v0.4.0; the CHANGELOG.md text shipped in the public repo at v0.4.0; `git diff v0.3.0..v0.4.0` (23 files); private commits 2b3f0ed2f, e2397a076, 77bafca45, ec10e49f9, a27f096f7, 01a03f4d7, 9f2325b64, 0454a1ee1, f608a55e2 -->

### Added
- **Standing rules ship with the template.** `CLAUDE.md.template` had not changed
  since July, so a fork started without the rules the private harness had learned
  since. It now carries a **Standing Rules** section: do the work instead of
  reporting on it, fix what is broken, never claim an action that has not happened,
  verify before you report, treat a question as a question, done means done, answer
  first and stop, record it where it is tracked in the same turn, link every file you
  name, read a shared document before you replace it, never act on a garbled
  instruction, and offer to save a correction as a rule. Each one is there because
  leaving it out caused a failure that repeated. The block sits between two markers,
  so the desktop app exports the same text and a rule learned once reaches both.

### Changed
- **A leaner CLAUDE.md, and one less process per command.** Incident history,
  command lists and kill switches moved out of the always-loaded `CLAUDE.md` into
  `docs/harness_reference.md`, word for word, under five new anchors. Each rule stays
  in `CLAUDE.md` as one line plus a link. The no-op `drive_verify` hook, which
  started a Python process after every Bash call and did nothing, is unregistered.
- **Moving machines: the record travels, content does not.**
  `harness_migrate.py export` now leaves video, audio, images, archives and
  installers behind wherever they sit, and prints how much it skipped. The first
  real run found 3.5 GB of rendered video in a 3.9 GB bundle. The desktop app's own
  chat transcripts, which the bundle never carried, now travel and are restored on
  import. The migrate test runs in a sandbox instead of reading the real app data.
- The agy-bridge model table brings the GLM models back and probes their ids live.
- `/learn` says the truth about memory: it travels with git, and links in
  `MEMORY.md` are relative.

### Fixed
- **The session model is detected on Windows.** The startup routing hook printed
  "Session model: unknown" on every Windows session, so the delegation table never
  had a real tier to work from. Two causes, stacked. The transcript lookup replaced
  only `/` when it built the project directory name, while Claude Code replaces every
  character that is not a letter, a digit or `-`, so a path with a drive colon never
  matched. And Python's bare `bash` resolves to WSL's `System32\bash.exe` before
  PATH, so the detection script ran inside Linux against a Windows path. Both are
  fixed, with a regression test.
- **Tests that failed for the wrong reason.** Three tests failed while the code under
  them was right: one fed Windows paths unquoted, one used 1970 timestamps that a
  newer staleness rule reads as ancient, and one asserted a routine that had been
  deliberately removed. The last one hid a real bug: the reply-router settings
  schema still said a "Scheduled runs" switch gated it, which it does not. All fixed.
- The access watch credits an on-behalf-of share request to the person who needs
  access, not to the person who asked.
- The reply router takes back a dispatch that the app never turned into a chat.

## [0.3.0] - 2026-09-21

<!-- source: tag v0.3.0 (public commit a57705e, 2026-09-21 13:35 WIB); GitHub release v0.3.0; `git diff v0.2.1..v0.3.0` in the public repo (63 files); private commits d829905f0, e119801ff, aaf56f6c4, 4bed8d0f9, a23a3988a, b1045d7dc, 272b8adf1, ca4e3753c, 12f357908, bd7cb7eba, 4e7d15b2e, 0cdc580a2, 704eabfee, 94c4d1cef, cd39d98a0, 83bbaabbe, 066192502, 79b43b520 -->

### Added
- **One conversation, one session.** `conversation_claim_guard.py` refuses a send
  into a Slack conversation that another session already owns.
- **Evidence-gated clean-up for waiting-on records.** `answer_hunt.py` looks for an
  answer in six sources and splits strong evidence from weak leads, and
  `deprio_discard.py` deprioritises or discards a record only on strong evidence.
  The lifecycle is in `docs/waiting_on_lifecycle.md`.
- **Model drift check.** `model_drift_check.py` finds dead and outdated model ids in
  the agy-bridge chain before a call fails on them.
- **Local Ollama backend** for agy-bridge, through Ollama's OpenAI-compatible `/v1`,
  with a base URL override.
- **Calendar attachments.** `gcal_manager.py --attach` puts a Drive file on an
  invite, and `list --json` reports attachments.
- **Slack channel rename**, approval-gated like post and create.
- **Automation holds are visible.** `automation_settings.py` gives every automation
  one `held` block that says what is held, why, and when it clears.
- **Task board builder.** `build_task_board.py` builds one Kanban board from a
  roadmap sheet and a backlog, ready for a Linear import.
- **Windows dictation helper.** `meeting-recorder/dictate_win.pyw` records from a
  global hotkey and puts the transcript on the clipboard.

### Changed
- The agy-bridge chain moved off retired Gemini ids.
- The work-hours tracker counts every session on every host, and counts automation
  as parallel output. The dashboard's automatic sweep backfills the whole stale gap.

### Fixed
- `link_guard.py` knows every checkout root and follows writes through UNC paths,
  and a local file link opens on Windows.
- Reply drafts quote the whole original message and show people by name, never by a
  bare id.
- The ledger guard treats a checkout that is behind as "pull first", not as deleted
  records.
- `--convert` in the Drive connector maps pptx, docx and xlsx to their Google formats.
- The reply-router close status file is mirrored into every checkout.

## [0.2.1] - 2026-09-11

<!-- source: tag v0.2.1 (public commit 080368d, 2026-09-11 16:39 WIB); GitHub release v0.2.1; `git diff v0.2.0..v0.2.1` in the public repo (6 files); private commits 342f3c735, 0a8286caa, 8e0afa61c -->

### Added
- `SKILL.md` files for the `gdoc-comment` and `gemini-image` skills.

### Changed
- The reply router runs from the desktop app scheduler instead of a crontab line
  that was never installed. `/autodraft` documents the change.

### Fixed
- `register_prd.py` no longer corrupts the first row of every table it writes to.

## [0.2.0] - 2026-09-11

<!-- source: tag v0.2.0 (public commit ce7bbd7, 2026-09-11 13:26 WIB); GitHub release v0.2.0; the CHANGELOG.md text shipped in the public repo at v0.2.0; public commits 0b6a1dc, 7633f18, 38f5405, 63f880c, bee7d2c, f0441f6, 9f7bc1f, fc7ee8d; private commits 2bff0fb29, b94d0cc3c, 424d276df, d36ff5a39, 2b82b0868 -->

### Added
- **Seven new commands.** They wrap skills that already shipped with nothing in
  front of them. `/user-story` turns a feature into stories and holds the line that
  every `Then` has to be objectively measurable, because "the user sees a friendly
  message" cannot be tested and will be argued about. `/ticket` drafts a tracker
  issue, resolves its work-tree node first, and waits for explicit approval before
  filing. `/rca` runs the incident protocol under one rule: no root cause, no
  "resolved". On the artifact side, `/artifact` builds a self-contained HTML
  explainer or report, `/mockup` a clickable prototype with presenter keys, `/deck`
  a keyboard-driven slide deck, and `/diagram` a Mermaid diagram validated by an
  actual render before anyone sees it. The three HTML commands end with the same
  self-containment grep, because a page that pulls in a font or an image stops
  working the moment it is sent to someone.
- **Capability packs.** `tools/pack.py` installs skills, commands, agents and hooks
  from one folder as one unit, and never runs code the pack supplies.
- **The anti-AI-voice gate ships.** `.agent/skills/no-ai-slop` is now in the template,
  so `/no-ai-slop` resolves, and [`answer_budget.md`](.agent/skills/no-ai-slop/answer_budget.md) adds the rule that runs first:
  answer first, stop, and keep to a word ceiling per channel.
- `public_drift_check.py` reports how far the public mirror has drifted without
  running the sync.

### Changed
- **The command list says what each group is for.** The command set had grown to 32
  with flat descriptions, which reads as one long alphabetical list. Every
  description now leads with a group, so the picker reads in blocks: Daily, Weekly,
  Product, Artifact, Comms, Notes, Tracking, Thinking, Setup, Harness.
- **Six commands retired, none of their procedures deleted.** Each moved to a
  permanent home first and every reference was repointed: the Slack send playbook to
  `.agent/protocols/slack_send.md`, the offload-mode toggle to
  `.agent/skills/agy-bridge/OFFLOAD_MODE.md`, and the morning and evening updates
  into a now self-contained `/daily-update`, which takes `morning` or `evening` to
  force a mode. `/sync-fathom` and `/no-ai-slop` were pure wrappers over SOPs that
  already lived in `.agent/`. Two wirings would have broken silently and were
  repointed in the same pass: `journal/state/routines.json` scheduled
  `/morning-update` and `/evening-update` as daily routines, and the SessionStart
  hook printed `/glm on` as the way to toggle offload mode.
- `/inbox-sweep` is now `/sweep`, and `access-watch` ships, so its access pass works.
- The end-of-turn ledger sync runs in the background (`ledger_sync.py
  --background`), so a turn no longer ends waiting on it.

### Fixed
- **A public sync could delete the public template's own onboarding.** `sync.py`
  copies `.claude/commands/` with an `rmtree` first, so a command retired from the
  private repo vanishes from the public one on the next run. That is right for a real
  restructuring and wrong for the generic starter commands, which the private repo
  drops because it has richer replacements while a fresh fork has nothing. `/setup`
  and `/update-harness` would both have gone, and `README.md` names `/update-harness`
  in its own text. `PRESERVE_PUBLIC_COMMANDS` now restores those ten from the public
  repo's git HEAD after the tree copy.
- **A synced command could point at a skill that deliberately does not ship.**
  Scrubbing renames client strings; it cannot remove a whole section, so the public
  `/artifact` would have told a reader to publish through `artifact-host`, which is
  excluded. `PUBLIC_OVERRIDES` copies a public-specific version from
  `.agent/skills/sync-public/public_overrides/` after the tree copy. Two related gaps
  closed the same way round: `reply-router` and `linear-connector` now ship, because
  `/autodraft` and `/ticket` named them and they were absent, with
  `linear-connector/teams.json` held back for its real workspace and team ids.
- **The leak audit caught a home path with the real username in it, on a file type
  nothing blocked.** `meeting-recorder/logs/watcher.err` synced because `*.log` was
  a blocked pattern and `*.err` was not. The audit refused the push, which is the
  guard working, but the file had already been written into the public tree. Log
  directories are now blocked by directory name as well as by suffix, so the next
  runtime file does not have to be discovered by the audit first.
- **The Hours tab froze for three weeks and said nothing.** Four defects, one shape:
  every failure was silent and no watch existed on the file. `dashboard/server.py`
  spawned the background sweep behind `flock`, a Linux-only binary, and the
  `except Exception: pass` around it turned the resulting `FileNotFoundError` on
  macOS into a no-op, so the tab served a frozen `work_hours.json` and rendered it in
  the same muted grey as fresh data. The same `flock` prefix broke the dashboard's
  manual run-job buttons on macOS. `work_hours.py` counted every `sdk-cli` session as
  cron automation, which drops every session run through the desktop app or the SDK
  (0 sessions, leverage 1.0x); an app session is now recognised by the chat UI's own
  line types plus more than one human-typed minute. And `gcal_manager.py list` never
  followed `nextPageToken`, so a window wider than 250 events came back truncated at
  the old end and the sweep cached the recent days as "no meetings". Data from 10 Aug
  to 3 Sep 2026 was rebuilt by backfill.
- **Guards, so the next variant of this is loud.** A calendar fetch that returns
  nothing for a day that had events keeps the cached events and warns instead of
  blanking them. A sweep that finds session transcripts but counts none of them as
  interactive writes `sessions_warning` into the state and warns on stderr.
  `work_hours.json` joined the `harness_health` staleness table at 6 hours, next to
  the four ledgers. The refresh spawn logs both its attempts and its failures, and
  the failure reaches the Hours tab, which now turns the "updated Xh ago" label into
  a warning past 6 hours.
- The sync recovers a stuck rebase instead of skipping every sync after it.
- A send whose draft file the send gate cannot read yet is refused, instead of
  passing unchecked.
- The Slack tracker has a thread-participation pass, so a reply inside a thread you
  are in is seen even when it does not mention you.
- Slack mentions are decoded to names everywhere the harness writes text.
- The Jira connector detects Kanban boards instead of reporting a stale sprint.

## [0.1.0] - 2026-08-23

<!-- source: tag v0.1.0 (public commit c60aec7, 2026-08-22 23:29 +0300, which is 2026-08-23 WIB) -->

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
- **Indonesian connection kit** in `docs/workshop/`: [`MULAI_DARI_SINI.md`](docs/workshop/MULAI_DARI_SINI.md) (start here),
  [`PANDUAN_KONEKSI.md`](docs/workshop/PANDUAN_KONEKSI.md) (step-by-step tool connection guide), matching PDFs, and illustrated
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
