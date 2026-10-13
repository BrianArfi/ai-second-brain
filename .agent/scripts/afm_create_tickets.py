#!/usr/bin/env python3
"""Create AFM Jira tickets for backlog rows that have none, from one-pagers.

Input: one-pager JSON files (one object per backlog id) with the fields
id, jira_summary, user_story, why, scope_in, out_of_scope,
acceptance_criteria, dependencies, open_questions, prd_section,
confidence, gaps. They are drafted from each epic's PRD; see
_temp/afm_onepagers/out_*.json for the 6 Oct 2026 batch.

Epic rows (AFM-n) become Epics; leaf rows (AFM-n.m) become Stories under
their epic's ticket, whether that epic ticket already exists or is created
in the same run. The description ends with "Backlog id: <id>", which is how
afm_sheet_sync.py maps tickets back to sheet rows, so run that afterwards to
put the new keys on the sheet.

  python3 .agent/scripts/afm_create_tickets.py --onepagers _temp/afm_onepagers
      dry run: writes the review file, creates nothing
  python3 .agent/scripts/afm_create_tickets.py --onepagers _temp/afm_onepagers --approved [--only AFM-11.9 ...]
"""
import sys, os, re, json, glob, argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import afm_sheet_sync as base
import afm_linked_sync as L
from jira_api import call

REVIEW = os.path.join(base.BASE, 'Clients', 'Work', 'Example Program', 'migration_backlog',
                      'AFM_New_Ticket_OnePagers_2026-10-06.md')

def link_dep(d, ids_to_key):
    d = d.strip()
    m = re.match(r'(AFM-\d+(?:\.\d+)?)', d)
    if m and m.group(1) in ids_to_key:
        k = ids_to_key[m.group(1)]
        return [L.t(f'{m.group(1)} (Jira {k})', href=f'{base.BROWSE}{k}'), L.t(d[len(m.group(1)):])]
    m = L.REF.search(d)
    if m:
        k = m.group(0)
        return [L.t(d[:m.start()]), L.t(k, href=f"{L.SITES[k.split('-')[0]]}/browse/{k}"), L.t(d[m.end():])]
    return [L.t(d)]

def build(op, row, H, ids_to_key, live):
    g = lambda c: (str(row[H[c]]).strip() if c in H and len(row) > H[c] else '')
    bid = op['id']
    c = [L.heading('Summary'), L.para(L.t(op['jira_summary'], bold=True))]
    if op.get('user_story'):
        c.append(L.para(L.t(op['user_story'])))
    if op.get('why'):
        c += [L.heading('Why the migration needs it'), L.para(L.t(op['why']))]
    if op.get('scope_in'):
        c += [L.heading('In scope'), L.bullets([L.t(s) for s in op['scope_in']])]
    if op.get('out_of_scope'):
        c += [L.heading('Out of scope'), L.bullets([L.t(s) for s in op['out_of_scope']])]
    if op.get('acceptance_criteria'):
        c += [L.heading('Acceptance criteria'), L.bullets([L.t(s) for s in op['acceptance_criteria']])]
    if op.get('dependencies'):
        c += [L.heading('Dependencies'), L.bullets([link_dep(d, ids_to_key) for d in op['dependencies']])]
    if g('Engineering Notes/Questions') or g('Product response'):
        c.append(L.heading('Engineering questions and product answers'))
        if g('Engineering Notes/Questions'):
            c += [L.para(L.t('Engineering: ', bold=True))] + L.text_paras(g('Engineering Notes/Questions'))
        if g('Product response'):
            c += [L.para(L.t('Product: ', bold=True))] + L.text_paras(g('Product response'))
    if op.get('open_questions'):
        c += [L.heading('Open questions'), L.bullets([L.t(s) for s in op['open_questions']])]
    if op.get('gaps'):
        c += [L.heading('Before this can be estimated'), L.para(L.t(op['gaps']))]
    facts = [[L.t('Sheet title: ', bold=True), L.t(g('Title'))]]
    parent = g('Parent')
    if parent and parent in ids_to_key:
        facts.append([L.t('Epic: ', bold=True), L.t(f'{parent} (Jira {ids_to_key[parent]})', href=f'{base.BROWSE}{ids_to_key[parent]}')])
    for label, col in [('System', 'System'), ('Priority', 'Priority'), ('Wave', 'Wave'),
                       ('Migration critical', 'Migration critical'), ('Usage basis', 'Usage basis'),
                       ('Estimate owner', 'Estimate owner'), ('Assignee (sheet)', 'Assignee'),
                       ('Estimate', 'Estimate'), ('Due date', 'Due date')]:
        if g(col):
            facts.append([L.t(f'{label}: ', bold=True), L.t(g(col))])
    facts.append([L.t('Draft confidence: ', bold=True), L.t(f"{op.get('confidence', '?')} (how fully the PRD states this requirement)")])
    c += [L.heading('Context'), L.bullets(facts)]
    refs = L.row_refs(row, H)
    if refs:
        c += [L.heading('Related tickets'), L.bullets([[L.t(k, href=f"{L.SITES[k.split('-')[0]]}/browse/{k}"),
              L.t((' legacy Example Program' if k.split('-')[0] in L.LEGACY else '')
                  + (f' ({live[k][0]}): {live[k][1]}' if k in live else ''))] for k in refs])]
    src = []
    p = L.prd_for(bid)
    if p:
        src.append([L.t('PRD: ', bold=True), L.t(p[0], href=p[1]),
                    L.t(f", section {op['prd_section']}" if op.get('prd_section') else '')])
    if g('Source'):
        src.append([L.t('Origin: ', bold=True), L.t(g('Source'))])
    src.append([L.t('Backlog row: ', bold=True), L.t(f'{bid} in the Migration Backlog sheet', href=L.SHEET_URL)])
    c += [L.heading('Sources'), L.bullets(src), L.para(L.t(f'Backlog id: {bid}'))]
    return {'type': 'doc', 'version': 1, 'content': c}

def review_md(items):
    out = ['# AFM new tickets: one-pager per backlog row, 6 Oct 2026', '',
           f'Draft content for {len(items)} Jira tickets in the AFM project, one per backlog row with no ticket. '
           'Nothing is created until approved. Each one-pager is drafted from the epic PRD; '
           '"confidence" says how fully the PRD states the requirement.', '',
           f'Sheet: [Example Program Migration Backlog]({L.SHEET_URL})', '']
    by = {}
    for op, row, H in items:
        by.setdefault(L.prd_for(op['id'])[0] if L.prd_for(op['id']) else 'No PRD', []).append((op, row, H))
    for prd, rows in by.items():
        out += [f'## {prd} ({len(rows)})', '']
        for op, row, H in rows:
            title = row[H['Title']]
            out += [f"### {op['id']}: {title}", '',
                    f"**{op['jira_summary']}** · priority {row[H['Priority']]} · confidence {op.get('confidence')}", '']
            if op.get('user_story'):
                out += [op['user_story'], '']
            if op.get('why'):
                out += [f"**Why:** {op['why']}", '']
            for label, key in [('In scope', 'scope_in'), ('Out of scope', 'out_of_scope'),
                               ('Acceptance criteria', 'acceptance_criteria'), ('Dependencies', 'dependencies'),
                               ('Open questions', 'open_questions')]:
                if op.get(key):
                    out += [f'**{label}:**', ''] + [f'- {x}' for x in op[key]] + ['']
            if op.get('gaps'):
                out += [f"**Before estimation:** {op['gaps']}", '']
    return '\n'.join(out)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--onepagers', required=True)
    ap.add_argument('--approved', action='store_true')
    ap.add_argument('--only', nargs='*')
    args = ap.parse_args()

    ops = {}
    src = os.path.join(base.BASE, args.onepagers)
    for f in sorted(glob.glob(os.path.join(src, 'out_*.json')) if os.path.isdir(src) else glob.glob(src)):
        for o in json.load(open(f, encoding='utf-8')):
            ops[o['id']] = o
    _, grid = L.load_grid()
    H = {h: i for i, h in enumerate(grid[0])}
    rows = {r[0].strip(): r for r in grid[1:] if r and r[0]}
    jira = base.fetch_jira()
    ids_to_key = {b: v['key'] for b, v in jira.items()}

    todo = [b for b in ops if b in rows and b not in ids_to_key and (not args.only or b in args.only)]
    missing = [b for b in rows if b not in ids_to_key and b not in ops]
    todo.sort(key=lambda b: ('.' in b, [int(x) for x in b[4:].split('.')]))   # epics first
    live = L.ticket_status(sorted({k for b in todo for k in L.row_refs(rows[b], H)}))
    print(f'one-pagers loaded: {len(ops)}; to create: {len(todo)} '
          f'({sum(1 for b in todo if "." not in b)} epics); rows still without a one-pager: {len(missing)}')
    if missing:
        print('  no one-pager yet:', ', '.join(missing[:20]), '...' if len(missing) > 20 else '')

    items = [(ops[b], rows[b], H) for b in todo]
    if not args.only:
        open(REVIEW, 'w', encoding='utf-8').write(review_md(items))
        print(f'review file: {REVIEW}')
    if not args.approved:
        print('DRY RUN. Re-run with --approved to create.')
        return

    created = []
    for b in todo:
        row = rows[b]
        is_epic = '.' not in b
        fields = {
            'project': {'key': 'AFM'},
            # The one-pager summary, not the sheet title: some sheet titles
            # predate a PRD revision (AFM-1.7 still names the withdrawn
            # three-state model). The sheet title stays in Context.
            'summary': ops[b]['jira_summary'][:250],
            'issuetype': {'name': 'Epic' if is_epic else 'Story'},
            'description': build(ops[b], row, H, ids_to_key, live),
            'labels': ['afm-onepager', f"confidence-{ops[b].get('confidence', 'na')}"],
        }
        parent = row[H['Parent']].strip() if len(row) > H['Parent'] else ''
        if not is_epic and parent in ids_to_key:
            fields['parent'] = {'key': ids_to_key[parent]}
        r = call('POST', '/rest/api/3/issue', {'fields': fields})
        ids_to_key[b] = r['key']
        created.append((b, r['key']))
        print(f'  {b} -> {r["key"]}')
    print(f'created {len(created)} tickets. Next: python3 .agent/scripts/afm_sheet_sync.py --approved')

if __name__ == '__main__':
    main()
