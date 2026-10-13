#!/usr/bin/env python3
"""Second half of the AFM sheet sync: linked delivery tickets and Jira detail.

afm_sheet_sync.py mirrors the AFM tracking tickets. Those stay at "To Do":
engineering works in the MSP / STOR / MBA (ExampleVendor) and MP / MPS (Work)
tickets that each backlog row cites in Source, Description or Product
response. This script reads those tickets and does three things.

  sheet   Writes two owned columns on the Migration Backlog tab:
            Linked Tickets  - every cited ticket, each one a clickable link
            Linked Status   - "KEY: status" per ticket, live from Jira
          plus the PRD column (one PRD per epic), and proposes a new value for the human Status column where the
          linked tickets show the row has moved (all Done -> Done, any in
          QA -> In QA, any in progress -> In Progress). The Status write is
          a separate flag, --apply-status, because that column is human.

  jira    Rewrites each AFM ticket description from its sheet row: what,
          why it matters for the migration, engineering Q&A, dependencies,
          linked delivery tickets, PRD, and the backlog row. The
          "Backlog id:" line stays, afm_sheet_sync.py matches on it.

  python3 .agent/scripts/afm_linked_sync.py sheet            # dry run
  python3 .agent/scripts/afm_linked_sync.py sheet --approved [--apply-status [AFM-9.1 ...]]
  python3 .agent/scripts/afm_linked_sync.py jira  [--only AFM-33] [--approved]
"""
import sys, os, re, json, argparse, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import afm_sheet_sync as base
from jira_api import call

SITES = {
    'MSP': 'https://examplevendor.atlassian.net', 'MBA': 'https://examplevendor.atlassian.net',
    'STOR': 'https://examplevendor.atlassian.net', 'AFMJ': 'https://examplevendor.atlassian.net',
    'MP': 'https://yourcompany.atlassian.net', 'MPS': 'https://yourcompany.atlassian.net',
}
REF = re.compile(r'\b(MSP|MBA|STOR|MPS|MP)-(\d+)\b')
SCAN_COLS = ['Source', 'Description', 'Product response', 'Title', 'Engineering Notes/Questions']
OWNED = ['Linked Tickets', 'Linked Status', 'PRD']
SHEET_URL = f'https://docs.google.com/spreadsheets/d/{base.SHEET_ID}/edit?gid=160358679#gid=160358679'
# One PRD per AFM epic (29 Sep ExampleProgram Resources decision; mapping in
# Clients/Work/Example Program/AFM_PRD_Ticket_Sheet_Mapping_2026-10-01.md).
_PRDS = {
    'Consumer Storefront': ('<YOUR_DRIVE_ID>', [27, 25, 20, 21, 29]),
    'Seller Portal': ('<YOUR_DRIVE_ID>', [2, 18, 26, 12, 15]),
    'Storefront Admin Portal': ('<YOUR_DRIVE_ID>', [1, 4]),
    'E-Commerce Core, PIM, LiveOps': ('<YOUR_DRIVE_ID>', [16, 17, 19, 11, 13, 14]),
    'OMS and Fulfillment': ('<YOUR_DRIVE_ID>', [3, 9, 10, 23]),
    'Cutover, Data Migration and Reporting': ('<YOUR_DRIVE_ID>', [5, 7, 8, 28, 22, 24]),
    'Identity, SSO and Members': ('<YOUR_DRIVE_ID>', [6]),
}
EPIC_PRD = {e: (name, f'https://docs.google.com/document/d/{doc}/edit')
            for name, (doc, epics) in _PRDS.items() for e in epics}

def prd_for(bid):
    m = re.match(r'AFM-(\d+)', bid or '')
    return EPIC_PRD.get(int(m.group(1))) if m else None

PRD_RE = re.compile(r'HYPERLINK\("([^"]+)","([^"]+)"\)')

def ticket_status(keys):
    """key -> (status, summary, category) for every key; missing ones are skipped."""
    out = {}
    by_site = {}
    for k in keys:
        by_site.setdefault(SITES[k.split('-')[0]], []).append(k)
    for site, ks in by_site.items():
        for i in range(0, len(ks), 50):
            chunk = ks[i:i + 50]
            jql = 'key in (' + ','.join(chunk) + ')'
            import urllib.parse
            try:
                d = call('GET', f'{site}/rest/api/3/search/jql?jql={urllib.parse.quote(jql)}'
                         '&maxResults=100&fields=summary,status')
            except RuntimeError:
                # One bad key fails the whole JQL; fall back to one by one.
                d = {'issues': []}
                for k in chunk:
                    try:
                        d['issues'].append(call('GET', f'{site}/rest/api/3/issue/{k}?fields=summary,status'))
                    except RuntimeError:
                        pass
            for iss in d.get('issues', []):
                st = iss['fields']['status']
                out[iss['key']] = (st['name'], iss['fields']['summary'],
                                   st.get('statusCategory', {}).get('key', ''))
    return out

LEGACY = ('MP', 'MPS')

def derive_status(refs, live):
    """Sheet Status implied by the row's own delivery tickets, or None.

    Only ExampleVendor keys cited in the Source column count. MP / MPS are legacy
    Example Program tickets (old-world build): Done there means built in the old
    stack, not migrated. Keys in Description or Product response are usually
    dependencies or context, so they never move the row either.
    """
    found = [live[k] for k in refs if k in live and k.split('-')[0] not in LEGACY]
    if not found:
        return None
    names = [f[0].lower() for f in found]
    cats = [f[2] for f in found]
    if any('fail' in n or 'defer' in n or 'cancel' in n or "won't" in n for n in names):
        return None
    if all(c == 'done' for c in cats):
        return 'Done'
    if any('qa' in n or 'test' in n or 'review' in n for n in names):
        return 'In QA'
    if any(c == 'indeterminate' for c in cats):
        return 'In Progress'
    return None

RANK = {'Backlog': 0, 'Todo': 1, 'Blocked': 1, 'In Progress': 2, 'In QA': 3, 'Done': 4}

def load_grid():
    svc = base.sheets()
    grid = svc.values().get(spreadsheetId=base.SHEET_ID, range=f'{base.TAB}!A1:AZ2000',
                            valueRenderOption='FORMULA').execute().get('values', [])
    return svc, grid

def row_refs(row, H):
    seen = []
    for c in SCAN_COLS:
        if c in H and len(row) > H[c]:
            for m in REF.finditer(str(row[H[c]])):
                k = f'{m.group(1)}-{m.group(2)}'
                if k not in seen:
                    seen.append(k)
    return seen

def cmd_sheet(args):
    svc, grid = load_grid()
    header = grid[0]
    new_header = list(header) + [c for c in OWNED if c not in header]
    H = {h: i for i, h in enumerate(new_header)}
    rows = grid[1:]
    refs = {r[0]: row_refs(r, H) for r in rows if r and r[0]}
    live = ticket_status(sorted({k for v in refs.values() for k in v}))
    print(f'rows citing a delivery ticket: {sum(1 for v in refs.values() if v)}')
    print(f'distinct tickets cited: {len({k for v in refs.values() for k in v})}, resolved live: {len(live)}')

    sheet_meta = svc.get(spreadsheetId=base.SHEET_ID, fields='sheets(properties(sheetId,title))').execute()
    gid = next(s['properties']['sheetId'] for s in sheet_meta['sheets'] if s['properties']['title'] == base.TAB)

    cell_reqs, status_changes, value_updates, prd_updates = [], [], [], []
    for rownum, row in enumerate(rows, start=2):
        if not row or not row[0]:
            continue
        ks = refs[row[0]]
        # Linked Tickets: rich text, one link run per key.
        text, runs, pos = '', [], 0
        for k in ks:
            if text:
                text += ', '
            start = len(text)
            text += k
            runs.append({'startIndex': start, 'format': {'link': {'uri': f"{SITES[k.split('-')[0]]}/browse/{k}"}}})
            runs.append({'startIndex': len(text), 'format': {}})
        if runs and runs[-1]['startIndex'] >= len(text):
            runs.pop()
        status_txt = '; '.join(
            (f'{k}: {live[k][0]}' if k in live else f'{k}: not found')
            + (' (legacy)' if k.split('-')[0] in LEGACY else '') for k in ks)
        cur_links = row[H['Linked Tickets']] if 'Linked Tickets' in header and len(row) > H['Linked Tickets'] else ''
        cur_stat = row[H['Linked Status']] if 'Linked Status' in header and len(row) > H['Linked Status'] else ''
        if cur_links != text:
            cell = {'userEnteredValue': {'stringValue': text}}
            if runs:
                cell['textFormatRuns'] = runs
            cell_reqs.append({'updateCells': {
                'range': {'sheetId': gid, 'startRowIndex': rownum - 1, 'endRowIndex': rownum,
                          'startColumnIndex': H['Linked Tickets'], 'endColumnIndex': H['Linked Tickets'] + 1},
                'rows': [{'values': [cell]}],
                'fields': 'userEnteredValue,textFormatRuns'}})
        if cur_stat != status_txt:
            value_updates.append({'range': f"{base.TAB}!{base.col_letter(H['Linked Status'])}{rownum}",
                                  'values': [[status_txt]]})
        src_refs = [m.group(0) for m in REF.finditer(str(row[H['Source']]) if len(row) > H['Source'] else '')]
        p = prd_for(row[0])
        if p:
            f = f'=HYPERLINK("{p[1]}","PRD: {p[0]}")'
            cur_prd = row[H['PRD']] if len(row) > H['PRD'] else ''
            if cur_prd != f:
                prd_updates.append({'range': f"{base.TAB}!{base.col_letter(H['PRD'])}{rownum}",
                                    'values': [[f]]})
        want = derive_status(src_refs, live)
        cur = row[H['Status']] if len(row) > H['Status'] else ''
        if want and RANK.get(want, 0) > RANK.get(cur, 0):
            status_changes.append((row[0], cur, want, status_txt, rownum))

    print(f'Linked Tickets cells to write: {len(cell_reqs)}; Linked Status cells: {len(value_updates)}')
    print(f'PRD cells to write: {len(prd_updates)}')
    print(f'Status column changes proposed: {len(status_changes)}')
    for bid, cur, want, st, _ in status_changes:
        print(f'  {bid}: {cur} -> {want}   [{st}]')
    json.dump([s[:4] for s in status_changes], open(os.path.join(base.BASE, '_temp', 'afm_status_changes.json'), 'w'), indent=1)

    if not args.approved:
        print('\nDRY RUN. Re-run with --approved (and --apply-status for the Status column).')
        return
    if new_header != header:
        svc.values().update(spreadsheetId=base.SHEET_ID, range=f'{base.TAB}!A1',
                            valueInputOption='RAW', body={'values': [new_header]}).execute()
    for i in range(0, len(cell_reqs), 200):
        svc.batchUpdate(spreadsheetId=base.SHEET_ID, body={'requests': cell_reqs[i:i + 200]}).execute()
    data = list(value_updates)
    if args.apply_status is not None:
        pick = set(args.apply_status)
        data += [{'range': f"{base.TAB}!{base.col_letter(H['Status'])}{rn}", 'values': [[w]]}
                 for b, _, w, _, rn in status_changes if not pick or b in pick]
    if data:
        svc.values().batchUpdate(spreadsheetId=base.SHEET_ID, body={
            'valueInputOption': 'RAW', 'data': data}).execute()
    if prd_updates:
        svc.values().batchUpdate(spreadsheetId=base.SHEET_ID, body={
            'valueInputOption': 'USER_ENTERED', 'data': prd_updates}).execute()
    data += prd_updates
    print(f'wrote {len(cell_reqs)} link cells, {len(data)} value cells; {SHEET_URL}')

# ---------- Jira description builder ----------

def t(s, **mark):
    node = {'type': 'text', 'text': s}
    if mark.get('href'):
        node['marks'] = [{'type': 'link', 'attrs': {'href': mark['href']}}]
    elif mark.get('bold'):
        node['marks'] = [{'type': 'strong'}]
    return node

def para(*nodes):
    return {'type': 'paragraph', 'content': [n for n in nodes if n]}

def heading(s):
    return {'type': 'heading', 'attrs': {'level': 3}, 'content': [t(s)]}

def bullets(items):
    return {'type': 'bulletList', 'content': [
        {'type': 'listItem', 'content': [para(*i) if isinstance(i, (list, tuple)) else para(i)]} for i in items]}

def text_paras(s):
    return [para(t(p.strip())) for p in re.split(r'\n\s*\n', s) if p.strip()]

def build_description(row, H, ids_to_key, live):
    g = lambda c: (str(row[H[c]]).strip() if c in H and len(row) > H[c] else '')
    bid = g('ID')
    content = []
    content.append(heading('What'))
    content.append(para(t(g('Title'), bold=True)))
    if g('Description'):
        content += text_paras(g('Description'))

    facts = []
    for label, col in [('System', 'System'), ('Priority', 'Priority'), ('Wave', 'Wave'),
                       ('Migration critical', 'Migration critical'), ('Usage basis', 'Usage basis'),
                       ('Estimate owner', 'Estimate owner'), ('Assignee (sheet)', 'Assignee'),
                       ('Estimate', 'Estimate'), ('Due date', 'Due date')]:
        if g(col):
            facts.append([t(f'{label}: ', bold=True), t(g(col))])
    content.append(heading('Context'))
    parent = g('Parent')
    if parent:
        pk = ids_to_key.get(parent)
        facts.insert(0, [t('Epic: ', bold=True),
                         t(f'{parent} (Jira {pk})', href=f'{base.BROWSE}{pk}') if pk else t(parent)])
    content.append(bullets(facts) if facts else para(t('No sheet metadata yet.')))

    if g('Engineering Notes/Questions') or g('Product response'):
        content.append(heading('Engineering questions and product answers'))
        if g('Engineering Notes/Questions'):
            content.append(para(t('Engineering: ', bold=True)))
            content += text_paras(g('Engineering Notes/Questions'))
        if g('Product response'):
            content.append(para(t('Product: ', bold=True)))
            content += text_paras(g('Product response'))

    deps = [d.strip() for d in re.split(r'[,;\s]+', g('Blocked by')) if d.strip()]
    if deps:
        items = []
        for d in deps:
            if d in ids_to_key:
                items.append([t(f'{d} (Jira {ids_to_key[d]})', href=f'{base.BROWSE}{ids_to_key[d]}')])
            elif REF.fullmatch(d):
                items.append([t(d, href=f"{SITES[d.split('-')[0]]}/browse/{d}")])
            else:
                items.append([t(d)])
        content.append(heading('Blocked by'))
        content.append(bullets(items))

    refs = row_refs(row, H)
    if refs:
        content.append(heading('Related tickets'))
        content.append(bullets([[t(k, href=f"{SITES[k.split('-')[0]]}/browse/{k}"),
                                 t((' legacy Example Program' if k.split('-')[0] in LEGACY else '')
                                   + (f' ({live[k][0]}): {live[k][1]}' if k in live else ' (not found)'))]
                                for k in refs]))

    content.append(heading('Sources'))
    src = []
    m = PRD_RE.search(g('PRD'))
    p = (m.group(2), m.group(1)) if m else (('PRD: ' + prd_for(bid)[0], prd_for(bid)[1]) if prd_for(bid) else None)
    if p:
        src.append([t('PRD: ', bold=True), t(p[0].replace('PRD: ', ''), href=p[1])])
    if g('Source'):
        src.append([t('Origin: ', bold=True), t(g('Source'))])
    src.append([t('Backlog row: ', bold=True), t(f'{bid} in the Migration Backlog sheet', href=SHEET_URL)])
    content.append(bullets(src))
    if g('Labels'):
        content.append(para(t(f"Labels (sheet): {g('Labels')}")))
    # afm_sheet_sync.py matches on this exact line. Keep it last and plain.
    content.append(para(t(f'Backlog id: {bid}')))
    return {'type': 'doc', 'version': 1, 'content': content}

def cmd_jira(args):
    _, grid = load_grid()
    H = {h: i for i, h in enumerate(grid[0])}
    jira = base.fetch_jira()               # backlog id -> {key, status, updated}
    ids_to_key = {b: v['key'] for b, v in jira.items()}
    rows = {r[0]: r for r in grid[1:] if r and r[0]}
    targets = [(b, v['key']) for b, v in sorted(jira.items(), key=lambda x: int(x[1]['key'].split('-')[1]))
               if b in rows and (not args.only or v['key'] in args.only or b in args.only)]
    live = ticket_status(sorted({k for b, _ in targets for k in row_refs(rows[b], H)}))
    print(f'AFM tickets to rewrite: {len(targets)}')
    out = {}
    for b, key in targets:
        doc = build_description(rows[b], H, ids_to_key, live)
        if args.approved:
            # Keep whatever the ticket says today under its own heading, so a
            # human edit made in Jira is never lost. Skip when the current text
            # is already one of ours (re-run), or it would nest every time.
            cur = call('GET', f'/rest/api/3/issue/{key}?fields=description,labels')['fields']
            old = cur.get('description') or {}
            ours = any(n.get('type') == 'heading' for n in old.get('content', []))
            if 'afm-onepager' in (cur.get('labels') or []):
                continue                     # created from a one-pager; never overwrite
            if old.get('content') and not ours:
                doc['content'] += [heading('Previous description')] + old['content']
        out[key] = doc
        if args.approved:
            call('PUT', f'/rest/api/3/issue/{key}', {'fields': {'description': doc}})
    json.dump(out, open(os.path.join(base.BASE, '_temp', 'afm_jira_desc_preview.json'), 'w'), indent=1)
    if args.approved:
        print(f'updated {len(out)} descriptions at '
              f"{datetime.datetime.now(datetime.timezone.utc):%Y-%m-%d %H:%M UTC}")
    else:
        print('DRY RUN. Preview in _temp/afm_jira_desc_preview.json. Re-run with --approved to write.')

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('mode', choices=['sheet', 'jira'])
    ap.add_argument('--approved', action='store_true')
    ap.add_argument('--apply-status', nargs='*', default=None,
                    help='write proposed Status values; optionally only these backlog ids')
    ap.add_argument('--only', nargs='*')
    args = ap.parse_args()
    cmd_sheet(args) if args.mode == 'sheet' else cmd_jira(args)

if __name__ == '__main__':
    main()
