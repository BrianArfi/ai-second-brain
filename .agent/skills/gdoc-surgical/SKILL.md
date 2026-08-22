---
name: GDoc Surgical Editor
description: Targeted in-place edits to existing Google Docs (replace text, hyperlink text, append sections, insert table rows) via the Docs API - preserves hand edits, comments, images, and sharing. Use INSTEAD of re-uploading whenever a doc already exists.
---

# GDoc Surgical Editor

Make **surgical, in-place edits** to a Google Doc instead of overwriting it.

**Why this exists:** a full `update --convert` re-upload WIPES inline images,
resets sharing to "anyone with link," and clobbers any edits a human made by
hand. When a doc already exists and you only need to change part of it, edit
it surgically.

**Auth:** reuses the drive connector `token.json` files (a Drive-scoped OAuth
token is valid for the Docs API too - no extra scopes or re-auth needed).

## Decision rule

| Situation | Use |
| :--- | :--- |
| Doc doesn't exist yet | `gdocs-create` |
| Replace the ENTIRE content | drive connector `update --convert` (then re-run formatting + permissions) |
| Fix a phrase, a date, a name | **this skill: `replace`** |
| Turn a "(Source: X)" citation / doc name into a clickable link | **this skill: `linkify`** |
| Add a section / changelog entry at the end | **this skill: `append`** |
| Add a row to an existing table | **this skill: `insert-row`** |
| Remove a row from an existing table | **this skill: `delete-row`** |

## Commands

All commands: `python3 .agent/skills/gdoc-surgical/gdoc_surgical.py <cmd> --id DOC_ID --account <account>`

```bash
# 1. ALWAYS read first - see the doc's structure + verify your target text
python3 .agent/skills/gdoc-surgical/gdoc_surgical.py read --id DOC_ID

# 2a. Replace text (hits ALL occurrences - widen the find-string if it's common)
python3 .agent/skills/gdoc-surgical/gdoc_surgical.py replace --id DOC_ID \
  --find "Target ship: July 15" --with "Target ship: July 22"

# 2a-bis. Linkify: hyperlink every occurrence of a name/citation, text unchanged
python3 .agent/skills/gdoc-surgical/gdoc_surgical.py linkify --id DOC_ID \
  --find "Q3 Roadmap v2.2" --url "https://docs.google.com/document/d/FILE_ID/edit"

# 2b. Append a section (supports #/##/### headings and - bullets, \n for newlines)
python3 .agent/skills/gdoc-surgical/gdoc_surgical.py append --id DOC_ID \
  --text "## Changelog\n- 2026-07-12: updated ship date"

# 2c. Tables: list them, then insert a filled row
python3 .agent/skills/gdoc-surgical/gdoc_surgical.py list-tables --id DOC_ID
python3 .agent/skills/gdoc-surgical/gdoc_surgical.py insert-row --id DOC_ID \
  --table 0 --cells "2026-07-12|Updated ship date|the owner"

# 2d. Delete a row. --row and --expect are both mandatory: --expect must appear
# in the row, or nothing is deleted (exit 2). Row indexes shift under any other
# edit, so this is what stops a stale index deleting the wrong row.
python3 .agent/skills/gdoc-surgical/gdoc_surgical.py delete-row --id DOC_ID \
  --table 13 --row 5 --expect "Discount > Work's earnings"
```

## Rules of engagement

1. **Read before writing.** Run `read` first, verify the exact wording of your
   target. `replace` exits with code 2 if it changed 0 occurrences.
2. **`replace` is global.** It hits every occurrence including inside tables.
   If the find-string appears more than once and you only want one, include
   surrounding words to make it unique.
3. **Never full-overwrite a doc a human has edited.** Their edits are the
   source of truth; surgical edits preserve them.
4. **Verify after writing.** Every command prints the doc link - confirm the
   edit landed (Drive verification rule applies: output file ID + link).
5. Text inside table cells is reachable by `replace`/`linkify` too - you
   rarely need cell coordinates for content fixes.
6. **`linkify` needs a real target.** Resolve the citation's actual Drive file
   ID first (search Drive by title, or check master_links.md / memory) -
   don't link to a guessed URL. If the cited doc genuinely doesn't exist in
   Drive (only a local repo file), it can't be made clickable without
   uploading it first - flag that to the owner rather than skipping silently.

## Limitations

- No anchored comments (Docs API can't create them - reply to existing threads
  via the drive connector's `reply_helper.py` instead).
- No person smart-chips (put the email as plain text).
- `append` styles are basic (headings, bullets, plain); for rich new content
  consider drafting a new doc instead.
- `insert-row` fills LEFT cells first visually but writes right-to-left
  internally; pipe-separated values map left-to-right.
