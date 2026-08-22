---
description: Weekly review + planning session - synthesize the past week across all clients and set priorities for the coming week
---

# Weekly Planning Workflow

Run this every Monday morning (or end of Friday) to close out the previous week and plan the next one. Covers all clients: Secondary, Work, ClientB.

---

## Phase 1: Gather Context (Don't re-read what you already have)

### 1a. Run the daily update script first
This fetches the freshest Slack messages and calendar data in one shot.

```bash
python3 .agent/scripts/daily_update_runner.py
```

Then read `daily_update_output.md` - this is the ground truth for today's state.

### 1b. Read the existing summaries (don't re-read raw sources if these exist)

- Read `Dashboard.md` - focus on:
  - Daily Briefing section (what was the state as of last update?)
  - All project status tables (Secondary + Work)
  - Top Priorities list
- Read `journal/todo.md` - which items moved, which are still stuck?
- Check `_output/daily-updates/` for any daily update files from this past week

### 1c. Scan for new meeting notes and Fathom summaries added this week

```bash
find Clients/ -name "*.md" -newer Dashboard.md -not -path "*/_archive/*" | sort
```

Read any new meeting notes found (these won't be in existing summaries yet).

### 1d. Slack - read this week's messages

**Work Slack** (via MCP Slack connector - built-in):
- Search for messages from the past 7 days mentioning key projects: ExampleProgram, Example Program, B2C, Seller Portal, ExampleVendor, ExampleProgram
- Look for decisions made, blockers raised, action items assigned to the owner

**Secondary-client Slack** (via `.agent/skills/secondary-slack-connector` if token is valid):
- Check if token is valid. If expired, flag it and skip - do not silently omit.
- Key channels to scan: ExampleProduct, Ops Platform, general product discussions

### 1e. Calendar - check the past week AND coming week

```bash
python .agent/skills/google-calendar-connector/gcal_manager.py sweep --profile default --output markdown
python .agent/skills/google-calendar-connector/gcal_manager.py sweep --profile work --output markdown
```

- If token expired, flag it and note what's missing.
- From **last week**: which meetings happened? Any key decisions from those?
- From **this week / next 7 days**: what's coming up that needs prep?

---

## Phase 2: Weekly Review (What happened last week?)

Synthesize everything from Phase 1 into a review. Structure:

### Secondary - Last Week
- **ExampleProduct**: What shipped, what's in progress, blockers
- **Operations Platform**: Status of Yellow.ai, refund automation, CS items
- **ExampleProduct / Travel**: Any movement?
- **Key decisions made** (from Slack or meetings)
- **Overdue items** still unresolved

### Work - Last Week
- **B2C SuperApp**: ExampleVendor, ExampleProgram, App Verification status
- **Example Program / ExampleCo**: Standalone app progress, CMS phase 2
- **Seller Portal / Marketplace**: Any blockers or progress
- **Operations**: Operational failures strategy - still overdue?
- **Key decisions made** (from Slack or meetings)
- **Overdue items** still unresolved

### Cross-client
- Anything in `journal/todo.md` that's been sitting too long
- Career/personal items in `journal/career/`

---

## Phase 3: Weekly Plan (What's the focus this week?)

Based on the review, set the week's priorities:

### Top 3 Must-Do This Week (across all clients)
Pick the highest-leverage items - not the longest list. If there are P0 blockers from the review, those go here first.

### Secondary - This Week
- Sprint items, ClickUp tasks due, meetings to prep for

### Work - This Week  
- Key deliverables, stakeholder syncs, docs to finish
- Note: Work uses no ClickUp - track in `journal/todo.md` only

### Meetings & Prep Needed
List meetings from the coming week calendar and what needs to be prepared for each.

### Unblocking Actions
Specific asks or follow-ups needed from others (tag the stakeholder: YourManager, Teammate, etc.)

---

## Phase 4: Update Dashboard + Todo

1. Update `Dashboard.md`:
   - Refresh "Daily Briefing & Priority" with the week's focus
   - Update project status tables based on the review
   - Update "Last Updated" date

2. Update `journal/todo.md`:
   - Move completed items to `## ✅ Archive`
   - Add new items from the plan to appropriate sections
   - Make sure "This Week" section reflects the Phase 3 top priorities

3. Save the full weekly report to:
   `_output/weekly-reports/Weekly_Review_{YYYY-MM-DD}.md`
   (Use the Monday date of the week being planned)

---

## Phase 5: Deliver to User

Present a concise summary (not the full report) with:

1. **3-sentence recap of last week** - what actually moved?
2. **Top 3 priorities for this week** - be specific, not generic
3. **Blockers that need the owner's attention** - anything stuck waiting on a decision or someone else
4. **Calendar heads-up** - key meetings this week and what to prep
5. **Token/auth issues** - list any expired tokens that prevented a full scan

---

## Quick Checklist Before Finishing

- [ ] Dashboard.md updated with today's date
- [ ] todo.md reflects this week's actual priorities  
- [ ] New meeting notes scanned and captured
- [ ] Slack covered for both Secondary and Work (or flagged if token expired)
- [ ] Calendar covered for both calendars (or flagged if token expired)
- [ ] Weekly report saved to `_output/weekly-reports/`
