---
description: Product - Meeting minutes (MOM) for Work - Fathom transcript, English, quality-gated, exported to Google Docs
argument-hint: "<meeting name/date, or paste notes>"
---

MOM workflow (Work):

1. Read `Dashboard.md` and the relevant project context (Clients/Work/...).
2. Get the raw meeting facts: spawn the `meeting-harvester` subagent with the meeting name/date (or Fathom URL / call-id). It resolves the recording via `journal/fathom_registry.json`, pulls the transcript + summary, and returns raw facts (attendees, decisions, action items, key quotes) WITHOUT synthesis, keeping this context lean for drafting. If the owner pastes notes instead, use those. Fallback only if the Fathom MCP is unavailable: `python3 .agent/skills/fathom-connector/scripts/fathom_client.py`.
   - **Local recordings** (registry `recording_id` starting with `local-`, `match_source: "local-recorder"`, or the owner points at an audio file): these come from the local note-taker (`meeting-recorder/README.md`). The transcript is already at `Clients/Work/meetings/transcripts/<file>.md` and there is usually a MOM draft `Clients/Work/meetings/MOM_<slug>_<date>.md` with header `Status: DRAFT (local pipeline, not reviewed yet)`. Do NOT redraft from scratch: review/refine that draft, then continue from step 4. If only a raw audio file exists (not yet processed), run `python3 meeting-recorder/watcher.py --file <audio>` first.
3. Draft in ENGLISH following `templates/mom_work.md` with sections: Attendees · Agenda · Discussion · Decisions · Action Items (owner + due date per item). No em-dashes.
4. Before presenting: spawn the `draft-reviewer` subagent (type "MOM"). Fix issues, then present to the owner.
5. After the owner approves, create the Google Doc:
   `timeout 180s python3 .agent/skills/gdocs-create/gdocs_create.py create-doc --title "..." --file <path> --account work`
6. If decisions affect active tasks → update `journal/todo.md`. Other people's action items that block the owner go into the waiting-on ledger, not `journal/todo.md` - see step 6b.
6b. Register the meeting's outcomes in the ledgers (they are the source of truth for later briefings):
   - Every decision surfaced in the meeting → `python3 .agent/skills/decision-log/scripts/decision_log.py add --title "..." --decider "<name>" [--deadline YYYY-MM-DD] [--project "..."] --source <fathom_url> --source-type fathom [--stakeholders "A,B"]`. If it was actually decided in-meeting, follow with `decision_log.py decide <DEC-id> --decision "<what was decided>"`.
   - Every action item where OWNER is the owner → `python3 .agent/skills/commitment-ledger/scripts/commitment_ledger.py add --text "..." --to "<requester>" [--due YYYY-MM-DD] [--project "..."] --source <fathom_url>`.
7. Confirm with the file ID + Drive link (no ID returned means the operation FAILED).

Request: $ARGUMENTS
