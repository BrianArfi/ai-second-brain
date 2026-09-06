---
description: Setup - Connect Gmail, Calendar, Slack, or Jira so sweeps can see them
---

Walk the user through connecting their work tools, one at a time, so this
workspace can actually see what's in their inbox, calendar, Slack, and Jira.
Nothing here is automatic: every connection is something the user sets up
themselves, and nothing leaves this machine without them doing it.

1. **Check what's already connected first.** Run `claude mcp list` and read
   the output before suggesting anything. Only walk through tools that
   aren't already there.

2. **Explain what each one unlocks, then let the user pick.** For any tool
   not yet connected:
   - **Gmail**: `/sweep` can find emails that need a reply and pull
     them into `inbox/` as notes.
   - **Google Calendar**: `/meeting-prep` can look up a meeting by name and
     time on its own instead of being told the details by hand.
   - **Slack**: sweeps and `/follow-ups` can see messages waiting on a
     response, not just email.
   - **Jira**: `/follow-ups` can check whether an open loop already has a
     ticket instead of guessing.

   Ask which ones they want to set up now. One, all four, or none is fine.

3. **Jira has a direct, official connector.** For Jira/Confluence (Atlassian):
   ```
   claude mcp add --transport sse atlassian https://mcp.atlassian.com/v1/sse
   ```
   This opens a browser window to sign in and authorize. Run
   `claude mcp list` afterward to confirm it shows as connected, not just
   added.

4. **Gmail, Calendar, and Slack go through one no-code hub: Zapier's MCP
   service.** This is the realistic path for someone who isn't setting up
   developer credentials by hand; one Zapier account can unlock all three:
   - Go to `https://mcp.zapier.com` and sign in (a free Zapier account
     works).
   - Enable the specific actions you want Claude to use, for example
     "Gmail: Find Email" and "Gmail: Send Email", "Google Calendar: Find
     Event", "Slack: Find Message" and "Slack: Send Message". Only enable
     what you actually want the assistant able to do.
   - Zapier gives you a personal MCP server URL after that. Copy it, then
     run:
     ```
     claude mcp add --transport sse zapier <the URL Zapier gave you>
     ```
   - Confirm with `claude mcp list`.

   If the exact steps on Zapier's page have changed, or the user's company
   already provides its own Gmail/Calendar/Slack MCP endpoint (some IT
   departments do), use that instead. Ask before assuming.

5. **After each connection, verify it actually shows as connected**, not
   just listed, before moving to the next one. If something errors, help
   troubleshoot rather than leaving it half-set-up.

6. **Close the loop.** Once at least one tool is connected, mention that
   `/sweep`, `/follow-ups`, and `/meeting-prep` will now use it
   automatically, and that `/connect-tools` can be run again any time to
   add more.

If the user isn't sure what a tool would be used for, or doesn't want to
connect anything right now, that's a fine place to stop: this workspace
still works on notes and inbox alone, just without the sweeps able to see
outside data.
