#!/usr/bin/env python3
"""
GDoc Surgical Editor - make targeted in-place edits to a Google Doc WITHOUT
re-uploading/overwriting it (preserves hand edits, comments, sharing, images).

Uses the same Drive-scoped OAuth token as the drive connectors: a token with
https://www.googleapis.com/auth/drive is valid for the Docs API too.

Commands:
  read        Print the doc's text with paragraph indexes (find your target first)
  replace     Replace ALL occurrences of exact text (Docs API replaceAllText)
  linkify     Hyperlink ALL occurrences of exact text to a URL, without changing the text
  append      Append markdown-ish text (headings/bullets/plain) to the end of the doc
  insert-row  Insert a row into table N, filled with cell texts
  list-tables Show every table with its index, size, and first-row preview

Usage:
  python3 gdoc_surgical.py read        --id DOC_ID --account work
  python3 gdoc_surgical.py replace     --id DOC_ID --find "old text" --with "new text" [--match-case]
  python3 gdoc_surgical.py linkify     --id DOC_ID --find "Q3 Roadmap" --url "https://docs.google.com/document/d/..."
  python3 gdoc_surgical.py append      --id DOC_ID --text "## New Section\nBody line"
  python3 gdoc_surgical.py list-tables --id DOC_ID
  python3 gdoc_surgical.py insert-row  --id DOC_ID --table 0 --cells "Col A|Col B|Col C" [--row -1]

Rules of engagement (see SKILL.md):
  - ALWAYS `read` first and verify the target text is unique enough.
  - `replace` hits EVERY occurrence - if the find-string is short/common, widen it.
  - Never use this to rewrite a whole doc; that's what the drive connector's
    `update --convert` is for (and it wipes images - prefer surgical).
"""

import os
import re
import sys
import time
import signal
import argparse

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SKILL_DIR, '..', '..', '..'))

sys.path.insert(0, os.path.join(REPO_ROOT, '.agent', 'scripts'))
from file_utils import assert_drive_result  # Drive Operation Verification (CLAUDE.md)

# Account name -> connector dir holding token.json (same map as gdocs-create)
ACCOUNTS = {
    'work':    os.path.join(REPO_ROOT, '.agent/skills/work-drive-connector'),
    'personal': os.path.join(REPO_ROOT, '.agent/skills/personal-drive-connector'),
    'secondary': os.path.join(REPO_ROOT, '.agent/skills/secondary-drive-connector'),
}

SCOPES = ['https://www.googleapis.com/auth/drive']

def timeout_handler(signum, frame):
    print("[ERROR] Timed out after 180 seconds", file=sys.stderr)
    sys.exit(1)

if os.name != 'nt':
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(180)

def authenticate(account: str):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if account not in ACCOUNTS:
        print(f"[ERROR] Unknown account '{account}'. Choose from: {list(ACCOUNTS.keys())}")
        sys.exit(1)
    token_path = os.path.join(ACCOUNTS[account], 'token.json')
    if not os.path.exists(token_path):
        print(f"[ERROR] Token not found: {token_path} - run that connector's auth flow first.")
        sys.exit(1)
    creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(token_path, 'w') as f:
                f.write(creds.to_json())
        else:
            print(f"[ERROR] Token invalid and cannot be refreshed for '{account}'. Re-auth needed.")
            sys.exit(1)
    return creds

def docs_service(account: str):
    from googleapiclient.discovery import build
    return build('docs', 'v1', credentials=authenticate(account))

def get_doc(docs, doc_id):
    return docs.documents().get(documentId=doc_id).execute()

def batch(docs, doc_id, requests):
    if not requests:
        print("[INFO] Nothing to do.")
        return None
    return docs.documents().batchUpdate(
        documentId=doc_id, body={'requests': requests}).execute()

# ── Structure walking ─────────────────────────────────────────────────────────

def _para_text(element):
    """Plain text of a paragraph element."""
    out = []
    for run in element.get('paragraph', {}).get('elements', []):
        out.append(run.get('textRun', {}).get('content', ''))
    return ''.join(out)

def walk_body(doc):
    """Yield (kind, element) for top-level body elements. kind: paragraph|table|other."""
    for el in doc.get('body', {}).get('content', []):
        if 'paragraph' in el:
            yield 'paragraph', el
        elif 'table' in el:
            yield 'table', el
        else:
            yield 'other', el

# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_read(docs, args):
    doc = get_doc(docs, args.id)
    print(f"# {doc.get('title')}  (docId: {args.id})\n")
    t_idx = 0
    for kind, el in walk_body(doc):
        if kind == 'paragraph':
            text = _para_text(el).rstrip('\n')
            if text.strip():
                style = el['paragraph'].get('paragraphStyle', {}).get('namedStyleType', '')
                prefix = {'HEADING_1': '# ', 'HEADING_2': '## ', 'HEADING_3': '### ',
                          'HEADING_4': '#### ', 'TITLE': '=== '}.get(style, '')
                print(f"[{el['startIndex']}-{el['endIndex']}] {prefix}{text}")
        elif kind == 'table':
            tbl = el['table']
            print(f"[{el['startIndex']}-{el['endIndex']}] <TABLE #{t_idx}: "
                  f"{tbl.get('rows')}x{tbl.get('columns')}>")
            t_idx += 1

def cmd_replace(docs, args):
    # Safety: show occurrence count before writing
    doc = get_doc(docs, args.id)
    full_text = ''.join(_para_text(el) if kind == 'paragraph' else ''
                        for kind, el in walk_body(doc))
    flags = 0 if args.match_case else re.IGNORECASE
    n = len(re.findall(re.escape(args.find), full_text, flags))
    # Note: count above misses text inside tables; the API replace still hits it.
    print(f"[INFO] '{args.find}' found {n}x in body paragraphs (table cells not counted; replace hits those too).")
    result = batch(docs, args.id, [{
        'replaceAllText': {
            'containsText': {'text': args.find, 'matchCase': bool(args.match_case)},
            'replaceText': getattr(args, 'with'),
        }
    }])
    assert_drive_result(result, 'gdoc_surgical replace')
    changed = result['replies'][0].get('replaceAllText', {}).get('occurrencesChanged', 0)
    print(f"[OK] Replaced {changed} occurrence(s). Doc: https://docs.google.com/document/d/{args.id}/edit")
    if changed == 0:
        print("[WARN] Nothing replaced - check exact wording/case with `read` first.")
        sys.exit(2)

def cmd_linkify(docs, args):
    """Hyperlink every occurrence of --find to --url, leaving the visible text
    unchanged. Walks paragraphs at the top level and inside table cells."""
    doc = get_doc(docs, args.id)
    requests = []

    def walk_and_link(elements):
        for el in elements:
            if 'paragraph' in el:
                text = _para_text(el)
                start = 0
                while True:
                    idx = text.find(args.find, start)
                    if idx == -1:
                        break
                    abs_start = el['startIndex'] + idx
                    abs_end = abs_start + len(args.find)
                    requests.append({'updateTextStyle': {
                        'range': {'startIndex': abs_start, 'endIndex': abs_end},
                        'textStyle': {'link': {'url': args.url}},
                        'fields': 'link',
                    }})
                    start = idx + len(args.find)
            elif 'table' in el:
                for row in el['table']['tableRows']:
                    for cell in row['tableCells']:
                        walk_and_link(cell.get('content', []))

    walk_and_link(doc['body']['content'])
    if not requests:
        print(f"[WARN] {args.find!r} not found anywhere in the doc - nothing linked.")
        sys.exit(2)
    # updateTextStyle never changes text length, so every request's indices stay
    # valid against the original document regardless of batch order.
    result = batch(docs, args.id, requests)
    assert_drive_result(result, 'gdoc_surgical linkify')
    print(f"[OK] Linked {len(requests)} occurrence(s) of {args.find!r} -> {args.url}")
    print(f"Doc: https://docs.google.com/document/d/{args.id}/edit")

def cmd_append(docs, args):
    doc = get_doc(docs, args.id)
    end_index = doc['body']['content'][-1]['endIndex'] - 1  # before final newline
    text = args.text.replace('\\n', '\n')
    requests = []
    cursor = end_index
    for line in text.split('\n'):
        m = re.match(r'^(#{1,4})\s+(.*)$', line)
        bullet = re.match(r'^[-*]\s+(.*)$', line)
        content = (m.group(2) if m else bullet.group(1) if bullet else line) + '\n'
        requests.append({'insertText': {'location': {'index': cursor}, 'text': content}})
        seg = {'startIndex': cursor, 'endIndex': cursor + len(content)}
        if m:
            style = f'HEADING_{len(m.group(1))}'
            requests.append({'updateParagraphStyle': {
                'range': seg, 'paragraphStyle': {'namedStyleType': style},
                'fields': 'namedStyleType'}})
        elif bullet:
            requests.append({'createParagraphBullets': {
                'range': seg, 'bulletPreset': 'BULLET_DISC_CIRCLE_SQUARE'}})
        else:
            requests.append({'updateParagraphStyle': {
                'range': seg, 'paragraphStyle': {'namedStyleType': 'NORMAL_TEXT'},
                'fields': 'namedStyleType'}})
        cursor += len(content)
    result = batch(docs, args.id, requests)
    assert_drive_result(result, 'gdoc_surgical append')
    print(f"[OK] Appended {len(text.splitlines())} line(s). Doc: https://docs.google.com/document/d/{args.id}/edit")

def cmd_list_tables(docs, args):
    doc = get_doc(docs, args.id)
    t_idx = 0
    for kind, el in walk_body(doc):
        if kind != 'table':
            continue
        tbl = el['table']
        first_row = tbl['tableRows'][0] if tbl.get('tableRows') else None
        headers = []
        if first_row:
            for cell in first_row.get('tableCells', []):
                cell_text = ''.join(
                    _para_text(c) for c in cell.get('content', []) if 'paragraph' in c
                ).strip()
                headers.append(cell_text)
        print(f"TABLE #{t_idx}: {tbl.get('rows')}x{tbl.get('columns')} "
              f"@ startIndex {el['startIndex']} | header: {' | '.join(headers)}")
        t_idx += 1
    if t_idx == 0:
        print("[INFO] No tables in this doc.")

def _guard_version(args, tbl_el, new_cells):
    """Refuse a revision row whose version is not above every version already there.

    See .agent/scripts/doc_version_guard.py for why this exists.
    """
    if getattr(args, 'allow_version_regression', False) or not new_cells:
        return
    sys.path.insert(0, os.path.join(SKILL_DIR, '..', '..', 'scripts'))
    from doc_version_guard import check_new_version
    rows = [[_cell_text(c) for c in r['tableCells']]
            for r in tbl_el['table']['tableRows']]
    try:
        check_new_version(new_cells[0], rows, where='this document')
    except ValueError as e:
        print(f"[BLOCKED] {e}")
        sys.exit(1)

def cmd_insert_row(docs, args):
    doc = get_doc(docs, args.id)
    tables = [el for kind, el in walk_body(doc) if kind == 'table']
    if args.table >= len(tables) or args.table < 0:
        print(f"[ERROR] Table #{args.table} not found (doc has {len(tables)}). Use list-tables.")
        sys.exit(1)
    tbl_el = tables[args.table]
    tbl = tbl_el['table']
    n_rows, n_cols = tbl['rows'], tbl['columns']
    _guard_version(args, tbl_el, args.cells.split('|') if args.cells else [])
    row_idx = args.row if args.row >= 0 else n_rows - 1  # -1 = insert below last row

    # Step 1: insert the empty row
    insert_result = batch(docs, args.id, [{
        'insertTableRow': {
            'tableCellLocation': {
                'tableStartLocation': {'index': tbl_el['startIndex']},
                'rowIndex': row_idx, 'columnIndex': 0,
            },
            'insertBelow': True,
        }
    }])
    # This is the write that matters; step 2 below can legitimately be a no-op
    # (all cell values blank), so verify here rather than on the final batch().
    assert_drive_result(insert_result, 'gdoc_surgical insert-row')

    # Step 2: re-fetch (indexes shifted) and fill cells RIGHT-TO-LEFT so earlier
    # insertions don't shift later targets.
    cells = args.cells.split('|')
    if len(cells) > n_cols:
        print(f"[WARN] {len(cells)} cell values for {n_cols} columns - extra values dropped.")
        cells = cells[:n_cols]
    time.sleep(1)
    doc = get_doc(docs, args.id)
    tables = [el for kind, el in walk_body(doc) if kind == 'table']
    new_row = tables[args.table]['table']['tableRows'][row_idx + 1]
    requests = []
    for cell, value in reversed(list(zip(new_row['tableCells'], cells))):
        if value.strip():
            # First paragraph of the cell starts right after the cell's startIndex
            requests.append({'insertText': {
                'location': {'index': cell['startIndex'] + 1},
                'text': value.strip(),
            }})
    batch(docs, args.id, requests)
    print(f"[OK] Row inserted into table #{args.table} at row {row_idx + 1} "
          f"with {len(cells)} cell(s). Doc: https://docs.google.com/document/d/{args.id}/edit")

def _cell_text(cell):
    return ''.join(
        _para_text(c) for c in cell.get('content', []) if 'paragraph' in c
    ).strip()

def cmd_set_cell(docs, args):
    """Replace the text of ONE table cell.

    `replace` is global and cannot target a cell whose whole content is a common
    string - a version cell reading "1.4" would also rewrite every "1.4" in the
    changelog. This addresses the cell by coordinate instead.
    """
    doc = get_doc(docs, args.id)
    tables = [el for kind, el in walk_body(doc) if kind == 'table']
    if args.table >= len(tables) or args.table < 0:
        print(f"[ERROR] Table #{args.table} not found (doc has {len(tables)}). Use list-tables.")
        sys.exit(1)
    tbl = tables[args.table]['table']
    if args.row < 0 or args.row >= tbl['rows']:
        print(f"[ERROR] Row {args.row} out of range (table #{args.table} has {tbl['rows']} rows).")
        sys.exit(1)
    if args.col < 0 or args.col >= tbl['columns']:
        print(f"[ERROR] Column {args.col} out of range (table #{args.table} has {tbl['columns']} columns).")
        sys.exit(1)

    cell = tbl['tableRows'][args.row]['tableCells'][args.col]
    current = _cell_text(cell)
    if args.col == 0:
        _guard_version(args, tables[args.table], [getattr(args, 'with') or ''])
    if args.expect is not None and args.expect not in current:
        print(f"[ERROR] Cell ({args.row},{args.col}) of table #{args.table} does not contain --expect text.")
        print(f"        expected: {args.expect!r}")
        print(f"        cell is:  {current[:300]!r}")
        print("        Nothing changed.")
        sys.exit(2)

    text_start, text_end = cell['startIndex'] + 1, cell['endIndex'] - 1
    requests = []
    if text_end > text_start:
        requests.append({'deleteContentRange': {
            'range': {'startIndex': text_start, 'endIndex': text_end}
        }})
    value = getattr(args, 'with')
    if value:
        requests.append({'insertText': {'location': {'index': text_start}, 'text': value}})

    result = batch(docs, args.id, requests)
    assert_drive_result(result, 'gdoc_surgical set-cell')
    print(f"[OK] Table #{args.table} cell ({args.row},{args.col}): {current[:60]!r} -> {value[:60]!r}. "
          f"Doc: https://docs.google.com/document/d/{args.id}/edit")

def cmd_insert_table(docs, args):
    """Append a table at the end of the doc and fill it.

    --rows is pipe-separated cells, one row per `--rows` flag repetition, so
    column counts are taken from the first row.
    """
    rows = [r.split('|') for r in args.rows]
    n_rows, n_cols = len(rows), max(len(r) for r in rows)

    create = batch(docs, args.id, [{
        'insertTable': {'endOfSegmentLocation': {}, 'rows': n_rows, 'columns': n_cols}
    }])
    assert_drive_result(create, 'gdoc_surgical insert-table')

    # Re-fetch: every index below shifted. Fill bottom-right to top-left so an
    # earlier insertion never moves a target that has not been written yet.
    time.sleep(1)
    doc = get_doc(docs, args.id)
    tables = [el for kind, el in walk_body(doc) if kind == 'table']
    new_tbl = tables[-1]['table']
    requests = []
    for r_i in range(n_rows - 1, -1, -1):
        cells = rows[r_i]
        for c_i in range(min(len(cells), n_cols) - 1, -1, -1):
            value = cells[c_i].strip()
            if not value:
                continue
            cell = new_tbl['tableRows'][r_i]['tableCells'][c_i]
            requests.append({'insertText': {
                'location': {'index': cell['startIndex'] + 1}, 'text': value,
            }})
    batch(docs, args.id, requests)
    print(f"[OK] Table {n_rows}x{n_cols} appended as table #{len(tables) - 1}. "
          f"Doc: https://docs.google.com/document/d/{args.id}/edit")

def cmd_delete_row(docs, args):
    doc = get_doc(docs, args.id)
    tables = [el for kind, el in walk_body(doc) if kind == 'table']
    if args.table >= len(tables) or args.table < 0:
        print(f"[ERROR] Table #{args.table} not found (doc has {len(tables)}). Use list-tables.")
        sys.exit(1)
    tbl_el = tables[args.table]
    tbl = tbl_el['table']
    n_rows = tbl['rows']
    if args.row < 0 or args.row >= n_rows:
        print(f"[ERROR] Row {args.row} out of range (table #{args.table} has {n_rows} rows).")
        sys.exit(1)

    row = tbl['tableRows'][args.row]
    row_text = ' | '.join(
        ''.join(_para_text(c) for c in cell.get('content', []) if 'paragraph' in c).strip()
        for cell in row.get('tableCells', [])
    )

    # Deleting a row is irreversible and row indexes shift under any concurrent
    # edit, so the caller must name text they expect to find in the row. This is
    # the same "read before writing" rule the other commands enforce by making
    # replace exit 2 on zero matches.
    if args.expect not in row_text:
        print(f"[ERROR] Row {args.row} of table #{args.table} does not contain --expect text.")
        print(f"        expected: {args.expect!r}")
        print(f"        row is:   {row_text[:300]!r}")
        print("        Nothing deleted. Re-run list-tables/read and check the row index.")
        sys.exit(2)

    print(f"[INFO] Deleting table #{args.table} row {args.row}: {row_text[:200]}")
    result = batch(docs, args.id, [{
        'deleteTableRow': {
            'tableCellLocation': {
                'tableStartLocation': {'index': tbl_el['startIndex']},
                'rowIndex': args.row, 'columnIndex': 0,
            }
        }
    }])
    assert_drive_result(result, 'gdoc_surgical delete-row')
    print(f"[OK] Row {args.row} deleted from table #{args.table} "
          f"({n_rows} -> {n_rows - 1} rows). "
          f"Doc: https://docs.google.com/document/d/{args.id}/edit")

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description='Surgical in-place Google Doc edits')
    p.add_argument('command', choices=['read', 'replace', 'linkify', 'append', 'insert-row', 'delete-row',
                                       'set-cell', 'insert-table', 'list-tables'])
    p.add_argument('--id', required=True, help='Google Doc ID')
    p.add_argument('--account', default='work', choices=list(ACCOUNTS.keys()))
    p.add_argument('--find', help='replace/linkify: exact text to find')
    p.add_argument('--with', dest='with', help='replace: replacement text')
    p.add_argument('--url', help='linkify: URL to hyperlink --find to')
    p.add_argument('--match-case', action='store_true', help='replace: case-sensitive')
    p.add_argument('--text', help='append: text to append (\\n for newlines, #/## headings, - bullets)')
    p.add_argument('--table', type=int, default=0, help='insert-row/delete-row: table index (see list-tables)')
    p.add_argument('--row', type=int, default=-1, help='insert-row: insert below this 0-based row (-1 = last). delete-row: the 0-based row to delete')
    p.add_argument('--cells', help='insert-row: pipe-separated cell values "A|B|C"')
    p.add_argument('--expect', help='delete-row/set-cell: text that MUST appear in the target before it is changed (safety guard)')
    p.add_argument('--allow-version-regression', action='store_true',
                   help='insert-row/set-cell: permit a revision number at or below one already in the doc')
    p.add_argument('--col', type=int, default=0, help='set-cell: 0-based column')
    p.add_argument('--rows', action='append', help='insert-table: one pipe-separated row per flag, repeat for each row')
    args = p.parse_args()

    docs = docs_service(args.account)
    if args.command == 'read':
        cmd_read(docs, args)
    elif args.command == 'replace':
        if not args.find or getattr(args, 'with') is None:
            p.error('replace requires --find and --with')
        cmd_replace(docs, args)
    elif args.command == 'linkify':
        if not args.find or not args.url:
            p.error('linkify requires --find and --url')
        cmd_linkify(docs, args)
    elif args.command == 'append':
        if not args.text:
            p.error('append requires --text')
        cmd_append(docs, args)
    elif args.command == 'list-tables':
        cmd_list_tables(docs, args)
    elif args.command == 'insert-row':
        if not args.cells:
            p.error('insert-row requires --cells')
        cmd_insert_row(docs, args)
    elif args.command == 'set-cell':
        if getattr(args, 'with') is None:
            p.error('set-cell requires --with (use "" to blank the cell)')
        if args.row < 0:
            p.error('set-cell requires an explicit --row (0-based)')
        cmd_set_cell(docs, args)
    elif args.command == 'insert-table':
        if not args.rows:
            p.error('insert-table requires at least one --rows "a|b|c"')
        cmd_insert_table(docs, args)
    elif args.command == 'delete-row':
        if args.row < 0:
            p.error('delete-row requires an explicit --row (0-based); there is no default')
        if not args.expect:
            p.error('delete-row requires --expect, text that must appear in the row being deleted')
        cmd_delete_row(docs, args)

if __name__ == '__main__':
    main()
