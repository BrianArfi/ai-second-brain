---
name: dashboard-updater
description: Refreshes Dashboard.md from Drive, Calendar, and Slack in one pass. Use when the dashboard has gone stale, rather than editing it by hand.
---

# Dashboard Update Skill

The **Dashboard Update Skill** is a centralized automation routine designed to synchronize the local Product Dashboard (`Dashboard.md`) with the latest developments across Google Drive, Google Calendar, and Slack.

## Purpose
To eliminate manual effort in keeping the product dashboard relevant. This skill aggregates:
- **Roadmap Updates**: Pulls the latest statuses from the Q2 Roadmap docs in Work Drive.
- **Calendar Focus**: Syncs the upcoming week's focus using the Google Calendar Connector.
- **Slack Intelligence**: Synthesizes recent discussions (blockers, alignments) into a daily briefing.

## Usage
Run the following command to trigger a full synchronization:
```bash
python3 .agent/skills/dashboard-updater/scripts/dashboard_sync.py
```

## Architecture
1. **Collector Layer**: Invokes `gcal_manager.py`, `gdrive_manager.py`, and `slack_client.py` to gather raw data.
2. **Synthesis Layer**: Uses LLM-ready formatting to summarize Slack discussions and roadmap changes.
3. **Writer Layer**: Updates the specific sections in `Dashboard.md` while preserving custom user notes.

## Dependencies
- `Google Calendar Connector` (Work Profile)
- `Work Drive Connector`
- `Slack Connector` (User Token required for search)
