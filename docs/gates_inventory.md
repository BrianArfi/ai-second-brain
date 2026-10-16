# Gates inventory: hook-only or in the tool?

Written 15 Oct 2026 for item B4 of the Grok Bot lessons plan (`inbox/2026-10-15-grokbot-lessons-plan.md`). Second pass the same day: gaps 1, 3 and 4 closed, gap 2 narrowed to the exact lines still open outside this repo.

**The rule.** A gate that protects an outbound action belongs in the script that does the action, the way `--approved` already does. A Claude Code hook only fires while Claude Code is the harness. Under agy-bridge, opencode, a cron job or a member's setup, a hook-only gate does not exist.

**The three verdicts.**

| Code | Meaning |
| :--- | :--- |
| (a) | Already enforced inside the sending script. |
| (b) | The check moves into the script. The hook keeps working and calls the same function. |
| (c) | Stays hook-only, with a reason. |

**How (b) is built.** The logic sits in `.agent/scripts/gates/`. `gates/outbound.py` holds the em-dash and Fathom link checks. `gates/claims.py` holds the reply-router conversation claim. `gates/handles.py` holds the Slack handle gate. The hook and the script both import them, so the wording, the decision and the override are one piece of code. Tests: `tests/test_gates_in_tools.py` (69 tests, network mocked, each refusal proves zero network calls) plus the existing hook tests, which still pass.

**Override rule.** A script bypass is the hook's own override, read from the environment: `SLOP_GUARD_ALLOW_EMDASH=1`, `SLOP_GUARD_ALLOW_FATHOM_CALLS=1`, `SLOP_GUARD_ALLOW_PLAIN_NAMES=1`, `CLAIM_GUARD_DISABLE=1`. Written as a prefix on the command, the hook sees the text and the script sees the variable. Only the exact value `1` counts. `slack_client.py` refuses to load any of the four from `token.env`, the same way it already refuses `SLACK_SEND_UNATTENDED`, so a credentials file cannot un-gate a send. There is no new bypass flag. The one thing that is new is `--approved` on `gmail_manager.py send`, and it has no environment bypass at all, like the Slack one.

## 1. Hook-by-hook table

| Guard | Event and matcher | What it protects | Verdict | Enforced where now | Override | Reason or note |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `send_slop_guard.py`: em-dash and en-dash | PreToolUse, Bash | Text leaving the machine to a person | **(b) done** | `slack_client.py` post, update, upload `--comment` (both connectors); `gmail_manager.py` `send_email` body and subject; `gdoc_comment.py` `comment`; `linear_client.py` create, update, comment and `jira_client.py` create, edit, comment (`gates.outbound.enforce_dashes`, 15 Oct 2026); `gates/outbound.py check` for WhatsApp helpers | `SLOP_GUARD_ALLOW_EMDASH=1` | Hook behaviour unchanged. Gate runs before the first network call. |
| `send_slop_guard.py`: internal `fathom.video/calls/` link | PreToolUse, Bash | Recipient cannot open the link | **(b) done** | Same call sites as above | `SLOP_GUARD_ALLOW_FATHOM_CALLS=1` | Cache lookup for the share link is local and read-only. |
| `send_slop_guard.py`: unreadable `--text-file` | PreToolUse, Bash | Draft written and sent in one Bash call | **(c)** | Hook only | none | A property of PreToolUse timing. The script reads its own file at send time, so the problem does not exist in the tool. |
| `send_slop_guard.py`: Slack handle gate | PreToolUse, Bash | A named person gets a ping | **(b) done** | `slack_client.py` post and update in the primary (Work) connector, via `gates/handles.py` | `SLOP_GUARD_ALLOW_PLAIN_NAMES=1` | Refuses a resolvable name with no `<@ID>`, before any network call, and prints the exact `<@ID>` to paste. A name matching two live accounts warns and passes. A missing or broken people index passes. **Warn-only for unattended senders**: `chase_queue.py send` and the dashboard click-send set the same override on the child process and print the finding first (see gap 4). The secondary (Secondary) connector stays hook-only, because the people index is a Work workspace index and its ids are not valid in the Secondary workspace. |
| `send_slop_guard.py`: slop words, phrases, word budget, voice, extra-line | PreToolUse, Bash | Quality of the draft | **(c)** | Hook only | n/a | Advisory (`additionalContext`), never a block. Needs judgment, and a warning must not stop an unattended script. |
| `conversation_claim_guard.py` | PreToolUse, Bash | Two sessions replying into one Slack thread | **(b) done** | `slack_client.py` post and upload (both connectors) via `gates/claims.py` | `CLAIM_GUARD_DISABLE=1` | Needs `CLAUDE_CODE_SESSION_ID` to know who "me" is. Without it (cron, agy-bridge) the check is skipped, because the claim only exists between Claude sessions. The hook's `ask` for a recently closed conversation becomes a stderr warning: under `bypassPermissions` an `ask` never reaches a prompt, so a warning is the same strength. |
| `slack_send_guard.py` | PreToolUse, `mcp__*Slack*__*(post\|send\|reply)*` | Slack send through the MCP tool | **(c)** | Hook only | none | There is no script to host the check: the tool is an MCP call. CLAUDE.md already forbids the MCP send tools (they post as the Claude bot). The hook stays as the backstop. |
| `resource_claim_guard.py` | PreToolUse, Bash | Two sessions on one Doc, ticket or file | owned by the claims stream (A5) | n/a here | set by that stream | Listed so the inventory matches `settings.json`. Its in-tool half belongs to the same stream. |
| `emdash_guard.py` | PostToolUse, Write and Edit | An em-dash in a repo `.md` or `.txt` | **(c)** | Hook only | none | Warns on a file edit. It is not an outbound action. The same text is gated again, hard, when it is sent. |
| `link_guard.py` | PostToolUse, Write and Edit | Dead local links in a repo `.md` | **(c)** | Hook only; also runs standalone | `--fix` | Warns on file content. For published Docs, `readability_gate.py` (R1 to R4) already runs inside `publish_prd.sh`, which is (a). |
| `reply_draft_guard.py` | PostToolUse, Write and Edit | A reply draft must quote the original | **(c)** | Hook only | none | Lints a draft file in `journal/drafts/`. The draft is not the send. `reply-queue` and `reply-router` build the three-part format themselves. |
| `artifact_publish_guard.py` | PostToolUse, Write and Edit | HTML under `Clients/` handed over as a URL | **(c)** | Hook only | none | A reminder about how to hand a file over, not a gate on an action. |
| `ledger_guard.py` | Stop | Ledger records propagated and recorded at turn end | **(c)** | Hook only | `ledger_sync.py` kill switches | A turn-end check. The ledger CLIs already write under the lock and propagate in the same call, which is the (a) part. |
| `ledger_watch.py` | PostToolUse, Bash, Write, Edit, Atlassian and Linear tools | Notices discharge actions and hand edits | **(c)** | Hook only | none | An observer. It records, it never refuses. |
| `ledger_freshness.py` | PreToolUse, Bash | Reading a stale ledger | **(c)** | Hook only | none | Warning plus a throttled fetch. `ledger_sync.py refresh` is the manual form. |
| `draft_lint_stop.py` | Stop | Drafts shown in chat | **(c)** | Hook only | none | A chat reply has no tool call. Nothing to move into. |
| `agent_spend_guard.py` | PreToolUse, Agent and Task | Subagent spend | **(c)** | Hook only | none | Guards the Agent tool, which has no script. Warns, never blocks. |
| `launchd_guard.py` | PreToolUse, Bash | `launchctl submit` loops | **(c)** | Hook only | none | Blocks a command pattern, with no script of ours to host a check. |
| `worktree_commit.py`, `session_git_sync.py`, `dashboard_context.py`, `wib_clock.py`, `glm_mode.py`, `routing_mode.py`, `upstream_check.py`, `retired_commands_guard.py` | Stop and SessionStart | Session lifecycle and context | **(c)** | Hook only | various | Not gates on an outbound action. |

## 2. Gates that were already in the tool (a)

| Action | Gate | Where |
| :--- | :--- | :--- |
| Slack post, update, delete, upload, invite, create or rename channel | `--approved`, no environment bypass | `require_send_approval` in `.agent/scripts/file_utils.py`, called by both Slack connectors |
| Slack message over 4000 characters | Refuses a silent split | `check_length` in `slack_client.py`, override `--allow-split` |
| Gmail send | `--approved`, no environment bypass | `require_send_approval` in `.agent/scripts/file_utils.py`, called at the top of `send_email` in `gmail_manager.py` and by the `send` command (15 Oct 2026) |
| Linear create, update, comment, role change | `--approved` | `linear_client.py` |
| Jira create-issue, edit-issue, comment | `--approved` | `jira_client.py` |
| Drive, Docs create and update | File id must come back | `assert_drive_result` in `file_utils.py` |
| Drive sharing | Domain by default, revokes anyone-with-link | `apply_visibility` in `file_utils.py` |
| Doc replace | Refuses when the Doc is newer than the local file | `drive_update`, override `GDOCS_ALLOW_STALE_OVERWRITE=1` |
| Doc publish readability | R1 to R4 | `readability_gate.py` inside `publish_prd.sh` |
| WhatsApp send | `WA_SEND_MODE` (disabled, approval, direct) | The WhatsApp bridge (`safety.py`), outside this repo |

## 3. Gaps this inventory found

| # | Gap | Proposed fix | Status |
| :--- | :--- | :--- | :--- |
| 1 | `gmail_manager.py send` had **no approval flag**. The two-turn approval for third-party email lived in CLAUDE.md and in `send_slop_guard.py` only as text checks. Any harness that ran the script sent. | `--approved` on `send`, using `require_send_approval` (wording says Gmail instead of Slack, rule identical). | **Closed 15 Oct 2026.** the owner approved. `send` refuses before any network call, `send_email(..., approved=False)` refuses too, so a programmatic caller is gated as well. No environment bypass. `SKILL.md` and the CLAUDE.md email rule now show `--approved` as the post-approval step. The second flag for third-party recipients (`--approved-final`) was **not** added: one flag keeps the same shape as Slack, and the two-turn rule stays a process rule the operator follows before adding `--approved`. |
| 2 | WhatsApp helpers live outside this repo. PSB has no WhatsApp send helper (checked 15 Oct 2026: no `send` code for WhatsApp anywhere under `.agent`, `.claude`, `scripts` or `dashboard`). | `gates/outbound.py check --origin whatsapp --text-file <draft>` exits 1 on a dash or an internal Fathom link. | CLI built and tested. **Bridge not patched, by instruction.** What remains is listed under "WhatsApp: exactly what remains" below. |
| 3 | The em-dash ban was not enforced on Linear and Jira text. The hook never covered them either. | `gates.outbound.enforce_dashes` at the top of each write path. | **Closed 15 Oct 2026.** Linear: `create_issue` (title, description), `update_issue` (title), `comment` (body). Jira: `create_issue` (summary, description), `edit_issue` (summary, description, also on `--dry-run`, so the preview the owner approves is already clean), `comment_issue`. Runs after the `--approved` check, before the first network call. Override `SLOP_GUARD_ALLOW_EMDASH=1`. Dashes only: the Fathom link check is not applied to tickets. MCP-tool writes (`mcp__linear__*`, Atlassian MCP) never touch these scripts and stay ungated. |
| 4 | Slack handle gate was hook-only. | Move beside `post_message`, sharing the hook's logic. | **Closed 15 Oct 2026** for the primary connector (`gates/handles.py`; the hook now imports the same functions). Unattended senders checked first: `chase_queue.py send` posts drafts like "Hi Teammate, following up on ..." with the recipient named in plain text and no `<@ID>`, and it can run from cron (`auto_send_ok`), so a hard block would have turned it into failed sends. It is **warn-only**: it prints `[WARN] ... names Teammate without a Slack handle` per bundle, then runs the child with `SLOP_GUARD_ALLOW_PLAIN_NAMES=1`, the hook's own override. The dashboard click-send (`POST /api/inbox-send`) does the same and logs the warning to `server_stderr.log`, because the drawer has no way to apply an override. `reply_router.py` does not send. Nothing else posts through `slack_client.py` unattended. If the chase queue should ping people, the follow-up is to resolve `user_id` into the draft with `slack_mentions.apply_mentions` at build time, then drop the override. |
| 5 | Programmatic callers that import `post_message` directly (none today) would now exit on a dash. | Documented in `gates/__init__.py`. Every current caller runs the script as a subprocess, where exit 1 is the intended result. | Accepted. |

## 4. What changed in code

| File | Change |
| :--- | :--- |
| `.agent/scripts/gates/__init__.py`, `outbound.py`, `claims.py`, `handles.py` | New. The shared gate logic and a small CLI. `outbound.enforce_dashes` and `handles.py` were added in the second pass. |
| `.claude/hooks/send_slop_guard.py` | Em-dash and Fathom code replaced by calls into `gates.outbound`; Slack handle code replaced by calls into `gates.handles`. Output text and override identical. |
| `.claude/hooks/conversation_claim_guard.py` | Decision logic replaced by `gates.claims.evaluate`. `STATE`, `main()` and the output are unchanged. |
| `.agent/skills/slack-connector/scripts/slack_client.py` | Gate in `post_message`, `update_message`, upload comment; claim check on post and upload; handle gate on post and update; override names added to the `token.env` deny list. |
| `.agent/skills/secondary-slack-connector/scripts/slack_client.py` | Same gate and claim check on post. |
| `.agent/skills/gmail-connector/gmail_manager.py` | `--approved` required (`require_send_approval`), then the text gate, both at the top of `send_email`, ahead of authentication. |
| `.agent/scripts/file_utils.py` | `require_send_approval` takes an optional `destination` label so the refusal says Gmail. Default and wording for Slack unchanged. |
| `.agent/skills/gmail-connector/SKILL.md` | Documents `--approved` as mandatory; every example command carries it. |
| `.agent/skills/linear-connector/scripts/linear_client.py`, `.agent/skills/jira-connector/scripts/jira_client.py` | `enforce_dashes` on every write path (gap 3). |
| `.agent/skills/chase-queue/scripts/chase_queue.py`, `dashboard/server.py` | Warn-only handle gate for unattended senders (gap 4). |
| `.agent/skills/gdoc-comment/gdoc_comment.py` | Gate before the browser launches. |
| `tests/test_gates_in_tools.py` | New. 69 tests after the second pass (Gmail approval, Slack handle gate, warn-only senders, Linear and Jira dashes). |
| `tests/test_conversation_claim_guard.py` | One stale test corrected: it expected a top-level post to be blocked by an open thread, which the 28 Sep 2026 fix removed on purpose. It now tests a channel-level conversation, and asserts the thread case passes. |

## 5. WhatsApp: exactly what remains (checked 15 Oct 2026)

**Where the bridge really is.** `~/wa-bridge` does not exist on the WSL host. The working copy is `~/wa-for-pm` (remote `github.com/you/wa-for-pm`; its tree has uncommitted work on `whatsapp-bridge/main.go` and pairing scripts, which is another stream's). The ASB app bundles a second copy at `~/repos/ai-second-brain-app/src-tauri/resources/whatsapp-mcp-server` (the path is under `src-tauri/`, not `resources/` at the top). The two copies differ in `main.py` and `pyproject.toml`, and the `~/wa-for-pm` copy has `approve.py` and tests the bundle lacks. Neither was edited.

**What is already enforced there.** `safety.check_send(kind, recipient, payload)` in `whatsapp-mcp-server/safety.py` (line 123 in both copies) is the single gate every send passes through, and it applies `WA_SEND_MODE` (disabled, approval, direct). It is called from `whatsapp.py` in `send_message` (line 1072 to 1074), `send_file` (1111 to 1113), `send_audio_message` (1149 to 1151 in the bundle, 1152 to 1154 in `~/wa-for-pm`) and `send_reaction` (1212 to 1214 in the bundle, 1215 to 1217 in `~/wa-for-pm`).

**What is missing.** `check_send` never looks at the text. A dash or an internal Fathom link in a WhatsApp message goes out, or is staged for approval, unchecked.

**The change, for the stream that owns that code.** In `safety.check_send`, before the mode switch and only when `kind` is `"message"` or `"file"` (the only kinds that carry text: `payload["message"]` and, in the `~/wa-for-pm` copy, `payload["caption"]`), call the shared gate and return `(False, reason)` on a refusal:

1. Preferred: vendor `gates/outbound.py` into the bridge package (it imports only the standard library) and call `outbound.refusals(text, "whatsapp")`. Refuse with `(False, "\n".join(reasons))`. Do not call `outbound.enforce`: it exits the process, which would kill the MCP server.
2. Or shell out: `python3 <PSB>/.agent/scripts/gates/outbound.py check --origin whatsapp --text-file <tmp>`, exit 1 means refuse. Works only on a host that has the PSB checkout, so it is wrong for the bundled copy that ships to other members.

Then add a test beside `tests/test_whatsapp.py` that asserts a dash returns `(False, ...)` with zero calls to the bridge, and that `SLOP_GUARD_ALLOW_EMDASH=1` lets it through. The ASB copy needs the same change in `src-tauri/resources/whatsapp-mcp-server/safety.py`, in its own commit, because the app ships that folder.
