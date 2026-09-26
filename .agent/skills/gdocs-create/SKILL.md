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

## Diagrams: Mermaid images only, never text-drawn (rule since 24 Sep 2026)

A tree, box or flow drawn with text characters (`├──`, `└──`, `│`, `+---+`, dotted leaders like `A. Thing ...... P0`) renders in a Google Doc as a monospace block with broken line joins. the owner flagged it on the Q4 roadmap initiative tree. Every diagram that goes into a Doc is a Mermaid diagram, rendered to an inline image.

- **Draw it** in Mermaid per [`diagram-gen`](../diagram-gen/SKILL.md). A hierarchy or initiative tree is `flowchart LR` with one node per leaf. Colour nodes by meaning with `classDef` (for priorities: P0 red, P1 amber, P2 grey) and add a one-line legend under the image.
- **Validate it** with `.agent/skills/diagram-gen/render_check.py` before anyone sees it.
- **In the markdown sent to Drive**, put `[[PLACEHOLDER_NAME]]` where the diagram goes. A raw ```` ```mermaid ```` fence would convert to a code block.
- **After the convert**, embed the image: `python3 scripts/embed_mermaid_in_gdoc.py --id <DOC_ID> --account work --diagrams-file <placeholders.json>`, where the JSON maps each placeholder to its Mermaid source. Re-run it after every `update --convert`, because a convert wipes inline images.
- **The repo copy** of the document keeps the ```` ```mermaid ```` fence, which the local dashboard renders.

**Enforced in code.** `find_ascii_diagrams()` in `.agent/scripts/file_utils.py` runs inside `gdocs_create.py create-doc`, both `--convert` paths of the work and secondary `gdrive_manager.py`, and the source lint in `scripts/readability_gate.py` (which `publish_prd.sh` runs). It refuses box-drawing characters, ASCII art in an untyped code block, and a raw mermaid fence. Typed code blocks (`bash`, `json`, `gherkin` and similar) are left alone. Override for one call only: `GDOC_ALLOW_ASCII_DIAGRAM=1`.

---

## Publish pass checks R1 to R4 (rule since 25 Sep 2026)

the owner's rule, after the ExampleProgram OTP PRD and BRD shipped with local links, a run-on header, and screenshot promises with no screenshot. Every Doc passes these four checks before and after publishing:

| # | Rule | Fails when |
| :--- | :--- | :--- |
| R1 | **Links are internet links.** A reader on another machine must be able to click every link. | A link or bare path points at `C:/`, `/home/`, `file://`, a WSL path, a repo-relative `.md`, or `localhost:3737`. Link the Drive, Jira, Slack or Fathom URL instead. Ledger ids (`DEC-`, `COM-`, `WAIT-`) go in as plain text. |
| R2 | **Tables are sized.** | A 3+ column table has even widths (the format pass did not run), a column is under 36pt, or a table is wider than the page in a paged Doc. |
| R3 | **Promised pictures exist and render.** | A `[[PLACEHOLDER]]` resolves to neither a Mermaid diagram nor an image file. Also when a free-text `[[SCREENSHOT: ...]]` is left in, a "Representation of" caption has no picture, the Doc holds fewer images than the source promised, or an image has no content. |
| R4 | **Header fields sit one per line.** | One line carries two or more `**Label:**` fields joined by `·`, `•` or `|`. Put each field on its own line, or use a Field/Value table. |

- **Screenshots are made, not promised.** When a document calls for a screen, build a mockup: HTML in the product's visual style, captured per section with Playwright at 2x. Example: `Clients/Work/Example Program/assets/otp_address_mockups/` (`screens.html`, `capture.py`). Label each screen "Mockup for review" until Product Design supplies the final.
- **Embed screenshots in the same run:** `bash scripts/publish_prd.sh --file <md> --id <DOC_ID> --images <map.json>`, where the JSON maps each `[[TOKEN]]` to a PNG. The script is `scripts/embed_png_in_gdoc.py`. A re-convert wipes inline images, so always publish through `publish_prd.sh` with `--images`, never convert alone.
- **Enforced in code** by `scripts/readability_gate.py`: `--source` checks R1, R3 and R4 in the markdown, and `--doc` checks R1, R2 and R3 on the published Doc. `publish_prd.sh` runs both. Check by hand: `python3 scripts/readability_gate.py --source <md> --doc <DOC_ID> --images <map.json>`.

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
