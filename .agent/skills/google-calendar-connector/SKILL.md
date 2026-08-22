---
name: Google Calendar Connector
description: A skill to interact with Google Calendar API for fetching schedules, sweeping weekly focus, and generating calendar summaries.
---

# Google Calendar Connector Skill

Connect to Google Calendar to fetch events, sweep weekly schedules, and generate structured summaries for Dashboard integration.

## Capabilities
- **List** events for a custom date range.
- **Sweep** events grouped by Today / This Week / Last Week (text or markdown output).
- Supports multiple profiles: `default` (personal) and `work`.
- **Timeouts**: Scripts have a built-in **180-second global timeout**. Always wrap background calls in `timeout 180s` for safety.

## Commands

### Sweep (Recommended for Dashboard)
```powershell
# Personal calendar sweep (markdown for Dashboard)
timeout 180s python3 .agent/skills/google-calendar-connector/gcal_manager.py sweep --profile default --output markdown

# Work calendar sweep
timeout 180s python3 .agent/skills/google-calendar-connector/gcal_manager.py sweep --profile work --output markdown

# Plain text sweep
python .agent/skills/google-calendar-connector/gcal_manager.py sweep --profile default

### Create (Schedule Event)
```powershell
# Create a 1-hour meeting
timeout 180s python3 .agent/skills/google-calendar-connector/gcal_manager.py create --summary "Meeting Title" --start "2026-04-08T08:00:00" --end "2026-04-08T09:00:00" --profile default

# Create with description
python .agent/skills/google-calendar-connector/gcal_manager.py create --summary "Sync with Team" --start "2026-04-08T10:00:00" --end "2026-04-08T11:00:00" --desc "Weekly sync to discuss PRDs"

# With attendees (comma-separated) on the Work account
python3 .agent/skills/google-calendar-connector/gcal_manager.py create --summary "Title" --start "2026-08-13T13:00:00" --end "2026-08-13T13:30:00" --attendees "teammate@yourcompany.com, other@yourcompany.com" --desc "$(cat /tmp/desc.txt)" --profile work
```

Times are interpreted as **Asia/Jakarta**, so pass the owner's local WIB time directly. A Google Meet link is attached by default; pass `--no-meet` to skip it.

### Update (patch an existing event)

Use this rather than deleting and recreating: recreating drops existing RSVPs and re-notifies everyone from scratch. Only the fields you pass are touched.

```bash
# Add a pre-read link to an invite that already went out
python3 .agent/skills/google-calendar-connector/gcal_manager.py update --event-id <id> --desc "$(cat /tmp/desc.txt)" --profile work

# Move it 30 minutes later without emailing anyone
python3 .agent/skills/google-calendar-connector/gcal_manager.py update --event-id <id> --start "2026-08-13T13:30:00" --end "2026-08-13T14:00:00" --no-notify --profile work
```

The event ID is the first field of the base64 `eid` in a calendar link:
`python3 -c "import base64,sys;s=sys.argv[1];print(base64.b64decode(s+'='*(-len(s)%4)).decode())" <eid>`

`--attendees` on `update` **replaces** the whole list, it does not append.

### RSVP (answer an invite someone else sent)

```bash
# Accept, by event id
python3 .agent/skills/google-calendar-connector/gcal_manager.py rsvp --event-id <id> --response accepted --profile work

# Or find it by title, when exactly one upcoming event matches
python3 .agent/skills/google-calendar-connector/gcal_manager.py rsvp --find "ExamplePartner Channels" --response accepted --profile work
```

`--response` takes `accepted` | `declined` | `tentative`, and `list --json` now emits each event's `id`.

**Never use `update --attendees` to RSVP.** It replaces the guest list, so answering an invite that way silently drops everyone else off an event this account does not own. `rsvp` reads the event, changes only this account's own `responseStatus`, patches the full list back, and re-reads to confirm the answer stuck. It refuses outright if the profile is not already on the attendee list, and never emails the other guests (`sendUpdates='none'`).

### List (Raw event listing)
```powershell
# Default profile
timeout 180s python3 .agent/skills/google-calendar-connector/gcal_manager.py list --days-back 7 --days-forward 7

# Work profile
python .agent/skills/google-calendar-connector/gcal_manager.py list --days-back 7 --days-forward 7 --profile work
```

## Authentication
- **Default**: Uses `credentials.json` and `token_calendar.json` in the project root. This is the owner's **personal** calendar.
- **Work**: Uses `credentials.json` and `token_calendar_work.json` in `.agent/skills/work-drive-connector/`.
- First run per profile requires OAuth browser authorization.

### Check token health first
```bash
python3 .agent/skills/google-calendar-connector/gcal_manager.py auth --status
```
Prints OK / DEAD / NO TOKEN per profile. Run this before blaming a sweep for missing events. The personal token's refresh grant was revoked on 3 Jun 2026 and nothing caught it until 13 Aug, because every automated caller passes `--profile work` and the dead profile only failed when someone asked for it by hand. That is the root cause behind `ME-CALENDAR-PERSONAL-BLINDSPOT` in `journal/todo.md`.

### Re-authorize without an interactive terminal
Most sessions here have no TTY, and the old flow called `input()`, so it blocked until timeout and produced nothing. Use the two-step flow instead:

```bash
# 1. print the URL, approve it in a browser. Always pass --login-hint.
python3 .agent/skills/google-calendar-connector/gcal_manager.py auth --profile default --start --login-hint you@example.com

# 2. paste back the code, or the whole localhost redirect URL
python3 .agent/skills/google-calendar-connector/gcal_manager.py auth --profile default --code '<PASTE>'
```
Step 2 writes the token, chmods it to 600, and prints the account plus the calendars it can now see. Codes are single use and expire in minutes, so if step 2 fails just run step 1 again.

**Always pass `--login-hint`.** A token for the wrong Google account is still a valid token, so the mistake looks exactly like success. On 13 Aug 2026 the personal profile was re-authorized against `you@yourcompany.com` because the browser was already signed into it, which left `default` and `work` pointing at the same calendar and the personal blindspot still open. With a hint set, `--code` now checks the granted account and **refuses to write the token** if it does not match.

Account per profile: `default` is `you@example.com` (personal), `work` is `you@yourcompany.com`.

## Integration Points
- **Daily Update Workflow** (`/daily-update`): Step 1.5 runs a calendar sweep.
- **Weekly Report Generator**: Uses sweep data for the "Key Deliverables" section.
- **Dashboard.md**: Output can be pasted into the `📅 Calendar Focus` section.
