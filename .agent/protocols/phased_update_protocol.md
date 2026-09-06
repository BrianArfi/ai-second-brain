# Phased Update Protocol (Step-by-Step execution)

To ensure strategic alignment, prevent informational overwhelm, and strictly enforce the Anti-Recency Guardrail, all daily updates (`/daily-update`) and weekly planning (`/weekly-planning`) MUST be executed as a multi-step conversation. 

The agent MUST NOT perform all steps in a single turn. Instead, proceed step-by-step, waiting for the user to acknowledge or align before moving to the next phase.

---

## 📅 The 4-Step Update Sequence

### 1. Step 1: Context Harvest
*   **Action**: Execute `daily_update_runner.py` to gather raw data (modified/created files, Slack history, calendar agendas, Fathom recordings, Jira sprint progress).
*   **Delivery**: Present a concise list of *where* data was collected (e.g., "Scanned 5 files, checked 3 Slack channels, fetched default/work calendars"). Highlight any expired credentials or access blockers immediately.
*   **User Gate**: Ask the user for permission to proceed to Summarization. Do not summarize or update any dashboard files yet.

### 2. Step 2: Summarize (Synthesis)
*   **Action**: Analyze the harvested raw data and synthesize it into a clean, human-readable draft review of recent events and changes. **Read the JSON sidecar (`_temp/harvest_[mode]_[date].json`) for synthesis -- it is a compact structured format (sections: slack, jira, calendar, todo_p0, files, fathom) that avoids re-reading the full 100-180 KB markdown dump. Fall back to the markdown output file only if the JSON is missing.**
*   **Delivery**: Present a draft summary of the facts (e.g., "Teammate posted X about the B2C App Store registration, 2 new meeting notes were added, 52 active tickets in MSP").
*   **User Gate**: Present the draft summary and ask the user if anything is missing or incorrect before moving to prioritization.

### 3. Step 3: Prioritize (Anti-Recency Guardrail)
*   **Action**: Evaluate all raw updates against existing strategic priorities in `Dashboard.md` and `journal/todo.md`.
*   **Delivery**: Enforce the **Anti-Recency Guardrail**:
    *   Separate **Strategic Focus Items** (high weight, aligned with P0/P1 backlogs) from **Operational Log / Activity Updates** (minor weight, purely recent status).
    *   Identify and highlight critical risk alerts (e.g., sprint workload imbalances like Teammate or Teammate exceeding 40% safety threshold).
*   **User Gate**: Present the proposed prioritized list for today/this week and wait for the user's explicit alignment on the focus items.

### 4. Step 4: Plan & Execute
*   **Action**: Formulate the actual updates to `Dashboard.md` and `journal/todo.md` based on the approved priorities.
*   **Delivery**: 
    1. Update `Dashboard.md` and `journal/todo.md` in the repository.
    2. Ensure the local dashboard at `http://localhost:3737` is active and synced.
    3. Generate the daily update briefing artifact in the active conversation's `brain/daily_briefing.md` directory.
    4. Provide the final concise briefing message to the user with actionable next steps.

---

## 🌅 Mode-Specific Guidance

### Morning Mode (`/daily-update morning`)
- **Step 1 (Context Harvest)**: Fast harvest only -- Calendar, Jira, Slack (all channels, 5 messages per channel), **email sweep (gmail-connector, mandatory)**, and current todo.md state. Skip Fathom, Document Indexer, and file scans.
- **Step 2 (Summarize)**: Brief overnight activity summary. Keep concise.
- **Step 3 (Prioritize)**: This is the CORE output. Propose exactly 5 concrete priorities for the day, ranked by strategic weight (P0 first). Write them to `_temp/daily_plan_[date].md` for evening cross-check.
- **Step 4 (Plan & Execute)**: Update Dashboard.md with the `(Pagi)` section only. Do NOT update todo.md completion status in morning mode.

### Evening Mode (`/daily-update evening`)
- **Step 1 (Context Harvest)**: Full harvest -- all Slack channels (10 messages per channel), **email sweep (gmail-connector, mandatory)**, Fathom sync, file change scan, Document Indexer, and GitHub sync.
- **Step 2 (Summarize)**: This is the CORE output. Compare today's accomplishments against the morning plan from `_temp/daily_plan_[date].md`. Report what was completed, what was missed, and what carries over.
- **Step 3 (Prioritize)**: Identify carryover items and flag any that need escalation. Enforce Anti-Recency Guardrail.
- **Step 4 (Plan & Execute)**: Update Dashboard.md with the `(Malam)` section, update todo.md completion status, archive entries older than 7 days to `journal/daily_logs/`, and run GitHub sync.

### Weekend Mode (Saturday/Sunday)
- Skip Jira and Fathom. Run Slack scan only (minimal mode).
- No LinkedIn content prompt unless the owner explicitly requests it.

---

## 🎯 Disambiguation & Enforcement
*   **No Multi-Step Jumps**: The agent must never jump from Step 1 straight to Step 4. Doing so violates this protocol and leads to unaligned dashboard updates.
*   **Windows Subprocess Safety**: Always run runner scripts with explicitly defined UTF-8 encoding to avoid Windows CP1252 character failures.
*   **Em-Dash Rule**: Double-hyphens (`--`) or hyphens (`-`) must be used exclusively in all generated report content.
