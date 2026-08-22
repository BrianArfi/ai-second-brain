---
name: work-link-sync
description: Links new Drive files into the Master Product List spreadsheet and the Master Documentation. Use after publishing a PRD or BRD so the index does not drift.
---

# work-link-sync

Auto-link new Work Drive files to the Master Product List spreadsheet and Master Documentation.

## When to use

Every time a file is created or updated in Work Drive (a new PRD, a new doc, a new reference), run this skill so that:
1. The **spreadsheet** (Master Product List) gets a hyperlink to the new file
2. The **master doc** for that component gets the file in its Related Documents section

## Usage

```bash
python3 .agent/skills/work-link-sync/link_sync.py \
  --url "<google_doc_url>" \
  --name "<display_name>" \
  --component "<component_name>" \
  --type <prd|master|reference>
```

## Arguments

| Argument | Required | Description |
|---|---|---|
| `--url` | Yes | Full Google Doc URL |
| `--name` | Yes | Display name shown in spreadsheet and master doc |
| `--component` | Yes | Component name. Accepts aliases (see below). |
| `--type` | No | `prd`, `master`, or `reference` (default: `reference`) |
| `--spreadsheet-only` | No | Only update spreadsheet, skip Master Doc |

## Supported Components & Aliases

| Canonical Name | Accepted Aliases |
|---|---|
| IAM (Identity Access Management) | `iam`, `identity` |
| Gamification | `gamification`, `gamif` |
| Promotion Engine | `promotion`, `promo`, `voucher` |
| Blockchain | `blockchain`, `crypto` |
| Mixed Payment | `mixed payment`, `payment` |

## Examples

```bash
# New PRD for IAM Phase 2
python3 .agent/skills/work-link-sync/link_sync.py \
  --url "https://docs.google.com/document/d/ABC/edit" \
  --name "PRD: IAM RBAC Engine" \
  --component "iam" \
  --type prd

# New reference doc for Blockchain
python3 .agent/skills/work-link-sync/link_sync.py \
  --url "https://docs.google.com/document/d/XYZ/edit" \
  --name "Blockchain Smart Contract Audit Report" \
  --component "blockchain" \
  --type reference

# Update spreadsheet only (skip Master Doc)
python3 .agent/skills/work-link-sync/link_sync.py \
  --url "https://..." \
  --name "Gamification Rules Engine Spec" \
  --component "gamification" \
  --type prd \
  --spreadsheet-only
```

## Adding New Components

Edit `link_sync.py`:
1. Add to `MASTER_DOCS` dict: `'New Component': '<google_doc_id>'`
2. Add aliases to `COMPONENT_ALIASES` dict

## Note on Master Doc Updates

Master Doc updates require the Google Docs API to be enabled in Google Cloud Console (project 352863487418). If not enabled, the script will skip Master Doc update and print the Master Doc URL for manual update.
