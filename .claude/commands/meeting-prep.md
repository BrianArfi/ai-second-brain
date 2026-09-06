---
description: Product - Gather notes and open items into a one-page meeting brief
argument-hint: meeting name or time
---

Given a meeting name and/or time (from `$ARGUMENTS`, or ask if it's empty),
pull together everything relevant in this workspace and produce a one-page
prep brief.

1. **If `$ARGUMENTS` is empty**, ask which meeting to prep for before doing
   anything else.

2. **If Google Calendar is connected** (check with `claude mcp list`), look
   up the meeting to confirm the real time, attendees, and any description
   or agenda already on the invite. If it's not connected, work from the
   meeting name alone and say the time/attendee list couldn't be confirmed.

3. **Search `notes/` and `inbox/`** for anything related: past notes
   mentioning this meeting series, the people attending, or the project it's
   about. Don't limit this to exact name matches: a meeting about "Q3
   budget" should also surface notes just about "budget."

4. **If Slack or Jira are connected**, check for anything relevant: recent
   messages with the attendees about this topic, open tickets tied to it.
   Skip this step cleanly if those aren't connected; don't guess at what
   might be there.

5. **Write a one-page brief** with:
   - **Meeting**: name, time, attendees (from the calendar if available)
   - **Context**: 2-4 sentences on what this meeting is about, from past
     notes
   - **Open items**: anything unresolved from past notes or connected tools
     that's likely to come up
   - **Questions worth raising**: anything the user seems to be waiting on
     an answer for, related to this meeting
   - **Nothing found**: say so plainly for any section with no material,
     rather than padding it

6. **Save the brief** to `notes/meeting-prep/<short-meeting-name>-<date>.md`,
   creating the `meeting-prep/` folder if it doesn't exist, and show it to
   the user.

Ground everything in what's actually written down or visible through a
connected tool. If the meeting is unfamiliar to this workspace, say that
plainly instead of inventing context.
