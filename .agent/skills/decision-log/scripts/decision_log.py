#!/usr/bin/env python3
"""
decision_log.py - Decision Log: a durable ledger of decisions the owner is tracking
across Work/Secondary work (Fathom meetings, Slack threads, GDocs, or captured
manually), so "what did we decide about X" never has to be re-dug from memory.

Design (2026-07-10, per the owner's harness-upgrade plan):
  No cron, no network. This is a Claude-driven capture tool: /mom, morning/evening
  update SOPs, and ad-hoc conversation call `add` / `decide` / `supersede` / `update`
  when a decision surfaces; `report` embeds verbatim into briefings.

Subcommands:
  add        --title --decider [--deadline] [--project] [--source URL --source-type T]
             [--stakeholders "Name A,Name B"] [--notes]
  decide     <id> --decision "..." [--decided-at epoch]
  supersede  <id> --by <id2>
  update     <id> [--title] [--deadline] [--project] [--notes] [--add-source URL --source-type T [--source-label L]]
             [--add-stakeholders "Name A,Name B"] [--status open|decided|superseded]
  list       [--status open|decided|superseded]
  report     [--all]     briefing-ready markdown: overdue-open, open by deadline, decided last 7d

State: journal/state/decisions.json
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
STATE_PATH = os.path.join(BASE_DIR, 'journal', 'state', 'decisions.json')
PEOPLE_PATH = os.path.join(BASE_DIR, 'journal', 'state', 'people.json')

# Every record carries a work-tree node (CLAUDE.md, "Every Ticket Belongs To A
# Work-Tree Node").
sys.path.insert(0, os.path.join(BASE_DIR, '.agent', 'scripts'))
from work_tree import UNFILED, UnknownNode, resolve_or_die  # noqa: E402

WIB = timezone(timedelta(hours=7))
VALID_STATUS = ('open', 'decided', 'superseded')
VALID_SOURCE_TYPES = ('fathom', 'slack', 'gdoc', 'manual')

# ------------------------------------------------------------------- state --

def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {'next_seq': 1, 'items': {}, 'last_sweep': None}

def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f, indent=1, ensure_ascii=False)
    os.replace(tmp, STATE_PATH)

def slugify(name):
    s = re.sub(r'[^a-z0-9]+', '-', name.strip().lower()).strip('-')
    return s or 'unknown'

_PERSON_LOOKUP = None   # cache: {lowercase name/alias/first-name -> canonical slug}

def _load_person_lookup():
    """journal/state/people.json is the canonical roster (owned by the
    stakeholders component). Build a case-insensitive lookup of full name,
    every alias, and unambiguous first names (first token of `name`, only
    when it maps to exactly one roster slug). Cached per process; graceful
    (empty lookup) if people.json is missing or unparseable."""
    global _PERSON_LOOKUP
    if _PERSON_LOOKUP is not None:
        return _PERSON_LOOKUP
    lookup = {}
    people = {}
    if os.path.exists(PEOPLE_PATH):
        try:
            with open(PEOPLE_PATH, encoding='utf-8') as f:
                data = json.load(f)
            people = data.get('people', {}) if isinstance(data, dict) else {}
        except Exception:
            people = {}
    first_name_owners = {}
    for slug, person in people.items():
        name = (person.get('name') or '').strip()
        if name:
            lookup.setdefault(name.lower(), slug)
            first = name.split()[0].lower()
            first_name_owners.setdefault(first, set()).add(slug)
        for alias in person.get('aliases') or []:
            alias = (alias or '').strip()
            if alias:
                lookup.setdefault(alias.lower(), slug)
    for first, slugs in first_name_owners.items():
        if len(slugs) == 1:
            lookup.setdefault(first, next(iter(slugs)))
    _PERSON_LOOKUP = lookup
    return lookup

def resolve_person_slug(name):
    """Resolve a captured name against the people.json roster (full name /
    alias / unambiguous first name, case-insensitive) -> canonical slug.
    Falls back to bare slugify() on a miss or a missing roster."""
    n = (name or '').strip()
    if not n:
        return slugify(name or '')
    return _load_person_lookup().get(n.lower()) or slugify(name)

def next_id(state):
    seq = state.get('next_seq', 1)
    state['next_seq'] = seq + 1
    return f'DEC-{seq:04d}'

def today_wib_date():
    return datetime.now(WIB).date()

def parse_deadline(s):
    """Accept YYYY-MM-DD; return as-is (stored as string) or None."""
    if not s:
        return None
    try:
        datetime.strptime(s, '%Y-%m-%d')
    except ValueError:
        sys.exit(f'bad --deadline (expected YYYY-MM-DD): {s}')
    return s

def split_names(s):
    if not s:
        return []
    return [x.strip() for x in s.split(',') if x.strip()]

# ------------------------------------------------------------------- add ----

def cmd_add(args):
    try:
        node = resolve_or_die(args.node, allow_unfiled=bool(args.node_why))
    except UnknownNode as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)
    state = load_state()
    iid = next_id(state)
    now = time.time()
    stakeholders = split_names(args.stakeholders)
    sources = []
    if args.source:
        sources.append({
            'type': args.source_type or 'manual',
            'url': args.source,
            'label': args.source_label or '',
        })
    item = {
        'id': iid,
        'title': args.title,
        'status': 'open',
        'decision': None,
        'decider': args.decider,
        'decider_slug': resolve_person_slug(args.decider),
        'stakeholder_slugs': [resolve_person_slug(n) for n in stakeholders],
        'project': args.project or None,
        'node': node,
        'node_why': args.node_why,
        'deadline': parse_deadline(args.deadline),
        'sources': sources,
        'superseded_by': None,
        'notes': args.notes or None,
        'created_at': now,
        'decided_at': None,
        'updated_at': now,
    }
    state['items'][iid] = item
    save_state(state)
    print(f'added: {iid} - {args.title}')

def cmd_refile(args):
    """Move a record to another node. The triage pass runs on this."""
    try:
        node = resolve_or_die(args.node, allow_unfiled=bool(args.node_why))
    except UnknownNode as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)
    state = load_state()
    it = state['items'].get(args.item_id)
    if not it:
        print(f'no such decision: {args.item_id}', file=sys.stderr)
        sys.exit(1)
    was = it.get('node', UNFILED)
    it['node'] = node
    it['node_why'] = args.node_why
    it['updated_at'] = time.time()
    save_state(state)
    print(f'{args.item_id}: {was} -> {node}')

# ---------------------------------------------------------------- decide ----

def cmd_decide(args):
    state = load_state()
    it = state['items'].get(args.item_id)
    if not it:
        sys.exit(f'item not found: {args.item_id}')
    now = time.time()
    it.update(
        status='decided',
        decision=args.decision,
        decided_at=args.decided_at if args.decided_at else now,
        updated_at=now,
    )
    save_state(state)
    print(f'decided: {args.item_id} - {args.decision}')

# ------------------------------------------------------------- supersede ----

def cmd_supersede(args):
    state = load_state()
    it = state['items'].get(args.item_id)
    if not it:
        sys.exit(f'item not found: {args.item_id}')
    new_it = state['items'].get(args.by)
    if not new_it:
        sys.exit(f'superseding item not found: {args.by}')
    it.update(status='superseded', superseded_by=args.by, updated_at=time.time())
    save_state(state)
    print(f'superseded: {args.item_id} -> {args.by}')

# ---------------------------------------------------------------- update ----

def cmd_update(args):
    state = load_state()
    it = state['items'].get(args.item_id)
    if not it:
        sys.exit(f'item not found: {args.item_id}')
    if args.title:
        it['title'] = args.title
    if args.deadline:
        it['deadline'] = parse_deadline(args.deadline)
    if args.project:
        it['project'] = args.project
    if args.notes:
        it['notes'] = args.notes
    if args.status:
        if args.status not in VALID_STATUS:
            sys.exit(f'bad --status (expected {VALID_STATUS}): {args.status}')
        it['status'] = args.status
    if args.add_source:
        it.setdefault('sources', []).append({
            'type': args.source_type or 'manual',
            'url': args.add_source,
            'label': args.source_label or '',
        })
    if args.add_stakeholders:
        names = split_names(args.add_stakeholders)
        existing = set(it.get('stakeholder_slugs', []))
        for n in names:
            existing.add(resolve_person_slug(n))
        it['stakeholder_slugs'] = sorted(existing)
    it['updated_at'] = time.time()
    save_state(state)
    print(f'updated: {args.item_id}')

# ------------------------------------------------------------------ list ----

def cmd_list(args):
    state = load_state()
    items = list(state['items'].values())
    if args.status:
        items = [i for i in items if i['status'] == args.status]
    items.sort(key=lambda i: i['id'])
    if not items:
        print('No items.')
        return
    for it in items:
        deadline = it.get('deadline') or '-'
        print(f"{it['id']}  [{it['status']:11s}]  {it['title']}  "
              f"(decider={it['decider']}, deadline={deadline})")

# ---------------------------------------------------------------- report ----

def age_str(ts):
    if not ts:
        return '-'
    h = (time.time() - float(ts)) / 3600
    return f'{h/24:.1f}d' if h >= 24 else f'{h:.0f}h'

def _first_source(it):
    srcs = it.get('sources') or []
    if not srcs:
        return ''
    s = srcs[0]
    label = s.get('label') or s.get('type') or 'source'
    url = s.get('url') or ''
    return f' [{label}]({url})' if url else f' {label}'

def cmd_report(args):
    state = load_state()
    items = list(state['items'].values())
    if not args.all:
        items = [i for i in items if i['status'] != 'superseded']
    if not items:
        print('No decisions tracked. Ledger is clean.')
        return

    today = today_wib_date()

    def deadline_date(it):
        d = it.get('deadline')
        if not d:
            return None
        try:
            return datetime.strptime(d, '%Y-%m-%d').date()
        except ValueError:
            return None

    overdue_open = [i for i in items if i['status'] == 'open'
                    and deadline_date(i) and deadline_date(i) < today]
    open_with_deadline = [i for i in items if i['status'] == 'open'
                          and deadline_date(i) and deadline_date(i) >= today]
    open_no_deadline = [i for i in items if i['status'] == 'open' and not deadline_date(i)]
    cutoff = time.time() - 7 * 86400
    decided_recent = [i for i in items if i['status'] == 'decided'
                      and (i.get('decided_at') or 0) >= cutoff]

    overdue_open.sort(key=lambda i: deadline_date(i))
    open_with_deadline.sort(key=lambda i: deadline_date(i))
    open_no_deadline.sort(key=lambda i: i['id'])
    decided_recent.sort(key=lambda i: -(i.get('decided_at') or 0))

    print('## 🧭 Decision Log\n')

    if overdue_open:
        print(f'### 🔴 Overdue open ({len(overdue_open)})\n')
        for it in overdue_open:
            print(f"- **{it['title']}** · decider: {it['decider']} · "
                  f"deadline {it['deadline']} (overdue){_first_source(it)}  `{it['id']}`")
        print()

    rest_open = open_with_deadline + open_no_deadline
    if rest_open:
        print(f'### 🟡 Open ({len(rest_open)})\n')
        for it in rest_open:
            deadline = it.get('deadline') or 'no deadline'
            print(f"- **{it['title']}** · decider: {it['decider']} · "
                  f"deadline {deadline}{_first_source(it)}  `{it['id']}`")
        print()

    if decided_recent:
        print(f'### ✅ Decided last 7d ({len(decided_recent)})\n')
        for it in decided_recent:
            print(f"- **{it['title']}** -> {it['decision']} · decider: {it['decider']} · "
                  f"{age_str(it['decided_at'])} ago{_first_source(it)}  `{it['id']}`")
        print()

    if not (overdue_open or rest_open or decided_recent):
        print('No open or recently-decided items.')

# -------------------------------------------------------------------- main --

def main():
    p = argparse.ArgumentParser(description='Decision Log ledger CLI')
    sub = p.add_subparsers(dest='cmd')

    ap = sub.add_parser('add')
    ap.add_argument('--title', required=True)
    ap.add_argument('--decider', required=True)
    ap.add_argument('--deadline')
    ap.add_argument('--project')
    ap.add_argument('--source')
    ap.add_argument('--source-type', choices=VALID_SOURCE_TYPES)
    ap.add_argument('--source-label')
    ap.add_argument('--stakeholders')
    ap.add_argument('--notes')
    ap.add_argument('--node', required=True,
                    help='work-tree node id; find one with work_tree.py find <text>')
    ap.add_argument('--node-why', default=None,
                    help='only with --node unfiled, and only for unattended runs')

    rf = sub.add_parser('refile', help='move a record to a different work-tree node')
    rf.add_argument('item_id')
    rf.add_argument('--node', required=True)
    rf.add_argument('--node-why', default=None)

    dp = sub.add_parser('decide')
    dp.add_argument('item_id')
    dp.add_argument('--decision', required=True)
    dp.add_argument('--decided-at', type=float)

    sp = sub.add_parser('supersede')
    sp.add_argument('item_id')
    sp.add_argument('--by', required=True)

    up = sub.add_parser('update')
    up.add_argument('item_id')
    up.add_argument('--title')
    up.add_argument('--deadline')
    up.add_argument('--project')
    up.add_argument('--notes')
    up.add_argument('--status', choices=VALID_STATUS)
    up.add_argument('--add-source')
    up.add_argument('--source-type', choices=VALID_SOURCE_TYPES)
    up.add_argument('--source-label')
    up.add_argument('--add-stakeholders')

    lp = sub.add_parser('list')
    lp.add_argument('--status', choices=VALID_STATUS)

    rp = sub.add_parser('report')
    rp.add_argument('--all', action='store_true')

    args = p.parse_args()
    handlers = {
        'add': cmd_add, 'decide': cmd_decide, 'supersede': cmd_supersede,
        'update': cmd_update, 'list': cmd_list, 'report': cmd_report,
        'refile': cmd_refile,
    }
    handler = handlers.get(args.cmd)
    if not handler:
        p.print_help()
        sys.exit(1)
    handler(args)

READONLY_CMDS = {'report', 'list', 'show'}

if __name__ == '__main__':
    # See .agent/scripts/ledger_lock.py: atomic replace protects the write,
    # not the read-modify-write cycle around it.
    _cmd = next((a for a in sys.argv[1:] if not a.startswith('-')), None)
    if _cmd in READONLY_CMDS:
        main()
    else:
        sys.path.insert(0, os.path.join(BASE_DIR, '.agent', 'scripts'))
        from ledger_lock import hold_ledger_lock
        hold_ledger_lock('decisions')
        # Propagate on the way out (derived views + commit + push) so other
        # sessions read this change immediately. See ledger_sync.py.
        from ledger_sync import run_and_sync
        run_and_sync('decisions', main)
