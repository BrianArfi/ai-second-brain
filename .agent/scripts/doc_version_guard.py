#!/usr/bin/env python3
"""Stop a Google Doc's revision number going backwards.

Two failures produced the BRD - ExampleProgram Store Earn Model regression (v2.1, v2.2,
then v2.1 again on 20 Jul 2026):

1. The repo mirror was two revisions behind, because v2.1 (1 Jul) and v2.2 (2 Jul)
   were typed straight into the Doc and never pulled back. The writer bumped from
   the stale local table, not from the Doc.
2. Nothing compared the new version against the versions already in the Doc, so
   the duplicate row was appended without complaint.

This module closes both. `check_new_version` is called from gdoc_surgical before a
revision row is written; the CLI does the same check plus an optional mirror
staleness comparison, for writers that push a whole file instead of a row.

    python3 .agent/scripts/doc_version_guard.py check \
        --id <doc-id> --new-version v2.3 \
        --mirror Clients/Work/.../BRD_....md
"""
import argparse
import re
import sys

VERSION_RE = re.compile(r'^v?(\d+)\.(\d+)$', re.I)

def parse_version(text):
    """Return a (major, minor) tuple, or None if this is not a version token."""
    m = VERSION_RE.match((text or '').strip())
    return (int(m.group(1)), int(m.group(2))) if m else None

def versions_in_column(rows):
    """Version tokens found in the first column of a revision table, in order.

    `rows` is a list of lists of cell strings.
    """
    found = []
    for row in rows:
        if not row:
            continue
        v = parse_version(row[0])
        if v:
            found.append((v, row[0].strip()))
    return found

def fmt(v):
    return f"v{v[0]}.{v[1]}"

def check_new_version(new_version, existing_rows, where='the document'):
    """Raise ValueError if `new_version` is not strictly above every existing one.

    Returns None when there is nothing to check (no version token, or the table
    holds no versions), so non-revision tables pass straight through.
    """
    new = parse_version(new_version)
    if not new:
        return None
    existing = versions_in_column(existing_rows)
    if not existing:
        return None
    highest, highest_raw = max(existing, key=lambda pair: pair[0])
    if new > highest:
        return None
    dupes = [raw for v, raw in existing if v == new]
    detail = (f"{fmt(new)} already appears in {where}"
              if dupes else
              f"{fmt(new)} is below {highest_raw}, the highest version in {where}")
    raise ValueError(
        f"version regression refused: {detail}. "
        f"Existing: {', '.join(raw for _, raw in existing)}. "
        f"Re-pull the document before editing, then bump from its last row "
        f"(next would be at least v{highest[0]}.{highest[1] + 1}). "
        f"Override with --allow-version-regression only when the duplicate is intentional."
    )

def rows_from_markdown(path):
    """Revision rows from a markdown mirror's first pipe table."""
    rows = []
    for line in open(path, encoding='utf-8'):
        line = line.strip()
        if not line.startswith('|'):
            continue
        cells = [c.strip() for c in line.strip('|').split('|')]
        if cells and parse_version(cells[0]):
            rows.append(cells)
    return rows

def _doc_rows(doc_id, account):
    sys.path.insert(0, '.agent/skills/gdoc-surgical')
    from gdoc_surgical import docs_service, get_doc, walk_body, _cell_text  # noqa: E402
    docs = docs_service(account)
    doc = get_doc(docs, doc_id)
    best = []
    for kind, el in walk_body(doc):
        if kind != 'table':
            continue
        rows = [[_cell_text(c) for c in r['tableCells']]
                for r in el['table']['tableRows']]
        found = versions_in_column(rows)
        if len(found) > len(versions_in_column(best)):
            best = rows
    return best

def main():
    p = argparse.ArgumentParser(description='Refuse Google Doc revision numbers that go backwards')
    p.add_argument('command', choices=['check'])
    p.add_argument('--id', required=True, help='Google Doc ID')
    p.add_argument('--new-version', required=True, help='the version about to be written, e.g. v2.3')
    p.add_argument('--account', default='work')
    p.add_argument('--mirror', help='local markdown mirror, checked for staleness against the Doc')
    args = p.parse_args()

    doc_rows = _doc_rows(args.id, args.account)
    doc_versions = [raw for _, raw in versions_in_column(doc_rows)]
    print(f"[doc] {', '.join(doc_versions) or '(no revision table found)'}")

    failed = False
    if args.mirror:
        mirror_versions = [raw for _, raw in versions_in_column(rows_from_markdown(args.mirror))]
        print(f"[mirror] {', '.join(mirror_versions) or '(no revision table found)'}")
        missing = [v for v in doc_versions if v not in mirror_versions]
        if missing:
            print(f"[STALE] the mirror is missing {', '.join(missing)}. Re-pull before editing.")
            failed = True

    try:
        check_new_version(args.new_version, doc_rows)
        print(f"[OK] {args.new_version} is above every version in the document.")
    except ValueError as e:
        print(f"[BLOCKED] {e}")
        failed = True

    sys.exit(1 if failed else 0)

if __name__ == '__main__':
    main()
