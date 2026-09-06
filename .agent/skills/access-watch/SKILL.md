# access-watch

Finds every request for access that is waiting on the owner, from both places they arrive, and verifies each one against the live permission state before reporting it.

## Why it exists

On 1 September 2026 the owner found Slack threads asking him for access that no sweep had ever put in front of him. One vendor engineer had asked twice in a project channel and got nothing back. The investigation found two separate holes:

- **Slack.** The mention ledger did capture "we don't have access to the doc above" as an open item. But `mention_ledger.py report` prints one flat list, 159 items long, sorted by age, and only the most senior stakeholder gets a flag. A blocked vendor team reads exactly like "Oh".
- **Google Drive.** A "Share request for ..." arrives by **email**. It never touches Slack, and nothing in the harness read Gmail. Five were sitting unanswered, the oldest 39 days, across four PRDs, a set of meeting minutes and a revenue doc. Four different people, none of whom could do their job until the owner clicked Approve.

## What it does

`report` prints one section, oldest first, because it is a queue.

- **Drive side.** Reads `from:drive-shares-dm-noreply@google.com subject:"Share request"`, pulls the file id out of each mail, then **checks the file's current permissions**. A request only appears when the requester still has no access right now, so a request the owner granted last week disappears by itself. No state file to go stale.
- **Slack side.** Reads open items in `journal/state/slack_mention_ledger.json` and keeps the ones that match `is_access_request()`, the shared detector for the phrases people actually use when locked out (English and Indonesian).

`is_access_request` lives here and `mention_ledger.py` imports it, so there is one definition. Items it matches are marked `access_request: true` and forced to `priority`, and the Slack report prints them in their own 🔑 block above everything else.

## Commands

```bash
python3 .agent/skills/access-watch/scripts/access_watch.py report                # markdown
python3 .agent/skills/access-watch/scripts/access_watch.py report --json
python3 .agent/skills/access-watch/scripts/access_watch.py report --no-drive     # Slack only, instant
python3 .agent/skills/access-watch/scripts/access_watch.py report --out journal/state/access_requests.json
python3 .agent/skills/access-watch/scripts/access_watch.py grant --file <id> --email <addr> --approved
python3 .agent/skills/access-watch/scripts/access_watch.py dismiss --file <id> --reason "..."
```

`report` exits 1 when something is pending, so it can gate.

The Drive pass costs one Gmail call per share-request mail plus one Drive call per file, about 90 seconds over 90 days. Cron writes the snapshot with `--out` every hour; briefings read `journal/state/access_requests.json` and never wait on Gmail. `--no-drive` is the instant path.

## Granting stays gated

`grant` refuses without `--approved`, for the same reason a Slack send does: it exposes an internal document to somebody outside the company. `report` only ever prints the command.

`dismiss` suppresses a file (or one requester on a file) in `journal/state/access_watch_ignore.json`. It exists for requests that are never going to be granted, like the seven candidates who asked for edit rights on the recruitment form.

## What it cannot fix

A doc inside a **shared drive with `domainUsersOnly`** cannot be shared with anyone outside Work, whatever the owner does. The API returns `teamDriveDomainUsersOnlyRestriction`. The report marks those, because the action is not "grant it", it is "ask the drive's organizer to move the doc or ask an admin to lift the restriction". This is what blocked ExampleVendor on `PRD: Seller Portal — Operations & Usability Improvements` on 1 Sep 2026.
