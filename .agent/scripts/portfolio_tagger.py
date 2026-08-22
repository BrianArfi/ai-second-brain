#!/usr/bin/env python3
"""Tag ledger items with a canonical portfolio.

Why this exists
---------------
`waiting_on.json` and `commitments.json` carried no reliable portfolio field, so
`premeeting_cards.py` fell back to joining open items on ATTENDEE. Every meeting
with a wide invite therefore inherited every open item those attendees owed,
regardless of which of the owner's four portfolios the work actually sat in. A
Marketplace sprint review ended up carrying Platform and E-Commerce Solution
items purely because Teammate and Teammate were in the room.

This writes a canonical `portfolio` onto each item so consumers can filter on the
work, not on who happens to be invited.

Resolution order (first hit wins, highest trust first):
  1. `portfolio_source == "manual"` -- sticky, never auto-overwritten
  2. `initiative_id` joined against journal/state/portfolio.json (authoritative)
  3. ALIASES matched against the item's own text/project field
  4. initiative + workstream names from portfolio.json matched against the text
  5. "unknown" -- deliberately NOT guessed from the owner, that was the bug

Usage:
  python3 .agent/scripts/portfolio_tagger.py              # dry-run report
  python3 .agent/scripts/portfolio_tagger.py --apply      # write the field
  python3 .agent/scripts/portfolio_tagger.py --report-unknown
  python3 .agent/scripts/portfolio_tagger.py --set WAIT-0091 ecom-solution --apply
"""

import argparse
import json
import os
import re
import sys
from collections import Counter

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
PORTFOLIO_PATH = os.path.join(BASE_DIR, 'journal', 'state', 'portfolio.json')
COMMITMENTS_PATH = os.path.join(BASE_DIR, 'journal', 'state', 'commitments.json')
WAITING_ON_PATH = os.path.join(BASE_DIR, 'journal', 'state', 'waiting_on.json')

VALID = ('marketplace', 'platform', 'b2c', 'ecom-solution', 'unknown')

# Curated aliases for work that has no initiative entry in portfolio.json yet
# (ExampleClient is the live example: greenfield, built by the Marketplace squad,
# but not registered as an initiative). Each mapping below was verified against a
# primary source -- a PRD/BRD owner line, a MOM label, or the Jira project key.
ALIASES = {
    'marketplace': [
        'entertainer', 'example program', 'exampleprogram', 'al-ExampleProgram', 'exampleco',
        'buy box', 'buybox', 'ExampleFeature', 'apple store', 'ExampleClient', 'kantar',
        'mastercard', 'mcm', 'peoppl', 'agora', 'demo marketplace', 'saib',
        'earn model', 'cashback', 'catalog approval', 'storefront ranking',
        'here maps', 'here map',
    ],
    'platform': [
        'ExampleVendor', 'stc', 'Example Catalogue', 'online catalog',
        'merchandise service', 'merchandise api', 'sab ', 'payout',
        'points exchange', 'sms gateway', 'msg-91', 'twilio', 'floward',
        'rewardsby', 'giftcardsby', 'ExampleVendor', 'awb', 'spl', 'Teammate',
        'notification hub', 'devops', 'selective inventory',
    ],
    'b2c': [
        'b2c', 'superapp', 'super app', 'gifti global', 'giftiglobal',
        'app store launch', 'play store', 'react native', 'mobile app',
        'qitaf', 'card-connect', 'ncnp', 'ExampleVendor', 'in-app',
    ],
    'ecom-solution': [
        'seller portal', 'redemption api', 'storefront analytics', 'oms',
        'order management', 'pim', 'tms', 'promo engine', 'mixed payment',
        'sharaf dg', 'ecommerce core', 'e-commerce core', 'ecomm core',
        'ecommcore', 'front-end builder', 'product information management',
    ],
}

# Short tokens that would false-positive as substrings ("oto" matches photo,
# moto, automation), so they are matched on word boundaries instead.
WORD_ALIASES = {
    # OTO logistics aggregator sits with E-Commerce Solution, NOT Platform.
    # Confirmed by the owner 27 Jul 2026 and by primary source: Teammate Rasheed runs
    # it as an Ecom Solution sprint item (MOM 2026-06-18, and the 2026-06-22
    # action "Confirm OTO logistics aggregator owner w/ Teammate" is assigned to
    # Teammate). journal/state/portfolio.json was wrong and has been corrected.
    'ecom-solution': [r'\boto\b'],
}

# Jira project keys are an authoritative portfolio signal: each board belongs to
# exactly one team (see BOARDS in .agent/skills/jira-connector/scripts/jira_client.py).
# Order matters -- MPS must be tested before MP, or every MPS ticket reads as MP.
TICKET_PREFIXES = [
    ('MPS-', 'platform'),
    ('MP-', 'marketplace'),
    ('MBA-', 'b2c'),
    ('MSP-', 'ecom-solution'),
    ('STOR-', 'ecom-solution'),
    ('COM-', None),   # our own ledger ids, never a portfolio signal
    ('WAIT-', None),
]

# "Storefront" names TWO different things and the word alone resolves nothing:
#   - Marketplace owns the client-facing storefront INSTANCES (ExampleCo/Example Program,
#     ExampleClient, MCM, Peoppl ...) -- the running shops.
#   - E-Commerce Solution owns the storefront PRODUCT (Front-end Builder +
#     Template, Storefront API, Storefront Analytics) -- the thing instances are
#     built with.
# Resolve by the surrounding context, and return None when it is genuinely
# ambiguous rather than defaulting to either side.
STOREFRONT_INSTANCE_MARKERS = [
    'exampleco', 'example program', 'exampleprogram', 'al-ExampleProgram', 'ExampleClient', 'kantar',
    'mastercard', 'mcm', 'peoppl', 'agora', 'cib', 'saib', 'entertainer',
    'aaib', 'demo marketplace',
]
STOREFRONT_PRODUCT_MARKERS = [
    'builder', 'template', 'analytics', 'storefront api', 'sdk', 'subscription',
    'tenant onboarding', 'parity', 'stor-', 'front-end', 'frontend',
    'sandbox', 'public api',
]

def resolve_storefront(text):
    """Which portfolio a 'storefront' mention belongs to, or None if ambiguous."""
    if 'storefront' not in text:
        return None
    product = any(m in text for m in STOREFRONT_PRODUCT_MARKERS)
    instance = any(m in text for m in STOREFRONT_INSTANCE_MARKERS)
    if product and not instance:
        return 'ecom-solution'
    if instance and not product:
        return 'marketplace'
    return None

# Free-text `project` values seen in commitments.json, normalised.
PROJECT_MAP = {
    'marketplace': 'marketplace',
    'marketplace platform': 'marketplace',
    'example program': 'marketplace',
    'example program / exampleco': 'marketplace',
    'exampleprogram store ui/ux revamp': 'marketplace',
    'exampleprogram earn model': 'marketplace',
    'exampleprogram apple store': 'marketplace',
    'exampleco merchandise': 'marketplace',
    'entertainer': 'marketplace',
    'platform': 'platform',
    'work/platform': 'platform',
    'ExampleVendor': 'platform',
    'fulfillment': 'platform',
    'b2c superapp': 'b2c',
    'b2c super app': 'b2c',
    'e-commerce solution': 'ecom-solution',
    'ecommerce solution': 'ecom-solution',
    'ecommerce': 'ecom-solution',
    'ecommerce solution / storefront': 'ecom-solution',
    'seller portal': 'ecom-solution',
}

# Explicitly not a Work portfolio -- personal brand or cross-cutting admin.
NON_PORTFOLIO = {'general', 'work', 'taaruf lalu nikah', 'you', 'hsi'}

def load_json(path, default):
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default

def items_of(blob):
    """Both ledgers store `items` as either a dict keyed by id or a list."""
    raw = blob.get('items', {})
    return list(raw.values()) if isinstance(raw, dict) else list(raw)

def build_portfolio_index():
    """Map initiative_id -> team id, plus team-owned name keywords."""
    blob = load_json(PORTFOLIO_PATH, {})
    ini_to_team = {}
    name_keywords = {}
    for team in blob.get('teams', []):
        tid = team.get('id')
        if not tid:
            continue
        kws = set()
        for ini in team.get('initiatives', []):
            iid = ini.get('id')
            if iid:
                ini_to_team[iid] = tid
            if ini.get('name'):
                kws.add(ini['name'].lower())
            for ws in ini.get('workstreams', []):
                if isinstance(ws, dict) and ws.get('name'):
                    kws.add(ws['name'].lower())
                elif isinstance(ws, str):
                    kws.add(ws.lower())
        name_keywords[tid] = kws
    return ini_to_team, name_keywords

def item_text(item):
    parts = [
        item.get('what') or '',
        item.get('text') or '',
        item.get('project') or '',
        item.get('notes') if isinstance(item.get('notes'), str) else '',
    ]
    return ' '.join(parts).lower()

def classify(item, ini_to_team, name_keywords):
    """Return (portfolio, source). Never infers from the owner."""
    if item.get('portfolio_source') == 'manual' and item.get('portfolio') in VALID:
        return item['portfolio'], 'manual'

    iid = item.get('initiative_id')
    if iid and iid in ini_to_team:
        return ini_to_team[iid], 'initiative_id'

    text_raw = ' '.join([item.get('what') or '', item.get('text') or '',
                         item.get('project') or ''])
    for prefix, tid in TICKET_PREFIXES:
        if tid and re.search(rf'\b{prefix}\d+', text_raw, re.IGNORECASE):
            return tid, f'ticket:{prefix.rstrip("-")}'

    project = (item.get('project') or '').strip().lower()
    non_portfolio = False
    if project:
        if project in PROJECT_MAP:
            return PROJECT_MAP[project], 'project_field'
        if project in NON_PORTFOLIO:
            # A catch-all project like "General" is not evidence of anything.
            # Note it, but still let the text decide before giving up.
            non_portfolio = True

    text = item_text(item)
    if text.strip():
        storefront = resolve_storefront(text)
        if storefront:
            return storefront, 'storefront-context'

        for tid, needles in ALIASES.items():
            for needle in needles:
                if needle in text:
                    return tid, f'alias:{needle.strip()}'

        for tid, patterns in WORD_ALIASES.items():
            for pattern in patterns:
                if re.search(pattern, text):
                    return tid, f'word:{pattern}'

        for tid, kws in name_keywords.items():
            for kw in kws:
                # only trust reasonably specific names, short ones over-match
                if len(kw) >= 8 and kw in text:
                    return tid, 'portfolio_name'

    return 'unknown', 'non_portfolio' if non_portfolio else 'unresolved'

def process(path, ini_to_team, name_keywords, apply_changes, overrides):
    blob = load_json(path, None)
    if blob is None:
        print(f'  ! cannot read {path}', file=sys.stderr)
        return None

    raw = blob.get('items', {})
    seq = raw.values() if isinstance(raw, dict) else raw

    stats = Counter()
    changed = 0
    unknown_open = []

    for item in seq:
        iid = item.get('id')
        if iid in overrides:
            portfolio, source = overrides[iid], 'manual'
        else:
            portfolio, source = classify(item, ini_to_team, name_keywords)

        stats[portfolio] += 1
        if item.get('status') in ('open', 'breached') and portfolio == 'unknown':
            unknown_open.append((iid, (item.get('what') or item.get('text') or '')[:70]))

        if item.get('portfolio') != portfolio or item.get('portfolio_source') != source:
            changed += 1
            if apply_changes:
                item['portfolio'] = portfolio
                item['portfolio_source'] = source

    if apply_changes and changed:
        # indent=1 matches how the ledger writers format these files. Using a
        # different indent reformats every line and buries a 600-line change in
        # an 18,000-line diff, which also makes concurrent cron writes conflict.
        #
        # tmp + os.replace, like every other writer of these files. This used to
        # be a plain `open(path, 'w')`, which meant a crash or a kill partway
        # through the dump left `commitments.json` truncated and unparseable --
        # on the two hottest files in the repo, from a script that runs
        # unattended.
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(blob, fh, indent=1, ensure_ascii=False)
            fh.write('\n')
        os.replace(tmp, path)

    return {'path': path, 'stats': stats, 'changed': changed, 'unknown_open': unknown_open}

def main():
    ap = argparse.ArgumentParser(description='Tag ledger items with a canonical portfolio.')
    ap.add_argument('--apply', action='store_true', help='write changes (default: dry-run)')
    ap.add_argument('--report-unknown', action='store_true',
                    help='list open/breached items that could not be resolved')
    ap.add_argument('--set', nargs=2, action='append', metavar=('ID', 'PORTFOLIO'),
                    help='force one item to a portfolio, marked manual and sticky')
    args = ap.parse_args()

    overrides = {}
    for iid, portfolio in (args.set or []):
        if portfolio not in VALID:
            sys.exit(f'portfolio_tagger: invalid portfolio {portfolio!r}, pick from {VALID}')
        overrides[iid] = portfolio

    ini_to_team, name_keywords = build_portfolio_index()
    if not ini_to_team:
        sys.exit('portfolio_tagger: portfolio.json has no teams, refusing to tag blind')

    mode = 'APPLY' if args.apply else 'DRY-RUN'
    print(f'portfolio_tagger [{mode}]  teams={len(name_keywords)} initiatives={len(ini_to_team)}')

    results = []
    for path in (WAITING_ON_PATH, COMMITMENTS_PATH):
        res = process(path, ini_to_team, name_keywords, args.apply, overrides)
        if res:
            results.append(res)

    for res in results:
        name = os.path.basename(res['path'])
        total = sum(res['stats'].values())
        print(f'\n{name}  ({total} items, {res["changed"]} would change)'
              if not args.apply else
              f'\n{name}  ({total} items, {res["changed"]} written)')
        for tid, n in res['stats'].most_common():
            print(f'   {n:4d}  {tid}')

    if args.report_unknown:
        print('\nUNRESOLVED open/breached items (need --set or a new alias):')
        any_unknown = False
        for res in results:
            for iid, text in res['unknown_open']:
                any_unknown = True
                print(f'   {iid}  {text}')
        if not any_unknown:
            print('   none')

    if not args.apply:
        print('\nDry-run only. Re-run with --apply to write.')

if __name__ == '__main__':
    # This script rewrites commitments.json and waiting_on.json wholesale, so on
    # an --apply run it takes the same locks the ledger CLIs take. Without them a
    # cron sweep mid read-modify-write has its file swapped underneath it and
    # writes its stale copy back, which is the lost-update this repo has already
    # paid for twice. A dry run reads only and stays lock-free so a long report
    # cannot block a writer.
    #
    # Acquired in sorted order, matching ledger_sync.all_ledger_locks, so a fixed
    # order cannot deadlock against it.
    if '--apply' in sys.argv[1:]:
        sys.path.insert(0, os.path.join(BASE_DIR, '.agent', 'scripts'))
        from ledger_lock import hold_ledger_lock
        for _ledger in ('commitments', 'waiting_on'):
            hold_ledger_lock(_ledger)
    main()
