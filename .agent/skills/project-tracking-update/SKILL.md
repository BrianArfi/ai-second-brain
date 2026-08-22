---
name: Project Tracking Update
description: The "Triple-Check" protocol for keeping Dashboard.md, todo.md, and the PM ledgers (commitments/waiting_on/decisions) perfectly synchronized. Prevents "hanging" tasks and ensures external dependencies are never forgotten.
---

# Project Tracking Update Skill

This skill defines the **mandatory** procedure for updating the project's source-of-truth documents. It runs whenever:
- A task is completed
- A status changes
- New information arrives that affects tracking
- An external dependency is resolved or becomes overdue

**Goal**: Zero drift between `Dashboard.md`, `todo.md`, and the PM ledgers. No completed work left appearing active. No overdue external item left unmarked. **Special focus on Management Mandates (YourManager/P0) and Team Capacity.**

> [!IMPORTANT]
> **`journal/master_followup_tracker.md` is a GENERATED FILE (since 2026-07-24), not a document you edit.**
> It is rendered by `.agent/skills/project-tracking-update/scripts/render_followup_tracker.py` from the three
> PM ledgers below. "Update the tracker" means updating a ledger with the CLI, then (optionally) re-running
> the renderer - never opening the markdown file and typing into it. The daily runner (`daily_update_runner.py`,
> both modes) already re-renders it mechanically, so most of the time no manual regenerate is needed.
>
> | What changed | Ledger + command |
> |:---|:---|
> | the owner owes someone something (new or done) | `commitment_ledger.py add \| close <COM-id> --note "..."` |
> | Someone owes the owner something (new or done) | `waiting_watchdog.py add \| close <WAIT-id> --note "..."` |
> | A decision was made or an open question closed | `decision_log.py add \| decide <DEC-id> --decision "..."` |

> [!IMPORTANT]
> **Same turn as the action, not later (added 2026-08-11).** The trigger for updating a record is the *action* -- the Slack message sent, the doc published, the ticket transitioned -- not a later tidy-up pass. Other sessions and 19 cron jobs read the ledgers, never this conversation, so an action with no record behind it keeps reporting as open and gets chased again.
>
> **Propagation is automatic; do not do it by hand.** Since 2026-08-11 all four ledger CLIs call `.agent/scripts/ledger_sync.py` on the way out, which re-renders `journal/state/*.index.json` and `master_followup_tracker.md`, then commits and pushes. One CLI call is the whole chain. Step 5 below is therefore normally a no-op.
>
> Enforced by three hooks: `ledger_freshness.py` (pull before reading), `ledger_watch.py` (flags hand-edits and tracked actions), `ledger_guard.py` (syncs at end of turn, blocks once on an action with no record). Full rule: `CLAUDE.md` -> `## Ledger Discipline`.

---

## The "Triple-Check" Protocol

When a task is completed or a status changes, perform ALL 7 steps in order.

### Step 1: Update the Daily Briefing & Priorities
- **Target**: `Dashboard.md` -> `## ☀️ Daily Briefing & Priority`
- **Actions**:
    - Mark the specific task checkbox as `[x]`.
    - If the task was blocking others, identify the *next* priority and add it.
    - Check the "Advisor's Note" section - does it still reflect reality? If the gap was just closed, rewrite it.
    - Update the `*(Last Updated: ...)*` timestamp.

### Step 2: Update the Daily Change Summary
- **Target**: `Dashboard.md` -> `## 📊 Daily Change Summary`
- **Actions**:
    - If today's summary section doesn't exist yet, create one: `## 📊 Daily Change Summary -- [Client] (Month DD, YYYY)`
    - Add the completed task: `- [x] **[Project Name]** Task Description`
    - If new files were created, add them under `**Created/Updated Master Docs**`

### Step 3: Update the Project Status Table
- **Target**: `Dashboard.md` -> `## Active Projects` -> [Specific Project]
- **Actions**:
    - Update **backlog checkboxes** (`[x]` for completed items)
    - Update the **Status** emoji if the project phase changed
    - If a milestone is done, advance to the next phase name

### Step 4: Synchronize Personal Todo
- **Target**: `journal/todo.md`
- **Actions**:
    - **Search broadly**: `grep` for key terms of the completed task across the entire file - it may appear under "Pekan Ini", "Immediate Priorities", "Active Tasks", or a project subsection.
    - **Mark ALL instances** as `[x]`. Tasks often appear in multiple sections.
    - **Consistency**: Ensure the wording matches the completion status in `Dashboard.md`.

### Step 5: Synchronize the PM Ledgers (CRITICAL)
- **Target**: `journal/state/commitments.json`, `journal/state/waiting_on.json` (via their CLIs - never edit the JSON by hand)
- **Actions**:

    #### For Self-Tasks (the owner completed something):
    - Find the item: `commitment_ledger.py report` (or `--all` to include closed), grep for the COM-id or task text.
    - Close it: `commitment_ledger.py close <COM-id> --note "<what happened, with evidence link>"`.
    - If this task was a prerequisite for an external follow-up, mention that in the close note.

    #### For External Tasks (Someone delivered something the owner was waiting on):
    - Find the item: `waiting_watchdog.py report`, grep for the WAIT-id or owner name.
    - Close it: `waiting_watchdog.py close <WAIT-id> --note "<evidence>"`.
    - **Cascade check**: Does this unblock a new self-task for the owner? If yes, `commitment_ledger.py add` it.

    #### Overdue Detection (Run during every update):
    - `waiting_watchdog.py report` already sorts breached items to the top with age and an ESCALATE flag - this IS the overdue scan, don't re-derive it by hand.
    - **YourManager Mandate Check**: If a breached item is a YourManager/Management mandate, move it to the TOP of the Dashboard priorities and add a 🚨 emoji.
    - Surface breached items in the Dashboard Advisor's Note / today's block.

    #### Regenerate the view: not needed any more.
    - The CLIs above already re-rendered the indexes and the tracker and pushed them, so every other session and cron job is already reading the new value. `render_followup_tracker.py` by hand is only for recovering from a failed sync.
    - To confirm it landed: `python3 .agent/scripts/ledger_sync.py check` (exit 0 = ledgers, derived views, and origin/main agree).

### Step 6: Update Team Workload & Design reporting
- **Target**: `Dashboard.md` -> `Advisor's Note` or a specific `Team Health` section.
- **Actions**:
    - If a task involves Teammate (Platform) or Teammate (Marketplace), update the "Team Balance" context in the Advisor's note.
    - If a task involves the Design team (Mark/Teammate), ensure the owner's direct oversight is reflected in the next steps.

### Step 6: Synchronize Project Backlog (If Applicable)
- **Target**: `Clients/[Client]/[Product]/backlog.md`
- **Actions**:
    - Find and mark the task as completed.
    - If the backlog has a priority ranking, check if priorities need reshuffling.

### Step 7: Final Verification (The "Hang" Check)
- **Target**: ALL tracking files
- **Actions**:

    1. **Grep for Duplicates**: Search for key terms of the completed task across `Dashboard.md`, `todo.md`, and `commitment_ledger.py report` / `waiting_watchdog.py report` output. Ensure no unchecked duplicates remain in either place.
    
    2. **Conflict Check**: Verify consistency:
        - Dashboard says "Done" -> todo.md must also say "Done"
        - A ledger item is closed -> todo.md must also say `[x]`
        - No file should say "In Progress" while a ledger says closed
    
    3. **Orphan Check**: Look for tasks in `todo.md` that reference external people (Gaith, ExampleVendor, Teammate, etc.) but are NOT tracked in `waiting_watchdog.py report`. If found, add them: `waiting_watchdog.py add --owner "..." --what "..." --sla-hours <n>`.
    
    4. **Staleness Check**: `waiting_watchdog.py add` requires `--sla-hours` at creation, so new items can't go untracked-for-staleness by construction. If a ledger item genuinely has no useful SLA (rare), default to **3 business days** (72h) when adding it.

    5. **Propagation Check**: `python3 .agent/scripts/ledger_sync.py check`. Exit 0 means the ledgers, the generated views, and `origin/main` all agree, so every other session reads what this one just wrote. Exit 1 names what is still local-only. This is the one that catches "I updated the record and it never left my working tree."

---

## When to Invoke This Skill

| Trigger | Action |
|:---|:---|
| the owner says "done", "completed", "shipped" | Full 7-step protocol |
| the owner uploads a PRD or document | Steps 2, 3, 6 (add to summary, update project, update backlog) |
| Meeting summary processed | Steps 4, 5 (extract tasks to todo and tracker) |
| `/daily-update` executed | Step 5 ledger sync + Step 7 full verification (tracker view re-renders automatically) |
| the owner mentions someone else's name + action | Step 5 only (add external follow-up) |
| Status change mentioned | Steps 1, 3, 4 (briefing, project table, todo) |

---

## Example Scenarios

### Scenario A: Task Completed - "RBAC Requirements Finalized"

1. **Dashboard Priorities**: Mark `[x] Review RBAC Requirements`.
2. **Dashboard Summary**: Add `- [x] **[Work Seller]** RBAC Requirements Finalized`.
3. **Project Table**: Update Seller Portal backlog. Next Action: "Order Fulfillment Workflow".
4. **todo.md**: Search "RBAC" - found in 2 places. Mark both `[x]`.
5. **Ledger**: Was RBAC blocking an external item? Check `waiting_watchdog.py report` / `commitment_ledger.py report`, close if resolved.
6. **Backlog**: Mark RBAC done in `Clients/Work/Seller Portal/backlog.md`.
7. **Verification**: Grep "RBAC" across `Dashboard.md`, `todo.md`, and the ledger reports. Found an old `P0: RBAC` in the backlog? Mark it `[x]` too. No conflicts found.

### Scenario B: External Dependency Resolved - "Gaith delivered the roadmap reframe"

1. **Dashboard**: Update Advisor's Note to reflect that the blocker is resolved.
2. **Dashboard Summary**: Add `- [x] **[Work]** Gaith delivered Q2 roadmap reframe`.
3. **Project Table**: Update milestone in relevant Work section.
4. **todo.md**: Find "Follow up Gaith on Q2 Roadmap reframing" and mark `[x]`.
5. **Ledger**:
    - `waiting_watchdog.py close <WAIT-id> --note "Gaith delivered the Q2 roadmap reframe, <link>"`.
    - If it unblocks a new self-task, `commitment_ledger.py add ...` for "Review Gaith's reframe and provide feedback".
6. **Backlog**: N/A (strategic item, not engineering task).
7. **Verification**: Grep "Gaith" and "roadmap" in `waiting_watchdog.py report --all` and `todo.md` - ensure no stale references remain active.

### Scenario C: Overdue Detection during Daily Update

1. `python3 .agent/skills/waiting-watchdog/scripts/waiting_watchdog.py report` - breached items already sort to the top with age and an ESCALATE flag, this is the scan.
2. Found: Gaith's "Reframe roadmap" shows 🚨 ESCALATE, breached 2 days.
3. Nothing to hand-edit in the ledger for this - the status is computed from `since` + `sla_hours` on every report run.
4. Update Dashboard Advisor's Note: "🔴 1 overdue: Gaith's roadmap reframe (breached 2d). Suggest Slack follow-up."
5. No changes needed in todo.md or backlog (task not completed, just flagged).

---

## Anti-Patterns to Avoid

| Bad Pattern | Why It's Bad | Correct Behavior |
|:---|:---|:---|
| Updating only `todo.md` | Dashboard and the ledgers get out of sync | Always update Dashboard + todo.md + the relevant ledger |
| **Hand-editing `journal/master_followup_tracker.md`** | It is a GENERATED file - your edit gets overwritten on the next render, and in the meantime the ledgers (the real source) don't reflect it | Use the ledger CLIs (`commitment_ledger.py`, `waiting_watchdog.py`, `decision_log.py`); re-render if you need the view refreshed now |
| Adding external tasks to `todo.md` only | the owner loses visibility on WHO owes WHAT | Must also add via `waiting_watchdog.py add` |
| No SLA on external items | Items silently rot with no accountability | `waiting_watchdog.py add` requires `--sla-hours`; default to 72h (3 business days) if unsure |
| Marking "Done" in one file only | Creates contradictions across the system | Grep and verify consistency across Dashboard, todo.md, and ledger reports |
| Silent updates | the owner doesn't know what changed | Always report: "Updated X, marked Y done, flagged Z overdue" |
