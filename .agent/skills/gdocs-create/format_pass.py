#!/usr/bin/env python3
"""
gdocs-create formatting pass (run AFTER every create-doc / update --convert with tables).

Does three things:
  1. Sets the document to PAGELESS mode (markdown->GDoc conversion creates PAGES/letter
     docs; the owner wants Work docs pageless). Skip with --keep-pages.
  2. Widens table columns to fill the width on pageless GDocs (conversion leaves the
     legacy ~468pt total, which looks cramped for wide tables). Widths are CONTENT-AWARE:
     measured per-column from actual cell text (dampened for wrapping), not a fixed
     profile keyed by column count. Pass --legacy-weights to fall back to the old
     column-count-only profiles.
  3. Lints the rendered doc for AI-tell artifacts that survive conversion and read
     messy: literal " -- " / "--" and "->". These MUST be rephrased in the source
     (colon/comma/restructure per feedback_no_emdash_rephrase), never left as dashes.
     Exits non-zero if any are found so the caller re-converts instead of sharing.

NOTE: `update --convert` resets documentMode back to PAGES and re-publishes the doc
public every time, so run this pass (and drive_permissions.py restrict) AFTER the
final convert, not before.

Usage:
  python3 .agent/skills/gdocs-create/format_pass.py <doc_id> [<doc_id> ...] \
      [--account work|personal|secondary] [--total-width 700] [--keep-pages]

Auth reuses the same token as gdocs_create.py (per --account).
"""
import os, sys, argparse
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
ACCOUNTS = {
    'work':     os.path.join(REPO, '.agent/skills/work-drive-connector/token.json'),
    'personal':  os.path.join(REPO, '.agent/skills/personal-drive-connector/token.json'),
    'secondary': os.path.join(REPO, '.agent/skills/secondary-drive-connector/token.json'),
}
SCOPES = ['https://www.googleapis.com/auth/documents', 'https://www.googleapis.com/auth/drive']

# LEGACY per-column-count weight profiles (sum to 1.0). Kept only for --legacy-weights
# rollback; the default path measures actual cell content instead (see col_demands).
WEIGHTS = {
    2: [0.22, 0.78],
    3: [0.07, 0.51, 0.42],
    4: [0.06, 0.50, 0.24, 0.20],
    5: [0.05, 0.42, 0.20, 0.20, 0.13],
}

# content-aware width tuning
CAP = 160          # chars; beyond this a cell wraps regardless of width granted
MIN_COL_PT = 46.0  # floor so a short label/tier column stays readable

def cell_text(cell):
    """Flatten a table cell's paragraphs into whitespace-collapsed plain text."""
    parts = []
    for el in cell.get('content', []):
        p = el.get('paragraph')
        if p:
            for r in p.get('elements', []):
                parts.append(r.get('textRun', {}).get('content', ''))
    return ' '.join(''.join(parts).split())

def col_demands(t, cap=CAP):
    """Per-column 'demand' from measured cell text, dampened so one long cell
    (which wraps) doesn't dominate, blended so a consistently-wide column beats
    a column with a single long outlier."""
    ncols = t['columns']
    demands = []
    for ci in range(ncols):
        lens = []
        for row in t['tableRows']:
            cells = row['tableCells']
            if ci < len(cells):
                lens.append(len(cell_text(cells[ci])))
        if not lens:
            lens = [0]
        damp = [min(L, cap) ** 0.5 for L in lens]
        d = 0.35 * max(damp) + 0.65 * (sum(damp) / len(damp))
        demands.append(max(d, 3 ** 0.5))
    return demands

def max_col_pt(ncols, total):
    return min(0.85, 2.2 / ncols) * total

def solve_widths(demands, total, min_pt=MIN_COL_PT, max_pt=None):
    """Water-fill normalization: distribute `total` across columns proportional
    to demand, clamping each column into [min_pt, max_pt] and redistributing the
    remainder among still-free columns. Widths always sum to `total`."""
    ncols = len(demands)
    if ncols == 1:
        return [total]
    if max_pt is None:
        max_pt = total
    if min_pt * ncols >= total:
        return [total / ncols] * ncols

    free = set(range(ncols))
    fixed = {}
    for _ in range(ncols):
        remaining = total - sum(fixed.values())
        wsum = sum(demands[j] for j in free)
        if wsum <= 0 or not free:
            break
        changed = False
        for j in list(free):
            w = remaining * demands[j] / wsum
            if w < min_pt:
                fixed[j] = min_pt; free.discard(j); changed = True
            elif w > max_pt:
                fixed[j] = max_pt; free.discard(j); changed = True
        if not changed:
            for j in free:
                fixed[j] = remaining * demands[j] / wsum
            free = set()
            break
    if free:  # loop exhausted without settling; equal-split whatever remains
        remaining = total - sum(fixed.values())
        share = remaining / len(free)
        for j in free:
            fixed[j] = share

    widths = [fixed.get(j, total / ncols) for j in range(ncols)]
    s = sum(widths)
    return [w * total / s for w in widths] if s > 0 else [total / ncols] * ncols

def svc(account):
    tok = ACCOUNTS[account]
    c = Credentials.from_authorized_user_file(tok, SCOPES)
    if not c.valid and c.expired and c.refresh_token:
        c.refresh(Request()); open(tok, 'w').write(c.to_json())
    return build('docs', 'v1', credentials=c)

def set_pageless(docs, doc_id):
    docs.documents().batchUpdate(documentId=doc_id, body={'requests': [{
        'updateDocumentStyle': {
            'documentStyle': {'documentFormat': {'documentMode': 'PAGELESS'}},
            'fields': 'documentFormat.documentMode'}}]}).execute()

def widen_and_lint(docs, doc_id, total, legacy_weights=False, cap=CAP, min_col_pt=MIN_COL_PT):
    doc = docs.documents().get(documentId=doc_id).execute()
    reqs, bad = [], []

    def widths_for(t, ncols):
        if legacy_weights:
            w = WEIGHTS.get(ncols, [1.0 / ncols] * ncols)
            return [total * x for x in w]
        demands = col_demands(t, cap)
        return solve_widths(demands, total, min_col_pt, max_col_pt(ncols, total))

    def walk(elems):
        for el in elems:
            p = el.get('paragraph')
            if p:
                txt = ''.join(r.get('textRun', {}).get('content', '') for r in p.get('elements', []))
                if ' -- ' in txt or '--' in txt or '->' in txt:
                    bad.append(txt.strip()[:80])
            t = el.get('table')
            if t:
                start = el['startIndex']; ncols = t['columns']
                widths = widths_for(t, ncols)
                for ci in range(ncols):
                    reqs.append({'updateTableColumnProperties': {
                        'tableStartLocation': {'index': start}, 'columnIndices': [ci],
                        'tableColumnProperties': {'widthType': 'FIXED_WIDTH',
                                                  'width': {'magnitude': widths[ci], 'unit': 'PT'}},
                        'fields': 'widthType,width'}})
                for row in t['tableRows']:
                    for cell in row['tableCells']:
                        walk(cell['content'])
    walk(doc['body']['content'])
    if reqs:
        docs.documents().batchUpdate(documentId=doc_id, body={'requests': reqs}).execute()
    return len(reqs), bad

def auto_format(doc_id, account='work', total=700.0, label=''):
    """Run the pass from inside a writer, right after a doc is created or converted.

    Every caller here has just made a doc the owner is about to share, so the widths
    matter and the caller must not die when the pass cannot run. Auth failures,
    a Sheet instead of a Doc, a missing token: all are printed and swallowed.
    The dash lint reports as a warning, because refusing to return the doc id
    would leave the writer looking like it failed after it already wrote.

    Returns (columns_widened, lint_hits) or (0, []) when the pass could not run.
    """
    tag = f'[format_pass{(" " + label) if label else ""}]'
    if account not in ACCOUNTS:
        account = 'work'
    try:
        docs = svc(account)
        set_pageless(docs, doc_id)
        n, bad = widen_and_lint(docs, doc_id, total)
        print(f'{tag} pageless, widened {n} columns')
        if bad:
            print(f'{tag} [LINT] {len(bad)} paragraph(s) still contain "--" or "->": rephrase the source and re-convert')
            for b in bad[:5]:
                print(f'{tag}   - {b}')
        return n, bad
    except Exception as e:
        print(f'{tag} skipped ({type(e).__name__}: {e}). Run it by hand: '
              f'python3 .agent/skills/gdocs-create/format_pass.py {doc_id} --account {account}')
        return 0, []

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('ids', nargs='+')
    ap.add_argument('--account', default='work', choices=list(ACCOUNTS))
    ap.add_argument('--total-width', type=float, default=700.0)
    ap.add_argument('--keep-pages', action='store_true', help='do not switch to pageless')
    ap.add_argument('--legacy-weights', action='store_true',
                     help='use the old column-count-only width profiles instead of measuring content')
    ap.add_argument('--cap', type=float, default=CAP, help='char-length wrap-dampening cap per cell')
    ap.add_argument('--min-col-pt', type=float, default=MIN_COL_PT, help='minimum column width in PT')
    a = ap.parse_args()
    docs = svc(a.account)
    fail = False
    for did in a.ids:
        if not a.keep_pages:
            set_pageless(docs, did)
        n, bad = widen_and_lint(docs, did, a.total_width, a.legacy_weights, a.cap, a.min_col_pt)
        print(f'{did}: pageless={not a.keep_pages}, widened {n} columns')
        if bad:
            fail = True
            print(f'  [LINT FAIL] {len(bad)} paragraph(s) still contain "--" or "->". Rephrase the source and re-convert:')
            for b in bad[:10]:
                print(f'    - {b}')
    sys.exit(1 if fail else 0)

if __name__ == '__main__':
    main()
