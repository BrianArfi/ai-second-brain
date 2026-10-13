#!/usr/bin/env python3
"""
work-link-sync — Auto-link new Work Drive files to Master Docs & Spreadsheet

Usage:
  python3 link_sync.py --url <google_doc_url> --name <display_name> --component <component_name> [--type prd|master|reference]

Examples:
  python3 link_sync.py \
    --url "https://docs.google.com/document/d/ABC123/edit" \
    --name "PRD: IAM Phase 2 RBAC" \
    --component "IAM (Identity Access Management)" \
    --type prd

What it does:
  1. Finds matching rows in Master Product List spreadsheet → updates Documents/Links (col D)
  2. Finds the Master Doc for the component → adds the new file to Related Documents section
"""

import argparse
import sys

# The tick marks in the output crash a Windows cp1252 console after the writes land.
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import os
import signal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'work-drive-connector'))
from gdrive_manager import authenticate
from googleapiclient.discovery import build

SHEET_ID = '<YOUR_DRIVE_ID>'
SHEET_NAME = 'Master Product List & Breakdown (MECE)'

# Master Doc IDs per component (update as new Master Docs are created)
MASTER_DOCS = {
    'IAM (Identity Access Management)': '<YOUR_DRIVE_ID>',
    'Gamification': '<YOUR_DRIVE_ID>',
    'Promotion Engine': '<YOUR_DRIVE_ID>',
    'Blockchain': '<YOUR_DRIVE_ID>',
    # Live doc verified 29 Jul 2026. The ID printed in the Master Product List
    # markdown (1QPv80qG...) returns 404 and must not be reused.
    'OMS': '<YOUR_DRIVE_ID>',
    'Mixed Payment': None,  # Add Master Doc ID when created
    'Fulfillment Service': None,  # Add Master Doc ID when created
}

# Aliases for flexible component matching
COMPONENT_ALIASES = {
    'iam': 'IAM (Identity Access Management)',
    'identity': 'IAM (Identity Access Management)',
    'gamification': 'Gamification',
    'gamif': 'Gamification',
    'promotion': 'Promotion Engine',
    'promo': 'Promotion Engine',
    'voucher': 'Promotion Engine',
    'blockchain': 'Blockchain',
    'crypto': 'Blockchain',
    'mixed payment': 'Mixed Payment',
    'payment': 'Mixed Payment',
    'oms': 'OMS',
    'order management': 'OMS',
    'fulfillment': 'Fulfillment Service',
    'fulfilment': 'Fulfillment Service',
    'fulfillment service': 'Fulfillment Service',
    'fulfilment service': 'Fulfillment Service',
}

# Global timeout: 180 seconds
def timeout_handler(signum, frame):
    print("[ERROR] Work Link Sync timed out after 180 seconds", file=sys.stderr)
    sys.exit(1)

if os.name != 'nt': # signal.alarm is Unix-only
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(180)

def resolve_component(component_input):
    lower = component_input.lower().strip()
    if lower in COMPONENT_ALIASES:
        return COMPONENT_ALIASES[lower]
    # Try direct match (case-insensitive)
    for canonical in MASTER_DOCS:
        if canonical.lower() == lower:
            return canonical
    return component_input  # Return as-is if no match

def update_spreadsheet(sheets, component, url, name, feature=None, force=False):
    """Update the Documents/Links column for rows matching the component.

    The sheet is L0: Product | L1: Component | L2: Feature | Documents/Links.
    Matching is on L1 under ANY L0. The original version required L0 to be the
    literal string 'Shared Components', so a real row under 'Ecom Solutions'
    reported 'no rows found' and silently did nothing (seen 29 Jul 2026 on the
    Work Fulfillment Service PRD).

    A component usually spans several features, and column D is per feature, so
    writing one link across every row of a component would destroy the other
    features' links. When the matched rows span more than one feature, --feature
    is required.
    """
    result = sheets.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range=f"'{SHEET_NAME}'!A1:H400",
        valueRenderOption='FORMULA'
    ).execute()

    rows = result.get('values', [])
    formula = f'=HYPERLINK("{url}","{name}")'

    matched = []
    for i, row in enumerate(rows, start=1):
        if not row or i == 1:
            continue
        l1 = row[1].strip() if len(row) > 1 else ''
        l2 = row[2].strip() if len(row) > 2 else ''
        cur = row[3] if len(row) > 3 else ''
        if l1 != component:
            continue
        if feature and l2 != feature:
            continue
        matched.append((i, l2, cur))

    if not matched:
        scope = f"component '{component}'" + (f", feature '{feature}'" if feature else "")
        print(f"  ⚠ Spreadsheet: No rows found for {scope}")
        return 0

    features = sorted({l2 for _, l2, _ in matched})
    if not feature and len(features) > 1:
        print(f"  ⚠ Spreadsheet: '{component}' spans {len(features)} features. "
              f"Pass --feature to avoid overwriting the others:")
        for f in features:
            print(f"      --feature \"{f}\"")
        return 0

    updates, already, clobber = [], 0, []
    for i, _l2, cur in matched:
        if url in str(cur):
            already += 1
            continue
        if str(cur).strip() and not force:
            clobber.append((i, cur))
            continue
        updates.append({'range': f"'{SHEET_NAME}'!D{i}", 'values': [[formula]]})

    if clobber and not force:
        print(f"  ⚠ Spreadsheet: {len(clobber)} row(s) already hold a different link. "
              f"Re-run with --force to replace them:")
        for i, cur in clobber[:5]:
            print(f"      row {i}: {str(cur)[:90]}")
        return 0

    if updates:
        sheets.spreadsheets().values().batchUpdate(
            spreadsheetId=SHEET_ID,
            body={'valueInputOption': 'USER_ENTERED', 'data': updates}
        ).execute()
        print(f"  ✓ Spreadsheet: Updated {len(updates)} row(s) for '{component}'"
              + (f" / '{feature}'" if feature else ""))
    if already:
        print(f"  ✓ Spreadsheet: {already} row(s) already linked to this URL, left alone")
    return len(updates)

def _doc_text(doc):
    out = []
    for el in doc.get('body', {}).get('content', []):
        para = el.get('paragraph')
        if not para:
            continue
        for run in para.get('elements', []):
            out.append(run.get('textRun', {}).get('content', ''))
    return ''.join(out)

def update_master_doc(creds, component, url, name, doc_type):
    """Add the new link to the Master Doc's Related Documents section.

    Idempotent: a URL already present in the doc is left alone. When there is no
    Related Documents section, one is created with a real heading instead of
    dropping a stray table-shaped line after whatever the doc happened to end on.
    """
    master_doc_id = MASTER_DOCS.get(component)
    if not master_doc_id:
        print(f"  ⚠ Master Doc: No Master Doc registered for '{component}'")
        return False

    docs = build('docs', 'v1', credentials=creds)
    doc = docs.documents().get(documentId=master_doc_id).execute()

    if url in _doc_text(doc):
        print(f"  ✓ Master Doc: '{name}' already linked, left alone")
        return True

    heading_idx = None
    for el in doc.get('body', {}).get('content', []):
        para = el.get('paragraph')
        if not para:
            continue
        text = ''.join(r.get('textRun', {}).get('content', '') for r in para.get('elements', []))
        if text.strip().lower().startswith('related documents'):
            heading_idx = el['endIndex'] - 1
            break

    if heading_idx is not None:
        insert_at = heading_idx
        payload = f"\n{name}, {doc_type.upper()}: {url}"
    else:
        insert_at = doc['body']['content'][-1]['endIndex'] - 1
        payload = f"\nRelated Documents\n{name}, {doc_type.upper()}: {url}"

    docs.documents().batchUpdate(
        documentId=master_doc_id,
        body={'requests': [{'insertText': {'location': {'index': insert_at}, 'text': payload}}]}
    ).execute()

    where = "Related Documents" if heading_idx is not None else "a new Related Documents section"
    print(f"  ✓ Master Doc: Added '{name}' to {where}")
    print(f"    https://docs.google.com/document/d/{master_doc_id}/edit")
    return True

def main():
    parser = argparse.ArgumentParser(description='Work Link Sync — auto-link new files to Master Docs & Spreadsheet')
    parser.add_argument('--url', required=True, help='Google Doc URL of the new file')
    parser.add_argument('--name', required=True, help='Display name for the link')
    parser.add_argument('--component', required=True, help='Shared component name (e.g., "IAM", "Blockchain")')
    parser.add_argument('--type', default='reference', choices=['prd', 'master', 'reference'],
                        help='Type of document: prd, master, or reference')
    parser.add_argument('--feature', default=None,
                        help='L2 feature name. Required when the component spans several features')
    parser.add_argument('--force', action='store_true',
                        help='Replace an existing different link in Documents/Links')
    parser.add_argument('--spreadsheet-only', action='store_true',
                        help='Only update spreadsheet, skip Master Doc update')

    args = parser.parse_args()

    component = resolve_component(args.component)
    print(f"\nWork Link Sync")
    print(f"  File    : {args.name}")
    print(f"  URL     : {args.url}")
    print(f"  Component: {component}")
    print(f"  Type    : {args.type}")
    print()

    creds = authenticate()
    sheets = build('sheets', 'v4', credentials=creds)

    # Update spreadsheet
    updated = update_spreadsheet(sheets, component, args.url, args.name,
                                 feature=args.feature, force=args.force)

    # Update Master Doc (skip if --spreadsheet-only or if this IS a master doc)
    if not args.spreadsheet_only and args.type != 'master':
        try:
            update_master_doc(creds, component, args.url, args.name, args.type)
        except Exception as e:
            print(f"  ⚠ Master Doc update failed (Docs API may not be enabled): {e}")
            print(f"  → Manually add to Master Doc: {MASTER_DOCS.get(component, 'Not registered')}")

    print(f"\nDone. {updated} spreadsheet rows updated.")

if __name__ == '__main__':
    main()
