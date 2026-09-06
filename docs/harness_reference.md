# Harness Reference (on-demand detail for CLAUDE.md)

> This file holds the full tool detail that used to live in `CLAUDE.md`. The lean `CLAUDE.md` keeps the always-on rules and routing; **Read the relevant section here when the matching task appears.** A rule lives in exactly one place: core owns rules, this file owns detail.
>
> Anchors: [#platform](#platform) · [#google-workspace](#google-workspace) · [#repo-skills](#repo-skills) · [#subagents](#subagents) · [#preferred-tools](#preferred-tools) · [#dedicated-tools](#dedicated-tools)

---

## repo-skills

Main repo (path varies by machine -- run platform detection below to resolve `REPO_ROOT`):
- **macOS**: `.`
- **WSL (Ubuntu)**: `.`
- **Windows native**: no independently-edited checkout -- open sessions rooted at the UNC path `\\wsl.localhost\Ubuntu\home\you\antigravity-projects\product-second-brain` (same files as the WSL path, no proxy needed for reads/edits) and proxy skill *execution* into WSL via `wsl.exe` as below. A separate clone under `C:\Users\...\scratch\product-second-brain` existed historically but is retired (2026-08-03) to avoid two independently-edited copies drifting apart -- do not create a new one.

Skills: `.agent/skills/` -- always check here first when looking for a skill/connector.

**Harness layout**: `.claude/` = Claude Code entry points (commands/agents/hooks); `.agent/` = cross-harness SOP source (Antigravity reads it too). Commands shim to `.agent/` bodies -- do not duplicate content across the two.
**Slack send guard**: the PreToolUse hook matches `mcp__.*[Ss]lack.*__.*(post|send|reply).*`: if a Slack MCP server is renamed or added, re-verify the matcher in `.claude/settings.json`.

**Source of truth**: `origin/main`, not any one machine. WSL is the **automation host** (the repo cron jobs run there and nowhere else); macOS is a **first-class interactive checkout** with full hook/skill/command parity and no cron; Windows native is retired and proxy-only. Work counts once it is committed and pushed. Full rule: `CLAUDE.md` > "Checkouts: `origin/main` is the source of truth".

**Worktree awareness**: This repo is sometimes opened via Windows worktree. The worktree may not have all files, and it is never the place to resolve a disagreement about state: fetch and compare against `origin/main`.

---

## platform

This repo runs on three machines (macOS, WSL/Ubuntu, Windows native). Detect once at session start by running the single source of truth:

```bash
bash .agent/scripts/detect_platform.sh
```

It prints `PLATFORM` (macos | wsl | windows), `REPO_ROOT` for this machine, and `RUN_PREFIX`/`RUN_SUFFIX` for wrapping skill calls. Adapt every Python skill call accordingly:

| `uname -s` | PLATFORM | Role | How to run Python skills |
| :---- | :---- | :---- | :---- |
| `Darwin` | **macos** | First-class interactive checkout, no cron | Run natively from `REPO_ROOT`. NO `wsl.exe`. e.g. `python3 .agent/skills/.../x.py` |
| `Linux` | **wsl** | Automation host: all repo cron jobs live here | Run natively, as written in this file. |
| `MINGW*` / `MSYS*` / missing | **windows** | Retired, proxy-only | Proxy into WSL: `wsl.exe bash -c "cd <WSL_ROOT> && <command>"` |

Example -- `gdocs-create` on **Windows native** (the only platform that needs a prefix):
```bash
wsl.exe bash -c "cd . && timeout 180s python3 .agent/skills/gdocs-create/gdocs_create.py create-doc --title 'Title' --content '...' --account work"
```
On **macOS** and **WSL** the same command runs directly with no prefix.

**Fallback (Windows only, if `wsl.exe` fails)**: use `MSYS_NO_PATHCONV=1 wsl.exe bash -c "..."`. If WSL is not configured on a Windows machine, alert the owner -- Python skills need WSL there because credentials and tokens live in the WSL filesystem.

**WSL auto-start**: a Windows Task Scheduler task ("WSL Autostart", created 2026-08-03) runs `wsl.exe -d Ubuntu -u you -- sleep infinity` at logon so the Ubuntu instance (and its crond, since `/etc/wsl.conf` has `systemd=true`) is always up without the owner manually starting it. `dashboard_keepalive.sh` (hourly cron) reports a `wsl-instance` heartbeat row with `/proc/uptime` so a missed autostart or crashed instance is visible on the Routines tab instead of silently failing. Cron/skills/tokens were deliberately NOT ported to native Windows Task Scheduler -- the WSL-resident system (20+ interdependent cron jobs, credentials, custom ffmpeg build) already works; the gap was availability, not platform. Full rationale: `docs/proposal_wsl_windows_2026-08-03.md`.

**Critical**: `uname -s` is cheap and reliable, so always run detection and never guess. NEVER use `wsl.exe` on macOS (`Darwin`) -- the binary does not exist there and the call will fail.

---

## google-workspace

**Rule: MCP first for read/search. Python skills for create/update/delete. NEVER use the browser tool.**

### Update Protocol (applies to all Drive/Docs operations)

**Drive is the source of truth.** Before creating or updating any document:

1. Check if the file exists (search by title or use known file ID).
2. If it exists → **use `update`, not a new upload**. Preserves title, file ID, sharing settings.
3. Never change the title of an existing document when updating.
4. If Drive and local differ, Drive wins (verify with `modifiedTime`). Sync local to match Drive.
5. Add a changelog entry to the document header on every revision:

```markdown
| Revision | Date | Summary |
| :---- | :---- | :---- |
| v1.0 | 2026-01-15 | Initial draft |
| v1.1 | 2026-05-04 | Updated section 3 |
```

### Full Capability Matrix

| Action | Google Drive | Google Docs | Google Sheets |
| :--- | :--- | :--- | :--- |
| Search/find | MCP `search_files` | MCP `search_files` | MCP `search_files` |
| Read content | MCP `read_file_content` | MCP `read_file_content` | MCP `read_file_content` |
| Get metadata/link | MCP `get_file_metadata` | MCP `get_file_metadata` | MCP `get_file_metadata` |
| Create real Google Doc | `gdocs-create` `create-doc` | `gdocs-create` `create-doc` | - |
| Upload new file | Python `upload` or MCP `create_file` | - | MCP `create_file` mimeType `text/csv` |
| Update/replace content | Python `update --id FILE_ID` | Python `update --id FILE_ID` | - |
| Rename | Python `rename --id FILE_ID` (Work) | Python `rename` | - |
| Delete (trash) | Python `delete --id FILE_ID` | Python `delete --id FILE_ID` | Python `delete --id FILE_ID` |
| Delete (permanent) | Python `delete --id FILE_ID --permanent` | same | same |
| Share | Python `share` | Python `share` | - |
| Read comments | Python `comments --id FILE_ID` | Python `comments` | - |
| Write to Sheets cells | - | - | **Not supported** - ask user to export as CSV |

### Creating a Real Google Doc from Markdown

Always use `gdocs-create` - it produces true editable Google Docs with proper headings, tables, bullets:

```bash
# Create new doc
timeout 180s python3 ".agent/skills/gdocs-create/gdocs_create.py" create-doc \
  --title "Title" --file path.md --account work|personal

# Or with inline content (no temp file needed - saves tokens)
timeout 180s python3 ".agent/skills/gdocs-create/gdocs_create.py" create-doc \
  --title "Title" --content "# My Doc\n\nContent here" --account work
```

Do NOT use MCP `text/plain` (shows raw `#` symbols) or `text/html` (not an editable Google Doc).

### Which Python Skill to Use

| Account | Skill | Key Path |
| :--- | :--- | :--- |
| Work | `work-drive-connector` | `.agent/skills/work-drive-connector/gdrive_manager.py` |
| Personal | `personal-drive-connector` | `.agent/skills/personal-drive-connector/gdrive_manager.py` |

Both support: `upload`, `update`, `delete`, `search`, `read`, `comments`.
Work also has: `rename`.
`gdocs-create` supports both via `--account work|personal`.

### Token Status
- Work: `.agent/skills/work-drive-connector/token.json` - auto-refreshed ✅
- Personal: `.agent/skills/personal-drive-connector/token.json` - auto-refreshed ✅
- Secondary client: `.agent/skills/secondary-drive-connector/token.json` - generic slot for whatever non-Work/non-personal company is in use (`--account secondary` / `--profile secondary`). Currently holds the ex-Secondary token (revoked ❌); drop a new company's credentials here to reuse.

### Known Folder IDs (MCP)
- Personal My Drive root: `<YOUR_DRIVE_ID>`
- Work My Drive: omit parent-id (uses account root)

---

## subagents

Spawn subagents to isolate context, parallelize independent work, or offload bulk mechanical tasks. Don't spawn when the parent needs the reasoning, when synthesis requires holding things together, or when spawn overhead dominates.

Subagent definitions carry matching `model:` / `effort:` frontmatter; synthesis and strategy stay in the main loop. The routing table lives in core CLAUDE.md.

Rules: pick the cheapest row that fully covers the task; mechanical → delegate, judgment → keep in main loop. If a subagent finds it needs a higher tier than itself, return to the parent. The main loop cannot auto-swap its own model/effort. If a task needs a different main-loop tier than the current session, say so and ask the owner to `/model` or `/effort` (or run it as a Workflow with explicit per-stage model+effort).

**Bidirectional model routing.** `Agent(model: ...)` and Workflow's per-stage `model`/`effort` override the session model, so a spawned agent's tier is independent of the main loop's. Route by work class, not by session:

| Main loop is on... | Delegate DOWN to | Delegate UP to |
| :--- | :--- | :--- |
| **fable** (planning-heavy session) | `opus` for execution-grade writing/analysis chunks; `sonnet` for routine drafts; `haiku` for harvest/lookup | rarely needed; spawn `opus` only for a differing flagship lens |
| **opus** (default working session) | `sonnet` / `haiku` as per the routing table | `fable` + `effort: xhigh` for complex decomposition, ambiguous multi-step planning, adversarial plan review |

Concretely: on a fable session, do NOT let the main loop do bulk generation just because it is capable of it. Fan the execution chunks out to opus/sonnet workers and keep fable on the plan + the seams. On an opus session facing a genuinely hard planning problem (roadmap sequencing, a migration order, a multi-workstream unblock plan), spawn a fable planner, get the plan back, then execute in-loop.

Invariants that do not change with tier:
- Final the owner-facing synthesis, tone, and judgment stay in the main loop. Up-delegated agents return plans/analyses, not finished deliverables.
- `synthesize` and `strategize` never leave Claude (see Cross-model offload below); the down-delegation lever for those is effort, not a non-Claude model.
- Cost discipline: a higher tier is justified by decision-density, not by task size. Big-but-mechanical → haiku. Small-but-load-bearing → flagship.

**Cross-model offload.** `harvest`, `critic` (the adversarial half of `review`/`/hyperplan`), and `research` MAY route to a non-Claude model via `agy-bridge` instead of a Claude subagent (cheap-bulk or cross-model diversity); `synthesize` and `strategize` NEVER leave Claude. The bridge is capability-routed and cost-logged, and honors a `claude_fallback` sentinel when every non-Claude model is down (honor it, don't degrade). Offload heuristic: during likely-Anthropic-busy hours (~21:00-12:00 WIB) prefer the bridge to conserve the Claude pool. The full capability→model matrix, per-Mtok pricing, and time-routing live in the agy-bridge entry under [#dedicated-tools](#dedicated-tools) + `models.json`.

**Activity log (full-context memory).** On completing a tracked task (a PRD, a Slack send, a doc update, a dashboard/agent action, a daily/evening update), append one event via `python3 .agent/scripts/activity_log.py --actor agent --action <type> --project "<initiative>" --target <id> --summary "..."`. This feeds the dashboard's Tracker/Active-Projects roll-up and gives future sessions a log of what was done. Dashboard ticket edits auto-log via `/api/action`. Keep summaries one line; tag the right project (Marketplace/Platform/B2C/E-Commerce Solution/AI Circle).

**GLM offload mode (toggle).** SessionStart surfaces `GLM MODE: ON/OFF` from `.agent/glm_mode.flag` (write `on` or `off` into that flag file; see [`OFFLOAD_MODE.md`](../.agent/skills/agy-bridge/OFFLOAD_MODE.md)). ON = Router offloads heavy generation/research/draft/bulk to GLM 5.2 via `agy-bridge` (zero Claude quota); Claude still orchestrates, reviews, and applies, and final client-facing synthesis + judgment stay on Claude. OFF (default) = normal routing. A convenience switch over the agy-bridge chains, not a change to them.

**The Router (supervisor).** The main loop IS the team supervisor ("Router"): it classifies each request, picks the cheapest specialist + tier, spawns it, and synthesizes. There is no spawnable "boss" agent: a subagent cannot reliably spawn sub-subagents, so for multi-step parallel fan-out the Router uses the **Workflow tool** (it spawns the agents, not the agent). Specialists do single-domain work, call skills via Bash, and offload generation to Gemini via `agy-bridge --task draft`.

**Team roster (role labels for the existing agents):** `harvester`=Scout, `meeting-harvester`=Scribe, `draft`=Writer, `draft-reviewer`=Editor, `report-auditor`=Auditor, `hyperplan-critic`=Red Team.

**You / Work data separation (hard).** The personal-brand specialist agents (`social-producer`, `seo-specialist`, `perfmarketing-analyst`) moved to the You repo along with their connectors + tokens. This repo never runs You work; Work PM work stays on the Work connectors.

**Observability.** Scheduled routines + specialist agents write status to `dashboard-data/agent_heartbeat.jsonl` via `.agent/scripts/heartbeat.py`; the `localhost:3737` "⏰ Routines" tab shows last-success/fail per job, so a silent 2am failure is visible without polling.

Parent owns final output and cross-spawn synthesis. User instructions override.

---

## preferred-tools

### Data Fetching

1. **WebFetch**: free, text-only, works on public pages that don't block bots.
2. **agent-browser CLI**: free, local Rust CLI + Chrome via CDP. For dynamic pages or auth walls that WebFetch can't handle. Returns the accessibility tree with element refs (@e1, @e2). ~82% fewer tokens than screenshot-based tools. Install: `npm i -g agent-browser && agent-browser install`. Use `snapshot` for AI-friendly DOM state, element refs for interaction.
3. **Agent-Reach (platform-specific readers)**: for READING/searching specific platforms (Exa semantic search, X/Twitter, Reddit, YouTube subtitles, RSS, GitHub, LinkedIn public profiles), use Agent-Reach instead of hand-rolling a fetch. Read-only (no posting). Best fit for research sweeps and `/deep-research`. See the Dedicated Tools entry below for invocation. Use WebFetch/agent-browser for generic pages; reach for Agent-Reach when the source is one of those named platforms, or when you need semantic search.
4. **Notice recurring fetch patterns and propose wrapping them as dedicated tools.** When the same fetch/parse logic comes up more than once, suggest wrapping it as a named tool (e.g. a skill file or a .py script that calls `agent-browser` with the snapshot and extraction steps baked in for that source). Add the entry to [#dedicated-tools](#dedicated-tools) below and reference it by name on future calls.

### PDF Files

Use `pdftotext`, not the `Read` tool. Use `Read` only when the user directly asks to analyze images or charts inside the document. Read loads PDFs as images.

---

## dedicated-tools

### Research / internet (read-only)
- **Agent-Reach** -- multi-platform reader (Exa search, X authed-as-the owner, Reddit, YouTube, RSS, GitHub, Jina web). **Read-only, NEVER posts/DMs/comments.** Activate per shell: `source ~/.agent-reach/activate.sh`. Use for `/deep-research` and research sweeps. Commands, auth status, and the WSL DNS-over-TCP gotcha: [[reference_agent_reach_tool]] + `~/.agents/skills/agent-reach/SKILL.md`.

- **Slack handles for named people** -- [`.agent/scripts/slack_mentions.py`](../.agent/scripts/slack_mentions.py): `check` lists every person a draft names without a `<@ID>`, `apply` rewrites the first mention of each, `lookup --name X` resolves one, `refresh [--if-stale 7]` rebuilds `journal/state/slack_person_index.json` from `users.list` (live humans only, no bots and no deactivated accounts). Curated `Clients/Work/People/*.md` Slack lines win over the cache, which is how two Rahuls resolve to the right one. Enforced on the way out by `send_slop_guard.py`.

### Google Drive / Docs
- **Work Drive** -- [`.agent/skills/work-drive-connector/gdrive_manager.py`](../.agent/skills/work-drive-connector/gdrive_manager.py): upload/update/delete/search/read/rename/share/comments; `fetch_sheets.py` reads Sheets by tab.
- **GDoc comment replies (as the owner)** -- [`.agent/skills/work-drive-connector/reply_helper.py`](../.agent/skills/work-drive-connector/reply_helper.py): reply to AND resolve Google Doc comment threads as the owner (reuses the Work Drive OAuth token; owner verified = you@yourcompany.com). `whoami` confirms token owner; `list --id FILE_ID` prints comment IDs; `reply --id FILE_ID --comment COMMENT_ID --text "..." [--resolve]`. Base `gdrive_manager.py comments` only READS; this is the write path. **Caveat: `@email` mentions post as plain text via the API and usually do NOT notify the person**, so ping them separately (Slack DM). Approval-gated: confirm with the owner before posting, same as Slack.
- **GDoc anchored comments** -- [`.agent/skills/gdoc-comment/gdoc_comment.py`](../.agent/skills/gdoc-comment/gdoc_comment.py): post real-anchored comments via Playwright editor (mints kix anchors so comments preserve on copy/move). `login --account work|personal` once per account; `comment --doc DOC_ID --account work --items items.json` posts from JSON list with anchor + text.
- **Personal Drive** -- [`.agent/skills/personal-drive-connector/gdrive_manager.py`](../.agent/skills/personal-drive-connector/gdrive_manager.py): same caps, you@example.com.
- **Secondary-client Drive** -- [`.agent/skills/secondary-drive-connector/gdrive_manager.py`](../.agent/skills/secondary-drive-connector/gdrive_manager.py): generic non-Work/non-personal slot (`--account secondary`); drop creds+token in the dir.
- **Drive Permissions** -- [`.agent/scripts/drive_permissions.py`](../.agent/scripts/drive_permissions.py): **LANDMINE -- every upload auto-publishes as `anyone with link`, so docs leak public by default.** After any new `gdocs-create`/upload that shouldn't be public, run `restrict --domain yourcompany.com --apply` (`list <FILE_ID>` to audit; no `--apply` = dry run).
- **Google Docs Creator (preferred)** -- [`.agent/skills/gdocs-create/gdocs_create.py`](../.agent/skills/gdocs-create/gdocs_create.py): markdown -> real editable Google Doc (not raw text). Accounts work|personal|secondary. NOT MCP text/plain (shows raw `#`).
- **GDoc Surgical Editor** -- [`.agent/skills/gdoc-surgical/gdoc_surgical.py`](../.agent/skills/gdoc-surgical/gdoc_surgical.py): targeted in-place edits to an EXISTING doc (`read`, `replace`, `append`, `insert-row`, `list-tables`) via the Docs API; reuses the drive connector tokens. **Use this instead of `update --convert` whenever the doc already exists and only part of it changes** -- a full re-convert wipes images, resets sharing, and clobbers hand edits. Decision table + rules: [`.agent/skills/gdoc-surgical/SKILL.md`](../.agent/skills/gdoc-surgical/SKILL.md).
- **Google Docs Writer (legacy)** -- [`.agent/skills/gdocs-writer/scripts/gdocs_writer.py`](../.agent/skills/gdocs-writer/scripts/gdocs_writer.py): markdown->.docx->upload. Prefer gdocs-create unless `.docx` needed.
- **Automatic width pass** -- [`.agent/skills/gdocs-create/format_pass.py`](../.agent/skills/gdocs-create/format_pass.py) now runs itself at the end of `gdocs_create.py create-doc` and of every `gdrive_manager.py` upload/update that converts to a Doc (all three accounts). Pageless plus content-aware column widths, non-fatal: an auth failure prints the by-hand command and returns the doc. Skip it with `--no-format-pass` or `GDOC_FORMAT_PASS_DISABLE=1`. The dash lint warns here rather than failing, because the doc already exists by then.
- **Table Width Balancer** -- [`scripts/set_gdoc_table_widths.py`](../scripts/set_gdoc_table_widths.py): proportional column widths + pageless so content-heavy tables read well. **Run after every `gdocs-create`/`update --convert` on a table-heavy doc; re-run if table text changes.** Flags: `--help` / module docstring.
- **Mermaid Embedder** -- [`scripts/embed_mermaid_in_gdoc.py`](../scripts/embed_mermaid_in_gdoc.py): renders Mermaid -> PNG (kroki) into `[[PLACEHOLDER]]` slots (GDocs can't render Mermaid). **A re-push/re-convert WIPES inline images, so re-run after EVERY `update --convert`.** Sources live in the script's `DIAGRAMS` dict.
- **Work Weekly Reports (tabbed master doc)** -- [`scripts/weekly_reports_tabs.py`](../scripts/weekly_reports_tabs.py): one master Doc (ID `<YOUR_DRIVE_ID>`), one tab per week. **Run after a weekly report is approved instead of creating a standalone Doc.** SOP: [`.agent/skills/work-weekly-report/SKILL.md`](../.agent/skills/work-weekly-report/SKILL.md).
- **GDoc formatting pass** -- after any convert with tables/diagrams/numbered-lists, run the full pass (widths + mermaid embed + numbered-list fix) and verify before sharing; see [[feedback_gdoc_formatting_pass]].

### Calendar
- **Google Calendar** -- [`.agent/skills/google-calendar-connector/gcal_manager.py`](../.agent/skills/google-calendar-connector/gcal_manager.py): list/search/sweep; profiles `work` + `secondary`. Use this Python script for Work, NOT the MCP calendar tool (see [[feedback_work_calendar]]).

### Fathom (meetings)
- **Read** -- interactive: claude.ai Fathom MCP (`mcp__claude_ai_Fathom__*`); headless/registry: [`.agent/skills/fathom-connector/scripts/fathom_client.py`](../.agent/skills/fathom-connector/scripts/fathom_client.py) (`X-Api-Key`). Harvest via the `meeting-harvester` subagent. (Direct `mcp__fathom__*` server was removed -- rejects the API key.)
- **Sharing with a person** -- `fathom_client.py --action share-link --id <call id | recording id | URL>` prints the public `share_url`. A `fathom.video/calls/` link is internal: it needs an account plus the owner's approval, so sending one creates an access request instead of sharing anything (Teammate Meer, 1 Sep 2026). `send_slop_guard.py` blocks outbound sends carrying one.
- **Registry (which recording?)** -- [`scripts/fathom_registry_sync.py`](../scripts/fathom_registry_sync.py) -> [`journal/fathom_registry.json`](../journal/fathom_registry.json). **Grep the JSON FIRST** (by date_wib/matched_meeting/client) before hitting the API.
- **Frame grab (stills)** -- [`.agent/skills/fathom-frame-grab/scripts/fathom_frame_grab.py`](../.agent/skills/fathom-frame-grab/scripts/fathom_frame_grab.py): pull still frames for a BRD/PRD (API gives only transcript). SOP: [`.agent/skills/fathom-frame-grab/SKILL.md`](../.agent/skills/fathom-frame-grab/SKILL.md).

### Meeting recording routing (2026-07-06)
- **Vexa bot = PRIMARY auto-recorder** -- [`meeting-recorder/vexa_bots.py`](../meeting-recorder/vexa_bots.py) `auto` on cron `*/5` joins EVERY Work calendar event with a Meet/Teams link as bot "Your Name"; transcript -> registry + MOM draft. Log `/tmp/vexa_auto.log`, heartbeat job `vexa-auto`. Self-heals gateway-IP drift, restarts container/whisper-server, flags empty transcripts as failures.
- **Fathom = backup + video** for meetings the owner attends (unchanged).
- **Desktop recorder = manual fallback** -- [`meeting-recorder/recorder.py`](../meeting-recorder/recorder.py) / GUI; `--video` or GUI checkbox adds a screen-record `.mp4` sidecar (`video_path` in registry).
- **Dedupe: one meeting -> one MOM.** All three write to `journal/fathom_registry.json`; entries for the same `matched_meeting`+date are cross-referenced (`related_recordings`) and MOM drafting is skipped when a related entry already has `mom_path`. Full detail: [`meeting-recorder/README.md`](../meeting-recorder/README.md).

### Slack
- **Slack** -- [`.agent/skills/slack-connector/scripts/slack_client.py`](../.agent/skills/slack-connector/scripts/slack_client.py): read history/threads + **SEND AS the owner via `--action post`** (uses the owner's user token `SLACK_USER_TOKEN` / xoxp by default, no bot footer; `--thread-ts`, `--text-file`, prints permalink). **`--approved` is mandatory on `post`/`upload`/`invite`** -- the script refuses to send without it, and there is no environment-variable override, so pass it only once the owner has actually signed off on that draft. **NEVER send via the MCP Slack tools (those post as the Claude bot); confirm with the owner before EVERY send.**

### Figma
- **Figma (raw API)** -- [`.agent/skills/figma-connector/scripts/figma_client.py`](../.agent/skills/figma-connector/scripts/figma_client.py): REST fallback; prefer MCP Figma for design context.
- **Marketplace Figma index** -- [`scripts/marketplace_figma_index_sync.py`](../scripts/marketplace_figma_index_sync.py) -> mirror [`Clients/Work/Marketplace/Marketplace_Figma_Design_Index.md`](../Clients/Work/Marketplace/Marketplace_Figma_Design_Index.md). **Grep the mirror FIRST** for "where's the design for X".

### Issue trackers (Jira today, Linear from the 25 Aug cutover)
- **Jira** -- [`.agent/skills/jira-connector/scripts/jira_client.py`](../.agent/skills/jira-connector/scripts/jira_client.py): `verify-connections`, `daily-digest`, `sprint-status --portfolio`, `create-issue`. Two instances (`examplevendor.atlassian.net` = MSP/MBA/STOR, `yourcompany.atlassian.net` = MP/MPS); `PORTFOLIO_BOARDS` is the mechanical portfolio boundary, **never mix boards across portfolios in one review**. [`dump_all_issues.py`](../.agent/skills/jira-connector/scripts/dump_all_issues.py) feeds the work tree.
- **Linear** -- [`.agent/skills/linear-connector/scripts/linear_client.py`](../.agent/skills/linear-connector/scripts/linear_client.py): `verify-connection`, `teams --write`, `projects`, `cycles`, `cycle-status --team|--portfolio`, `issue`, `issues`, `daily-digest`; writes (`create-issue`, `update-issue`, `comment`) **refuse without `--approved`**. Key in `token.env`; **team keys are discovered into `teams.json`, never guessed** -- a key colliding with a Jira project key (MP/MPS/MSP/MBA/STOR) makes an identifier ambiguous and is skipped by the work-tree pass until source-qualified. [`dump_all_issues.py`](../.agent/skills/linear-connector/scripts/dump_all_issues.py) emits the same shape as the Jira dump so both merge into one work-tree pass. Also registered as a project-scope MCP server (`.mcp.json`, OAuth via `/mcp`) for in-session use; scripts and cron use the CLI, which the MCP is invisible to.

### Work product tracking
- **Master Product List** -- [`.agent/skills/master-product-list/register_prd.py`](../.agent/skills/master-product-list/register_prd.py): register a PRD into the MPL sheet + local md.
- **Work Link Sync** -- [`.agent/skills/work-link-sync/link_sync.py`](../.agent/skills/work-link-sync/link_sync.py): link a GDoc URL to MPL rows + Master Doc; [`batch_master_docs_upload.py`](../.agent/skills/work-link-sync/batch_master_docs_upload.py) for bulk (`--dry-run`).

### Operational / Dashboard automation
- **Command Queue** -- [`.agent/skills/command-queue/scripts/command_queue.py`](../.agent/skills/command-queue/scripts/command_queue.py): routes the owner's dashboard ticket task-comments to auto-dispatched headless `claude -p` workers, triaging by model+effort per CLAUDE.md. `scan` (enqueue), `dispatch --live` (spawn workers), `report` (queue status).
- **Inbox Hub** -- [`.agent/skills/inbox-hub/scripts/inbox_sweep.py`](../.agent/skills/inbox-hub/scripts/inbox_sweep.py): unified inbound-inquiry hub aggregating Slack mentions, Gmail, GDoc comments, Jira into dashboard inbox with AI triage and per-item drafts.
- **Interview Assistant** -- [`.agent/skills/interview-assistant/`](../.agent/skills/interview-assistant/): automates candidate pre-interview prep (CV analysis, question banks, custom rubrics by role) and post-interview assessment (Fathom transcript grading, scorecards, onboarding SLAs).
- **Proactive Assistant** -- [`.agent/skills/proactive-assistant/`](../.agent/skills/proactive-assistant/): the owner's autonomous Product Operations system syncing Dashboard, todo, and the PM ledgers (commitments/waiting_on); surfaces management mandates and team workload imbalances.
- **Project Tracking Update** -- [`.agent/skills/project-tracking-update/`](../.agent/skills/project-tracking-update/): Triple-Check protocol keeping Dashboard, todo.md, and the PM ledgers synchronized on task completion and overdue detection. `master_followup_tracker.md` is a generated view over the ledgers, rendered by `render_followup_tracker.py`, never hand-edited.

### Analytics / observability
- **Dashboard Sync** -- [`.agent/skills/dashboard-updater/scripts/dashboard_sync.py`](../.agent/skills/dashboard-updater/scripts/dashboard_sync.py): calendar+Drive+Slack -> `Dashboard.md`.
- **Mixpanel** -- [`.agent/skills/mixpanel-connector/scripts/mixpanel_client.py`](../.agent/skills/mixpanel-connector/scripts/mixpanel_client.py): events/funnels/retention/export (creds in `token.env`).
- **GA4 (Work)** -- [`.agent/skills/ga4-connector/scripts/ga4_client.py`](../.agent/skills/ga4-connector/scripts/ga4_client.py): read-only Google Analytics 4. `snapshot` (KPIs + deltas + top tables) is the default entry point; also `report`/`realtime`/`top`/`meta`/`accounts`. Default property = exampleprogram-estore (`config.json`). Token `token_ga4_work.json` via `ga4_auth_helper.py` (reuses Work OAuth client); analysis SOP in `SKILL.md`.
- **Heartbeat** -- [`.agent/scripts/heartbeat.py`](../.agent/scripts/heartbeat.py): routines/agents append status -> `dashboard-data/agent_heartbeat.jsonl` -> `localhost:3737` "⏰ Routines" tab (catches silent 2am failures). `--job <name> --status ok|fail --summary "..."`.

### Content / document tooling (dual-use; copies also live in the You repo)
- **Gemini Image** -- [`.agent/skills/gemini-image/generate.py`](../.agent/skills/gemini-image/generate.py): image generation. Best `--model gemini-3-pro-image`; key is **metered/billing -- confirm before any batch** ([[reference_gemini_image_skill]]). Brand-specific visual rules live in the You repo.
- **Diagram generator** -- [`.agent/skills/diagram-gen/SKILL.md`](../.agent/skills/diagram-gen/SKILL.md): English -> Mermaid, validate via `render_check.py`, then feed the Mermaid Embedder.
- **Make PDF** -- [`.agent/skills/make-pdf/SKILL.md`](../.agent/skills/make-pdf/SKILL.md): markdown -> publication-quality PDF (WeasyPrint) for lead magnets / one-pagers.

### Repo sync & development
- **sync-public** -- [`.agent/skills/sync-public/sync.py`](../.agent/skills/sync-public/sync.py): private-to-public repo sync with deep scrub (removes personal data, client names, tokens, Drive IDs before publishing to template repo). `--dry-run`, `--push` + audit; SOP in module docstring.

### Cross-model offload
- **agy-bridge** -- [`.agent/skills/agy-bridge/run.py`](../.agent/skills/agy-bridge/run.py): shell out to non-Claude models (agy: Gemini/GPT-OSS), capability-routed via [`models.json`](../.agent/skills/agy-bridge/models.json) with a `claude_fallback` tier. **Exit 3 = `{"status":"fallback_to_claude"}` sentinel the caller MUST honor.** Every call logs per-Mtok cost + Claude counterfactual to `dashboard-data/agy_usage_log.jsonl` (`--report` / `localhost:3737` 💸 tab). **z.ai / GLM 5.2 retired 2026-07-27** (subscription ended): removed from every chain, never pass `--backend zai`. Time routing has no live effect now that only flat-rate agy remains. The full capability->model matrix, pricing, and time-routing live in `models.json` + the SOP: [`.agent/skills/agy-bridge/SKILL.md`](../.agent/skills/agy-bridge/SKILL.md).
