#!/usr/bin/env python3
"""Build the unified Work Task Board (kanban, Linear-import shaped).

Sources
  1. Master Product List, tab "Master Product List & Breakdown (MECE)"  -> roadmap work
  2. Clients/Work/Example Program/migration_backlog/exampleprogram_migration_backlog_v3.csv -> migration work

Output
  Clients/Work/task_board_linear_import.csv     (the artifact Linear imports)
  optional: writes the same rows into a "Task Board" tab of the MPL spreadsheet (--push)

No sprints. Kanban: Status is the column, Priority + Rank is the order.
"""
import argparse
import csv
import os
import re
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MPL_SHEET_ID = '<YOUR_DRIVE_ID>'
MECE_TAB = 'Master Product List & Breakdown (MECE)'
BOARD_TAB = 'Task Board'
AF_CSV = os.path.join(BASE_DIR, 'Clients', 'Work', 'Example Program',
                      'migration_backlog', 'exampleprogram_migration_backlog_v3.csv')
OUT_CSV = os.path.join(BASE_DIR, 'Clients', 'Work', 'task_board_linear_import.csv')
TOKEN = os.path.join(BASE_DIR, '.agent', 'skills', 'work-drive-connector', 'token.json')

COLUMNS = [
    'ID', 'Title', 'Description', 'Team', 'Project', 'Component', 'Parent',
    'Status', 'Priority', 'Rank', 'Assignee', 'Owner basis', 'Estimate',
    'Labels', 'Due date', 'Blocked by', 'Pickup ready', 'Doc', 'Doc URL',
    'MPL Feature', 'Phase', 'Wave', 'Migration critical', 'node',
]
DOC_COL = COLUMNS.index('Doc')

# the owner's board only. Teammate's Marketplace and Teammate's Platform work is tracked
# by those teams, so their MPL products are dropped here rather than duplicated.
EXCLUDED_L0 = {'Shared Components', 'Partner Portal', 'Marketplace'}

# --- taxonomy -------------------------------------------------------------

# Linear team per L0 product (MPL) -- matches the owner's four delivery teams.
TEAM_BY_L0 = {
    'Ecom Solutions': 'Ecom Solutions',
    'B2C Superapp': 'B2C Super App',
}
# Example Program migration is the owner's regardless of which service it touches, so every
# system maps onto one of his two teams. Nothing here routes to Platform.
TEAM_BY_AF_SYSTEM = {
    'Seller Portal': 'Ecom Solutions',
    'E-Commerce Core': 'Ecom Solutions',
    'E-Commerce Solution': 'Ecom Solutions',
    'Storefront': 'Ecom Solutions',
    'Storefront Admin': 'Ecom Solutions',
    'OMS': 'Ecom Solutions',
    'Settlement': 'Ecom Solutions',
    'Fulfillment': 'Ecom Solutions',
    'PIM': 'Ecom Solutions',
    'LiveOps': 'Ecom Solutions',
    'Identity': 'Ecom Solutions',
    'Data': 'Ecom Solutions',
    'Product': 'Ecom Solutions',
    'Promotions': 'Ecom Solutions',
}

# work-tree node per MPL L1 component. Every record needs a node (CLAUDE.md hard rule).
NODE_BY_COMPONENT = {
    'Seller Portal': 'np-sp',
    'E-commerce Core': 'np-core',
    'PIM': 'np-core',
    'Search Experience': 'np-core',
    'Recommendation & Personalization': 'np-core',
    'OMS': 'np-oms',
    'Refund Flow': 'np-oms',
    'E-commerce Front-end Builder': 'np-sf',
    'Fulfillment Service': 'np-ffs',
    'Multi-Warehouse Fulfillment Routing': 'np-ffs',
    'Multi-Service Shipping Options per Offer': 'np-ffs',
    'Promo Engine': 'np-promo',
    'Promotion Engine': 'np-promo',
    'Mixed Payment': 'b2c-pay',
    'Mixed Payment Engine v.1': 'b2c-pay',
    'Mixed Payment v.2': 'b2c-pay',
    'Checkout v1 & Payment Gateway': 'b2c-pay',
    'B2C Wallet & Balance Hub': 'b2c-pay',
    'IAM (Identity Access Management)': 'b2c-identity',
    'Work Verification': 'b2c-identity',
    'Account Deletion Request': 'b2c-identity',
    'App Store Launch': 'b2c-rel',
    'Guest Browsing & Optional Login (Apple 5.1.1(v) Remediation)': 'b2c-rel',
    'Guest Browsing & Optional Login (Phase 1)': 'b2c-rel',
    'Guest Checkout (Phase 2)': 'b2c-rel',
    'Digital Goods Store': 'b2c-commerce',
    'Physical Goods Marketplace': 'b2c-commerce',
    'Order Management & Tracking': 'b2c-commerce',
    'Wishlist & Social': 'b2c-commerce',
    'Gifti Global Migration': 'b2c-commerce',
    'Auto-Connect Loyalty Journey (Identity-Based Auto-Detection)': 'b2c-loyalty',
    'Gamification': 'b2c-loyalty',
    'Gamification & mTrust': 'b2c-loyalty',
    'In-App Messaging': 'b2c-support',
    'P2C Staging Bug Report': 'b2c-qa',
    'Fraud Prevention Layer': 'b2c-fraud',
    'Blockchain': 'np-backlog',
    'TMS': 'np-backlog',
    'Monetization': 'np-backlog',
    'Documentation': 'np-backlog',
    '★ START HERE': 'np-backlog',
}
NODE_FALLBACK_BY_L0 = {
    'Ecom Solutions': 'np-backlog',
    'B2C Superapp': 'b2c-commerce',
    'Shared Components': 'np-backlog',
    'Partner Portal': 'np-backlog',
}

# --- resourcing -----------------------------------------------------------
# Suggested owner by area, taken from the ExampleVendor roster sheet
# "Team Roster - Ecommerce Solution & B2C SuperApp" (<YOUR_DRIVE_ID>).
# These are SUGGESTIONS. "Owner basis" says where each one came from.
OWNER_BY_COMPONENT = {
    'Seller Portal': 'Teammate Ahmad',
    'E-commerce Core': 'Teammate Tahir',
    'OMS': 'Teammate Tahir',
    'Refund Flow': 'Teammate Tahir',
    'Settlement': 'Teammate Tahir',
    'Identity': 'Teammate Tahir',
    'PIM': 'Teammate Abbas',
    'Data': 'Teammate Abbas',
    'E-commerce Front-end Builder': 'Ameen Darwan',
    'Storefront': 'Ameen Darwan',
    'Storefront Admin': 'Ameen Darwan',
    'LiveOps': 'Ameen Darwan',
    'Fulfillment Service': 'Teammate Tahir',
    'Search Experience': 'Teammate Tahir',
}
OWNER_BY_TEAM = {
    'B2C Super App': 'Teammate Raza',
}
# Front-end wording inside a Seller Portal item routes to the FE engineer instead.
FRONTEND_HINT = re.compile(
    r'\b(ui|ux|screen|page|frontend|front-end|layout|design|tab|banner|modal|button)\b', re.I)
QA_HINT = re.compile(r'\b(qa|test|regression|uat|bug)\b', re.I)
SPEC_HINT = re.compile(r'\b(prd|spec|decision|sign-?off|policy|define|confirm|approval)\b', re.I)

# ExampleVendor exits. Anything suggested to a leaver carries a handover-risk label.
LEAVERS = {
    'Teammate Abbas': 'exits mid-Oct 2026',
    'Teammate Tahir': 'exits end-Nov 2026',
    'Teammate Ahmad': 'exits end-Oct 2026',
    'Ameen Darwan': 'exits end-Oct 2026',
    'Teammate Raza': 'exits end-Oct 2026',
    'Teammate Rasheed': 'exits end-Oct 2026',
    'Kinza Hanif': 'exits end-Oct 2026',
    'Jawad Shuja': 'exits end-Oct 2026',
}

PRIORITY_ORDER = {'Urgent': 0, 'High': 1, 'Medium': 2, 'Low': 3, '': 4}
STATUS_ORDER = {'In Progress': 0, 'In QA': 1, 'Blocked': 2, 'Todo': 3,
                'Backlog': 4, 'Done': 9}
MPL_STATUS = {'To Do': 'Todo', 'In Progress': 'In Progress', 'Released': 'Done'}
MPL_PRIORITY = {'P0': 'Urgent', 'P1': 'High', 'P2': 'Medium'}
ACTIVE_PHASES = {'Q3 2026', 'Q4 2026'}

def sheets_service():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials.from_authorized_user_file(TOKEN)
    return build('sheets', 'v4', credentials=creds).spreadsheets()

def read_mpl(svc):
    """Read the MECE tab WITH its hyperlinks.

    A plain values().get() returns only the display text, so every PRD link in
    the "Documents / Links" column is lost. The links are cell hyperlinks (and
    sometimes text-format runs), which only grid data exposes.
    """
    res = svc.get(
        spreadsheetId=MPL_SHEET_ID, ranges=["'%s'!A1:I2000" % MECE_TAB],
        includeGridData=True,
        fields=('sheets(data(rowData(values('
                'formattedValue,hyperlink,textFormatRuns(format/link/uri)))))')
    ).execute()
    row_data = res['sheets'][0]['data'][0].get('rowData', [])

    def cell(v):
        text = (v.get('formattedValue') or '').strip()
        url = v.get('hyperlink')
        if not url:
            for run in v.get('textFormatRuns') or []:
                uri = (run.get('format') or {}).get('link', {}).get('uri')
                if uri:
                    url = uri
                    break
        return text, url

    header = [cell(v)[0] for v in (row_data[0].get('values') or [])]
    out = []
    for r in row_data[1:]:
        cells = [cell(v) for v in (r.get('values') or [])]
        cells += [('', None)] * (len(header) - len(cells))
        if not any(t for t, _ in cells):
            continue
        row = {header[i]: cells[i][0] for i in range(len(header))}
        row['_doc_url'] = cells[header.index('Documents / Links')][1] or ''
        out.append(row)
    return out

MD_LINK = re.compile(r'\[([^\]]+)\]\((https?://[^)]+)\)')

def parse_md_link(text):
    """Example Program's Source column holds '[label](url) | [label](url)'."""
    m = MD_LINK.search(text or '')
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return (text or '').strip(), ''

def suggest_owner(component, team, title, description):
    """Return (assignee, basis). Never overwrites a real assignee upstream."""
    text = '%s %s' % (title, description)
    if SPEC_HINT.search(title):
        return 'Your Name', 'spec-or-decision'
    if QA_HINT.search(title):
        return 'Kinza Hanif', 'area-default'
    if component == 'Seller Portal' and FRONTEND_HINT.search(text):
        return 'Ameen Darwan', 'area-default'
    if component in OWNER_BY_COMPONENT:
        return OWNER_BY_COMPONENT[component], 'area-default'
    if team in OWNER_BY_TEAM:
        return OWNER_BY_TEAM[team], 'area-default'
    return '', 'unassigned'

def add_labels(existing, *extra):
    parts = [p.strip() for p in (existing or '').split(',') if p.strip()]
    for e in extra:
        if e and e not in parts:
            parts.append(e)
    return ','.join(parts)

def build_mpl_rows(mpl):
    """One epic per (L0, L1, L2) feature, one task per L3 detailed item."""
    epics = {}
    tasks = []
    epic_n = 0
    task_n = 0
    for r in mpl:
        l0 = r.get('L0: Product', '')
        l1 = r.get('L1: Component', '')
        l2 = r.get('L2: Feature', '')
        l3 = r.get('L3: Detailed Item', '')
        if not l2 or l0 in EXCLUDED_L0:
            continue
        team = TEAM_BY_L0[l0]
        node = NODE_BY_COMPONENT.get(l1) or NODE_FALLBACK_BY_L0.get(l0, 'np-backlog')
        phase = r.get('Phase', '')
        status = MPL_STATUS.get(r.get('Status', ''), 'Backlog')
        prio = MPL_PRIORITY.get(r.get('Priority', ''))
        if not prio:
            prio = 'Medium' if phase in ACTIVE_PHASES else 'Low'
        doc = r.get('Documents / Links', '')
        doc_url = r.get('_doc_url', '')

        key = (l0, l1, l2)
        if key not in epics:
            epic_n += 1
            epics[key] = {
                'ID': 'MPL-E%02d' % epic_n, 'Title': l2, 'Description': '',
                'Team': team, 'Project': l1, 'Component': l1, 'Parent': '',
                'Status': status, 'Priority': prio, 'Rank': '', 'Assignee': '',
                'Owner basis': 'epic', 'Estimate': '', 'Labels': 'epic,roadmap',
                'Due date': '', 'Blocked by': '', 'Pickup ready': 'No',
                'Doc': doc, 'Doc URL': doc_url,
                'MPL Feature': l2, 'Phase': phase, 'Wave': '',
                'Migration critical': '', 'node': node,
            }
        epic = epics[key]
        # the epic inherits the most urgent priority and the most advanced status of its children
        if PRIORITY_ORDER[prio] < PRIORITY_ORDER[epic['Priority']]:
            epic['Priority'] = prio
        if STATUS_ORDER[status] < STATUS_ORDER[epic['Status']]:
            epic['Status'] = status

        if doc_url and not epic['Doc URL']:
            epic['Doc'], epic['Doc URL'] = doc, doc_url
        if not l3:
            continue
        task_n += 1
        assignee, basis = suggest_owner(l1, team, l3, r.get('PRD Status', ''))
        labels = add_labels('roadmap', l1.lower().replace(' ', '-'))
        if assignee in LEAVERS and status != 'Done':
            labels = add_labels(labels, 'handover-risk')
        tasks.append({
            'ID': 'MPL-%03d' % task_n, 'Title': l3, 'Description': '',
            'Team': team, 'Project': l1, 'Component': l1, 'Parent': epic['ID'],
            'Status': status, 'Priority': prio, 'Rank': '',
            'Assignee': assignee, 'Owner basis': basis, 'Estimate': '',
            'Labels': labels, 'Due date': '', 'Blocked by': '',
            'Pickup ready': 'Yes' if status in ('Todo', 'Backlog') else 'No',
            'Doc': doc, 'Doc URL': doc_url,
            'MPL Feature': l2, 'Phase': phase, 'Wave': '',
            'Migration critical': '', 'node': node,
        })
    return list(epics.values()) + tasks

def build_af_rows():
    rows = []
    with open(AF_CSV, encoding='utf-8') as fh:
        raw = list(csv.DictReader(fh))
    by_id = {r['ID']: r for r in raw}
    for r in raw:
        system = (r.get('System') or '').strip()
        team = TEAM_BY_AF_SYSTEM.get(system, 'Ecom Solutions')
        status = (r.get('Status') or 'Backlog').strip()
        prio = (r.get('Priority') or 'Medium').strip()
        assignee = (r.get('Assignee') or '').strip()
        basis = 'existing' if assignee else ''
        if not assignee:
            assignee, basis = suggest_owner(system, team, r.get('Title', ''),
                                            r.get('Description', ''))
        # v3 has a column-shift bug: a few rows carry a person's name in Estimate.
        estimate = (r.get('Estimate') or '').strip()
        if estimate and not estimate.replace('.', '').isdigit():
            if not assignee:
                assignee, basis = estimate, 'existing'
            estimate = ''
        is_epic = '.' not in r['ID']
        labels = add_labels(r.get('Labels', ''), 'al-ExampleProgram-migration')
        if is_epic:
            labels = add_labels(labels, 'epic')
        if (r.get('Migration critical') or '') == 'Yes':
            labels = add_labels(labels, 'migration-critical')
        if assignee in LEAVERS and status != 'Done':
            labels = add_labels(labels, 'handover-risk')
        blocker = (r.get('Blocked by') or '').strip()
        blocked_open = bool(blocker) and by_id.get(blocker, {}).get('Status') != 'Done'
        # an item with no doc of its own inherits its epic's
        doc, doc_url = parse_md_link(r.get('Source', ''))
        if not doc_url and r.get('Parent'):
            doc, doc_url = parse_md_link(by_id.get(r['Parent'], {}).get('Source', ''))
        rows.append({
            'ID': r['ID'], 'Title': r.get('Title', ''),
            'Description': r.get('Description', ''),
            'Team': team, 'Project': 'Example Program legacy migration',
            'Component': system, 'Parent': r.get('Parent', ''),
            'Status': status, 'Priority': prio, 'Rank': '',
            'Assignee': assignee, 'Owner basis': basis or 'unassigned',
            'Estimate': estimate, 'Labels': labels,
            'Due date': r.get('Due date', ''), 'Blocked by': blocker,
            'Pickup ready': 'No' if (is_epic or blocked_open
                                     or status not in ('Todo', 'Backlog')) else 'Yes',
            'Doc': doc, 'Doc URL': doc_url, 'MPL Feature': '',
            'Phase': '', 'Wave': r.get('Wave', ''),
            'Migration critical': r.get('Migration critical', ''),
            'node': 'nw-afmig',
        })
    return rows

def rank(rows):
    """Kanban order: status first, then priority, then migration wave."""
    wave = {'October': 0, 'November': 1, '': 2}
    rows.sort(key=lambda r: (STATUS_ORDER.get(r['Status'], 5),
                             PRIORITY_ORDER.get(r['Priority'], 4),
                             wave.get(r['Wave'], 2),
                             r['Team'], r['Project'], r['ID']))
    for i, r in enumerate(rows, 1):
        r['Rank'] = i
    return rows

def col(name):
    return COLUMNS.index(name)

STATUS_ALL = ['In Progress', 'In QA', 'Blocked', 'Todo', 'Backlog', 'Done']
PRIORITY_ALL = ['Urgent', 'High', 'Medium', 'Low']

def one_of(*values):
    """Filter views reject ONE_OF_LIST, so keep-a-set is expressed as hide-the-rest."""
    domain = STATUS_ALL if values[0] in STATUS_ALL else PRIORITY_ALL
    return {'hiddenValues': [v for v in domain if v not in values]}

def text_eq(value):
    return {'condition': {'type': 'TEXT_EQ',
                          'values': [{'userEnteredValue': value}]}}

def is_blank():
    return {'condition': {'type': 'BLANK', 'values': []}}

# The saved views. This is how the board is meant to be read: pick a view,
# never scroll the raw 600 rows.
VIEWS = [
    ('1. Now - being worked on',
     {col('Status'): one_of('In Progress', 'In QA')}),
    ('2. Next - urgent and high, not started',
     {col('Status'): one_of('Todo', 'Backlog'),
      col('Priority'): one_of('Urgent', 'High')}),
    ('3. Up for grabs - nobody on it',
     {col('Pickup ready'): text_eq('Yes'), col('Assignee'): is_blank()}),
    ('4. Blocked',
     {col('Status'): text_eq('Blocked')}),
    ('5. Handover risk - owner leaving',
     {col('Labels'): {'condition': {'type': 'TEXT_CONTAINS',
                                    'values': [{'userEnteredValue': 'handover-risk'}]}},
      col('Status'): one_of('In Progress', 'In QA', 'Blocked', 'Todo', 'Backlog')}),
    ('6. Example Program migration - October wave',
     {col('Project'): text_eq('Example Program legacy migration'),
      col('Wave'): text_eq('October')}),
    ('7. Needs an estimate - urgent and high',
     {col('Priority'): one_of('Urgent', 'High'), col('Estimate'): is_blank()}),
    ('8. Missing a doc',
     {col('Doc URL'): is_blank(),
      col('Status'): one_of('In Progress', 'In QA', 'Blocked', 'Todo')}),
]

README_TAB = 'How to use the board'
README = [
    ['WORK TASK BOARD - how to read it'],
    [''],
    ['Kanban, not sprints. Nothing here has a sprint number and nothing rolls over.'],
    ['A task sits in a Status column until it moves. Priority plus Rank decides what comes next.'],
    [''],
    ['STEP 1 - NEVER SCROLL THE RAW SHEET'],
    ['Open the Task Board tab, then the menu Data, then Filter views, then pick one of the eight below.'],
    ['Toolbar shortcut: the icon immediately to the RIGHT of the blue funnel, between the funnel and the sigma.'],
    ['The blue funnel itself is the plain filter, not the saved views. That is the wrong icon.'],
    ['To leave a view, press the X at the top right of the dark bar that appears.'],
    ['The eight views answer the eight questions you actually ask.'],
    [''],
    ['View', 'What it answers'],
    ['1. Now', 'What is being worked on this minute.'],
    ['2. Next', 'What to pull the moment something frees up. Read top down, Rank is already sorted.'],
    ['3. Up for grabs', 'Unblocked work with nobody on it. This is the list you hand out.'],
    ['4. Blocked', 'What is stuck, and the Blocked by column says on what.'],
    ['5. Handover risk', 'Open work owned by someone leaving Work. Reassign from here.'],
    ['6. Example Program October wave', 'The migration cut that has to land first.'],
    ['7. Needs an estimate', 'Urgent and high work with no size yet. Fill this in grooming.'],
    ['8. Missing a doc', 'Active work with no PRD behind it. Each one is a spec gap.'],
    [''],
    ['STEP 2 - THE COLUMNS THAT MATTER'],
    ['Column', 'What it is for'],
    ['Status', 'The kanban column: Backlog, Todo, In Progress, In QA, Blocked, Done.'],
    ['Priority', 'Urgent, High, Medium, Low. Linear words, so the import maps them itself.'],
    ['Rank', 'Already-computed running order. Lower number means do it sooner.'],
    ['Pickup ready', 'Yes means unblocked and startable today.'],
    ['Owner basis', 'Where the Assignee came from: existing, area-default (a guess), spec-or-decision, unassigned.'],
    ['Doc', 'Clickable. The PRD or spec that describes the work.'],
    ['Parent', 'The epic ID this task sits under. Filter by it to see one feature end to end.'],
    ['node', 'Work-tree node, so this row joins the ledgers and the dashboard.'],
    [''],
    ['STEP 3 - WORKING IT, DAY TO DAY'],
    ['Moving a task: change Status. Nothing else. Do not cut and paste rows.'],
    ['Assigning: type the name into Assignee and set Owner basis to existing.'],
    ['New task: add a row at the bottom, give it an ID, Team, Project, Status, Priority and node. Rank recomputes on the next rebuild.'],
    [''],
    ['STEP 4 - REBUILDING'],
    ['python3 .agent/scripts/build_task_board.py --push'],
    ['WARNING: a rebuild replaces the whole tab from the MPL and the Example Program CSV.'],
    ['Edits made directly in this tab are lost. Change the source, or say so before a rebuild.'],
    [''],
    ['STEP 5 - WHEN LINEAR IS PAID'],
    ['File, Download, CSV on the Task Board tab, then import it in Linear.'],
    ['Linear maps Title, Description, Status, Priority, Assignee, Estimate, Labels, Due date and Project by itself.'],
    ['The other columns are scaffolding for the sheet phase and can be dropped at import.'],
]

def write_readme(svc, meta):
    existing = {s['properties']['title']: s['properties']['sheetId'] for s in meta['sheets']}
    if README_TAB not in existing:
        res = svc.batchUpdate(spreadsheetId=MPL_SHEET_ID, body={'requests': [
            {'addSheet': {'properties': {'title': README_TAB, 'index': 1,
                                         'gridProperties': {'rowCount': len(README) + 10,
                                                            'columnCount': 2}}}}]}).execute()
        sheet_id = res['replies'][0]['addSheet']['properties']['sheetId']
    else:
        sheet_id = existing[README_TAB]
        svc.values().clear(spreadsheetId=MPL_SHEET_ID,
                           range="'%s'" % README_TAB, body={}).execute()
    svc.values().update(spreadsheetId=MPL_SHEET_ID, range="'%s'!A1" % README_TAB,
                        valueInputOption='RAW', body={'values': README}).execute()
    svc.batchUpdate(spreadsheetId=MPL_SHEET_ID, body={'requests': [
        {'repeatCell': {
            'range': {'sheetId': sheet_id, 'startRowIndex': 0, 'endRowIndex': 1},
            'cell': {'userEnteredFormat': {'textFormat': {'bold': True, 'fontSize': 14}}},
            'fields': 'userEnteredFormat.textFormat'}},
        {'updateDimensionProperties': {
            'range': {'sheetId': sheet_id, 'dimension': 'COLUMNS',
                      'startIndex': 0, 'endIndex': 1},
            'properties': {'pixelSize': 430}, 'fields': 'pixelSize'}},
        {'updateDimensionProperties': {
            'range': {'sheetId': sheet_id, 'dimension': 'COLUMNS',
                      'startIndex': 1, 'endIndex': 2},
            'properties': {'pixelSize': 560}, 'fields': 'pixelSize'}},
    ]}).execute()
    return sheet_id

def write_views(svc, sheet_id, n_rows):
    """Drop every existing filter view on the board, then recreate the eight."""
    meta = svc.get(spreadsheetId=MPL_SHEET_ID,
                   fields='sheets(properties/sheetId,filterViews/filterViewId)').execute()
    reqs = []
    for s in meta['sheets']:
        if s['properties']['sheetId'] != sheet_id:
            continue
        for fv in s.get('filterViews', []) or []:
            reqs.append({'deleteFilterView': {'filterId': fv['filterViewId']}})
    for title, criteria in VIEWS:
        reqs.append({'addFilterView': {'filter': {
            'title': title,
            'range': {'sheetId': sheet_id, 'startRowIndex': 0,
                      'endRowIndex': n_rows + 1,
                      'startColumnIndex': 0, 'endColumnIndex': len(COLUMNS)},
            'criteria': {str(k): v for k, v in criteria.items()},
            'sortSpecs': [{'dimensionIndex': col('Rank'), 'sortOrder': 'ASCENDING'}],
        }}})
    if reqs:
        svc.batchUpdate(spreadsheetId=MPL_SHEET_ID, body={'requests': reqs}).execute()

def push(svc, rows):
    meta = svc.get(spreadsheetId=MPL_SHEET_ID).execute()
    existing = {s['properties']['title']: s['properties']['sheetId'] for s in meta['sheets']}
    if BOARD_TAB not in existing:
        res = svc.batchUpdate(spreadsheetId=MPL_SHEET_ID, body={'requests': [
            {'addSheet': {'properties': {'title': BOARD_TAB, 'index': 1,
                                         'gridProperties': {'rowCount': len(rows) + 50,
                                                            'columnCount': len(COLUMNS),
                                                            'frozenRowCount': 1}}}}]}).execute()
        sheet_id = res['replies'][0]['addSheet']['properties']['sheetId']
    else:
        sheet_id = existing[BOARD_TAB]
        svc.values().clear(spreadsheetId=MPL_SHEET_ID,
                           range="'%s'" % BOARD_TAB, body={}).execute()
    body = {'values': [COLUMNS] + [[str(r.get(c, '')) for c in COLUMNS] for r in rows]}
    svc.values().update(spreadsheetId=MPL_SHEET_ID,
                        range="'%s'!A1" % BOARD_TAB,
                        valueInputOption='RAW', body=body).execute()

    # Second pass, USER_ENTERED, for the Doc column only. Everything else stays
    # RAW so a title that starts with "=" or "-" is not read as a formula.
    doc_a1 = chr(ord('A') + DOC_COL) if DOC_COL < 26 else 'A' + chr(ord('A') + DOC_COL - 26)
    docs = []
    for r in rows:
        url, label = r.get('Doc URL', ''), (r.get('Doc', '') or 'doc').replace('"', "'")
        docs.append(['=HYPERLINK("%s","%s")' % (url, label) if url else r.get('Doc', '')])
    svc.values().update(
        spreadsheetId=MPL_SHEET_ID,
        range="'%s'!%s2" % (BOARD_TAB, doc_a1),
        valueInputOption='USER_ENTERED', body={'values': docs}).execute()

    svc.batchUpdate(spreadsheetId=MPL_SHEET_ID, body={'requests': [
        {'repeatCell': {
            'range': {'sheetId': sheet_id, 'startRowIndex': 0, 'endRowIndex': 1},
            'cell': {'userEnteredFormat': {
                'backgroundColor': {'red': 0.13, 'green': 0.16, 'blue': 0.22},
                'textFormat': {'bold': True, 'foregroundColor':
                               {'red': 1, 'green': 1, 'blue': 1}}}},
            'fields': 'userEnteredFormat(backgroundColor,textFormat)'}},
        {'setBasicFilter': {'filter': {'range': {
            'sheetId': sheet_id, 'startRowIndex': 0,
            'startColumnIndex': 0, 'endColumnIndex': len(COLUMNS)}}}},
        {'updateSheetProperties': {
            'properties': {'sheetId': sheet_id,
                           'gridProperties': {'frozenRowCount': 1, 'frozenColumnCount': 2}},
            'fields': 'gridProperties(frozenRowCount,frozenColumnCount)'}},
    ]}).execute()
    write_views(svc, sheet_id, len(rows))
    write_readme(svc, meta)
    return sheet_id

def report(rows):
    from collections import Counter
    print('rows: %d' % len(rows))
    print('by status  :', dict(Counter(r['Status'] for r in rows)))
    print('by priority:', dict(Counter(r['Priority'] for r in rows)))
    print('by team    :', dict(Counter(r['Team'] for r in rows)))
    open_rows = [r for r in rows if r['Status'] != 'Done' and 'epic' not in r['Labels']]
    print('open tasks :', len(open_rows))
    load = Counter(r['Assignee'] or '(unassigned)' for r in open_rows
                   if r['Priority'] in ('Urgent', 'High'))
    print('urgent+high load per suggested owner:')
    for name, n in load.most_common():
        tag = '  <- %s' % LEAVERS[name] if name in LEAVERS else ''
        print('   %-24s %3d%s' % (name, n, tag))
    print('pickup ready (unblocked, nobody on it): %d'
          % sum(1 for r in rows if r['Pickup ready'] == 'Yes' and not r['Assignee']))
    print('handover-risk tasks: %d'
          % sum(1 for r in rows if 'handover-risk' in r['Labels']))
    print('with a doc link : %d of %d' % (sum(1 for r in rows if r['Doc URL']), len(rows)))
    no_doc = [r for r in open_rows if not r['Doc URL']]
    print('open work with NO doc: %d' % len(no_doc))
    print('   worst areas:', dict(Counter(r['Project'] for r in no_doc).most_common(6)))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--push', action='store_true',
                    help='write the "Task Board" tab into the MPL spreadsheet')
    ap.add_argument('--readme-only', action='store_true',
                    help='refresh just the how-to tab, leave the board untouched')
    args = ap.parse_args()

    svc = sheets_service()
    if args.readme_only:
        write_readme(svc, svc.get(spreadsheetId=MPL_SHEET_ID).execute())
        print('readme tab refreshed')
        return

    rows = rank(build_mpl_rows(read_mpl(svc)) + build_af_rows())

    with open(OUT_CSV, 'w', encoding='utf-8', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)
    print('wrote %s' % OUT_CSV)
    report(rows)

    if args.push:
        sheet_id = push(svc, rows)
        print('tab "%s" written: https://docs.google.com/spreadsheets/d/%s/edit#gid=%s'
              % (BOARD_TAB, MPL_SHEET_ID, sheet_id))

if __name__ == '__main__':
    main()
