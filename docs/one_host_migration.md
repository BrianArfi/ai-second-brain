# One host: make WSL the only writer

Written 15 Oct 2026 for item B5 of the Grok Bot lessons plan (`inbox/2026-10-15-grokbot-lessons-plan.md`). **This is a plan. Nothing here has been run, and no setting, cron entry or machine setup was changed.** The pre-flight is a read-only script: `python3 .agent/scripts/one_host_check.py`.

## 1. The problem, with today's numbers

CLAUDE.md says two things that do not match what runs:

- WSL (`.`) is the automation host, and `origin/main` is the source of truth.
- The Windows checkout is "retired, proxy-only".

The ASB desktop app does not follow that. Its `workspace.json` and `ASB_WORKSPACE` both point at `C:\Users\the owner\.gemini\antigravity\scratch\product-second-brain`, so every app session writes there. The dry-run on 15 Oct 2026 found:

| Finding | Value |
| :--- | :--- |
| App opens | The Windows checkout, through `ASB_WORKSPACE` |
| Commits touching `journal/state` in the last 7 days | 97 from the Windows git identity, 417 from the WSL or macOS identity |
| State files whose last writer is the Windows identity | 6 of 48 (`reply_router_state`, `reply_queue`, `initiatives`, `job_acks`, `people`, and the per-machine `windows-desktop-*` file) |
| Local-only work on the Windows checkout | 1 unpushed commit, 12 uncommitted files, 11 stashes |
| Local-only work on the WSL checkout | 28 uncommitted files (3 in `journal/state`), 8 stashes |
| Speed of git over the `\\wsl.localhost` share | 5 minutes 35 seconds for the report. The same report through `wsl.exe` took 5 seconds. |

The last row is the main technical risk of the obvious fix, and section 4 deals with it.

Two writers means three known failure modes: the ledger lost-update (`ledger_sync.py` already carries a deletion guard for it), a reply sent from the wrong checkout, and the 5 Oct style case where a file the owner opens exists in only one checkout.

## 2. Target state

| Item | Target |
| :--- | :--- |
| Only writer of `journal/state/*.json`, cron, and ledger CLIs | WSL |
| ASB app workspace | A WSL path, reached by one of the options in section 4 |
| `ASB_WORKSPACE` on Windows | Unset, or the WSL path |
| Windows checkout | Read-only mirror kept 30 days, then deleted. Nothing writes to it. |
| macOS checkout | Interactive, no cron (unchanged) |
| Proof | `one_host_check.py` prints `VERDICT: READY` on two consecutive days |

## 3. The dry-run checker

`.agent/scripts/one_host_check.py`. Read-only: it runs `git rev-parse`, `rev-list`, `status`, `log`, `ls-files`, `stash list` and reads `workspace.json`. It commits, pulls, pushes and writes nothing. `--fetch` is opt-in and refreshes remote-tracking refs only.

| Report section | Question it answers |
| :--- | :--- |
| 1. Checkouts | Which checkouts (this one, WSL, macOS, and every app-registered workspace that holds a `work_tree.json`) have commits `origin/main` has not seen, uncommitted `journal/state` files, or stashes? |
| 2. Who last wrote `journal/state/*.json` | For each tracked state file, the last committer identity and the machine that implies. Git stores an identity, not a hostname. The two identities in use are mapped in `IDENTITY_LABELS` (`you@example.com` is the Windows checkout, `you@example.com` is WSL or macOS). Override with `--identity email=label`. WSL and macOS cannot be told apart by identity. |
| 3. ASB app workspace | What the app will open: `ASB_WORKSPACE` wins over `workspace.json`, as in `src-tauri/src/workspace.rs`. Flags a BOM in `workspace.json`, which the app rejects. |
| 4. Findings and verdict | `READY` only when no high finding is open. `--strict` turns `NOT READY` into exit code 1 so cron or CI can use it later. |

Run it on Windows (it reaches WSL through `wsl.exe`), on WSL, and on the Mac. Each machine can only see its own app config.

## 4. Options for pointing the app at WSL

| Option | How | Cost | Verdict |
| :--- | :--- | :--- | :--- |
| A. UNC path | Add `\\wsl.localhost\Ubuntu\home\you\antigravity-projects\product-second-brain` as a workspace, or set `ASB_WORKSPACE` to it | One config line. But every file read crosses the 9p share, and git over it measured 66 times slower. The Claude CLI on Windows also cannot use a UNC path as its working directory in some shells. | Pilot only, to measure. Do not make it the default before the speed test passes. |
| B. Run the sessions inside WSL | The desktop app launches the CLI through `wsl.exe` with the WSL path as cwd | Needs a change in the app (another stream's repo). No speed cost, hooks run on Linux Python, paths are native. | **Recommended.** It matches the plan's "Windows becomes a client". |
| C. Remote only | Windows desktop and phone act as clients of a session host running on WSL (the relay) | Biggest build. Overlaps plan items C4 and D. | Later. |

**Recommendation:** run A as a one-day pilot to learn what breaks, ask the app stream for B, and move the default only when B exists. Until then, the safe interim step is to stop the Windows checkout writing state (section 5, step 2).

## 5. Steps

Nothing below has been run. Each step lists its check.

| # | Step | Command or action | Check |
| :--- | :--- | :--- | :--- |
| 0 | Baseline | `python3 .agent/scripts/one_host_check.py --fetch` on Windows, WSL and the Mac | Save the output. All three show the same `origin/main` head. |
| 1 | Land local-only work | On the Windows checkout, commit and push the unpushed commit; triage the 11 stashes (apply and commit, or write down and drop); move the untracked `inbox/` videos somewhere durable. Same for the 3 uncommitted state files on WSL, through the ledger CLI (`ledger_sync.py sync --reason ...`), never by hand. | Section 1 shows ahead 0, uncommitted state 0, on every checkout. |
| 2 | Freeze Windows writes | On Windows set two user variables: `WORKTREE_SYNC_DISABLE=1` (stops the end-of-turn auto-commit in `worktree_commit.py`) and `LEDGER_SYNC_DISABLE=1` (stops `ledger_sync.py` committing and pushing ledger changes; the ledger CLIs still write the local file, so use the WSL checkout for ledger commands from now on). `ledger_guard.py` stays registered; with the sync off it can only block a turn once to ask for a missing record. | New Windows commits touching `journal/state` stop. Section 2 recent-writer count for `windows` falls to 0 within a day. |
| 3 | Back up the app config | Copy `%APPDATA%\com.aisecondbrain.desktop\workspace.json` and `sessions.json` to a dated folder. `harness_migrate.py` already knows these files. | Backup opens as valid JSON. |
| 4 | Pilot (option A) | Set `ASB_WORKSPACE` to the WSL UNC path for one app session only. Do real work for one hour: a PRD edit, a ledger command, a reply draft. | Note every error. Compare git speed. Run `link_guard.py` from the session's own checkout. |
| 5 | Switch | When option B ships, or when the pilot is fast enough: set the active workspace to the WSL path. If the file is edited, write it **without a BOM** (the app rejects a BOM and drops to onboarding). Remove the `ASB_WORKSPACE` user variable. | Section 3 prints `canonical: True`. |
| 6 | Re-link per-workspace state | See "What breaks", rows 1 and 2. | A new session sees memory and `MEMORY.md`. |
| 7 | Retire the Windows checkout | Rename the folder (for example to `product-second-brain.retired`), do not delete. Keep 30 days. | `one_host_check.py` reports it as unreachable or read-only. |
| 8 | Prove it | Run `one_host_check.py --strict` daily for two days. | `VERDICT: READY` twice. |

## 6. What breaks, and the fix

| # | What breaks | Why | Fix |
| :--- | :--- | :--- | :--- |
| 1 | Session history and auto-memory look empty | Claude Code names its project folder from the workspace path. A new path means a new `~/.claude/projects/<slug>/`, and the `memory/` symlink to `journal/memory/` does not exist there. | Recreate the symlink for the new slug. Old transcripts stay on the Windows profile and `session_ref.py` cannot see them from the new slug (it already says transcripts are per machine). |
| 2 | `session_cards.py` and `session_ref.py` | They resolve the workspace through `ASB_WORKSPACE` then `CLAUDE_PROJECT_DIR`. | Fine once the variable is unset or correct. Check with `session_ref.py here`. |
| 3 | Absolute links in documents | Links written for the Windows session start with `C:/Users/...`. | New documents get the WSL form. `link_guard.py --fix` repairs safe classes. Old documents keep working only while the Windows folder exists, which is one more reason to keep it 30 days. |
| 4 | Files the owner opens | A file is written to the checkout the session runs in (CLAUDE.md). After the switch that is WSL. | Hand over `\\wsl.localhost\...` paths, or use `bash .agent/scripts/artifact_link.sh <path>`. |
| 5 | Slow reads and writes over the share (option A only) | 9p file system. Measured: git status plus log over the share 5 min 35 s, through `wsl.exe` 5 s. | Option B. Do not run option A as the long-term default. |
| 6 | Hooks call `python3` | On Windows that resolves to `C:\tools\bin\python3.exe`. Inside WSL it is the Linux Python. | No change needed for option B. For option A, the Windows `python3` runs the hooks against a UNC path, so test `ledger_freshness.py` and `link_guard.py` in the pilot. |
| 7 | Windows-only helpers | Anything that needs the Windows desktop session: the AMD GPU path, the app-managed WhatsApp bridge on port 8181, a recorder that captures Windows audio. | List them in the pilot. `story-time` already renders in WSL. The meeting recorder runs from the WSL cron host per `reference_meeting_recorder_stack`; confirm the Windows audio capture, if used, still has a path. |
| 8 | Per-machine state files | `slack_push.macos-*.json`, `windows-desktop-*.json`. | Leave them. They exist so machines do not write the same file. The Windows one goes quiet after step 2. |
| 9 | CRLF churn | The Windows checkout carries line-ending noise (renormalised 19 Sep). | Disappears with the checkout. |
| 10 | Two machines still editing the same Google Doc | Out of scope: Drive is its own source of truth and has its own revision guard. | None. |

## 7. Rollback

Every step is reversible, because the Windows checkout is renamed rather than deleted and WSL is never changed destructively.

| After step | Undo |
| :--- | :--- |
| 2 | Remove `WORKTREE_SYNC_DISABLE` and `LEDGER_SYNC_DISABLE`. The next end-of-turn sync commits and pushes as before. |
| 4 | Unset `ASB_WORKSPACE`. |
| 5 | Copy the step 3 backup of `workspace.json` back (no BOM), set `ASB_WORKSPACE` to the Windows path if it was set before, restart the app. |
| 6 | Delete the new memory symlink; the old slug still holds its data. |
| 7 | Rename the folder back. Run `git pull --ff-only` first, because it will have fallen behind. |

If a ledger write went to the wrong checkout during the pilot, do not hand-edit the JSON. Run `python3 .agent/scripts/ledger_sync.py refresh` on WSL, then re-run the missed ledger command there. The deletion guard in `ledger_sync.py` exists for this case.

## 8. Decisions for the owner

1. **Is option B (sessions run inside WSL) the target?** Recommendation: yes. It needs one change in the app repo, and it removes the speed risk.
2. **May the Windows checkout stop auto-committing now (step 2)?** Recommendation: yes. It is the cheapest step, it is reversible by deleting two variables, and it stops new Windows writes to the ledgers today.
3. **Keep the retired Windows folder how long?** Recommendation: 30 days.

## Limits of this analysis

- The macOS checkout could not be reached from this machine, so its numbers are missing. Run the checker there.
- Machine attribution is inferred from the git identity. WSL and macOS share one identity.
- "Does the app launch the CLI inside WSL?" was not verified in the app source. It is the open question for option B.
- The speed figure is one run on 15 Oct 2026, not a benchmark.
