---
name: Google Docs Creator
description: Convert markdown to real editable Google Docs, or upload any file to Google Drive. Supports work and personal accounts. Auto-refreshes tokens. Uses Drive API HTML→GDoc conversion for proper formatting.
---

# Google Docs Creator Skill

Creates **real editable Google Docs** from markdown, or uploads any file to Drive.
Supports `work` (you@yourcompany.com) and `personal` (you@example.com) accounts.

> **Update Protocol:** See `CLAUDE.md § Update Protocol`. Use `gdrive_manager.py update --convert` for revisions — not `create-doc`.

> **MANDATORY after any `create-doc` or `update --convert` that has tables:** run the formatting pass below, then re-restrict the doc to the domain (both `create-doc` and `update --convert` re-publish it "anyone can comment"). Skipping this is why docs render cramped/messy.

---

## Formatting pass (automatic since 2 Sep 2026)

`create-doc` runs it for you, and so does any `gdrive_manager.py` upload/update with `--convert` that produces a Doc. You only run it by hand on a doc created before this, or after a surgical edit that changed how much text sits in a table. Skip it on one call with `--no-format-pass` (gdocs-create) or `GDOC_FORMAT_PASS_DISABLE=1` (the Drive connectors).

`create-doc`/`update --convert` create PAGES/letter docs, leave tables at the legacy ~468pt total (cramped), and pass literal `--`/`->` straight through. `format_pass.py` switches the doc to **pageless**, widens columns to fill the width, AND lint-fails if any literal `--`/`->` survived (those are AI tells — rephrase the source per feedback_no_emdash_rephrase, never leave dashes):

```bash
# after create/update, before restricting + sharing:
python3 .agent/skills/gdocs-create/format_pass.py <DOC_ID> [<DOC_ID> ...] --account work
# exit 1 = literal --/-> still in the doc -> fix source markdown, re-run update --convert, re-run this
python3 .agent/scripts/drive_permissions.py restrict <DOC_ID> --domain yourcompany.com --apply
```

Full canonical flow for a new shared doc: `create-doc` → `format_pass.py` (widths + lint) → `drive_permissions.py restrict` → output ID + link + verify.

---

## Commands

### Create Google Doc from Markdown (new file only)

```bash
timeout 180s python3 ".agent/skills/gdocs-create/gdocs_create.py" create-doc \
  --title "Document Title" \
  --file "path/to/file.md" \
  --account work
```

**Options:**
- `--account`: `work` or `personal` (default: `work`)
- `--parent-id`: Drive folder ID (default: root)
- `--content "# inline markdown"`: inline content instead of `--file`
- `--html`: input is already HTML, skip MD conversion

### Update Existing Doc

Use `work-drive-connector` or `personal-drive-connector`:

```bash
timeout 180s python3 ".agent/skills/work-drive-connector/gdrive_manager.py" update \
  --id "FILE_ID" --file "path/to/updated.md" --convert
```

### Upload Any File

```bash
timeout 180s python3 ".agent/skills/gdocs-create/gdocs_create.py" upload \
  --file "report.pdf" --title "Q1 Report" --account work --parent-id FOLDER_ID
```

---

## Formatting Defaults

| Element | Font | Size |
| :--- | :--- | :--- |
| Body | Calibri | 12pt |
| H1 | Calibri | 19pt |
| H2 | Calibri | 15pt |
| H3 | Calibri | 13pt |
| H4 | Calibri | 12pt |
| Code/Pre | monospace | 11pt |

---

## Account / Token Status

| Account | Token File | Status |
| :--- | :--- | :--- |
| work | `work-drive-connector/token.json` | Auto-refreshed ✅ |
| personal | `personal-drive-connector/token.json` | Auto-refreshed ✅ |
| secondary | `secondary-drive-connector/token.json` | Generic slot (ex-Secondary token, revoked) |
