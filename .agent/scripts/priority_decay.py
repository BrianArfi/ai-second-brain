#!/usr/bin/env python3
"""priority_decay.py - a record's priority drops when nobody raises it any more.

Added 13 Oct 2026 at the owner's request: "kalo ada task yang gak dimention dan gak di
raise atau disebut dimana mana selama 2 weeks, dia bisa turun satu level prionya".

Priority is CALCULATED on every read, never stored. A stored priority needs someone
to lower it by hand, and nobody does.

    effective = base priority + one level per step of silence, floor P4

    base       P1 or P2 from the record (COM `priority: true` -> P1, else P2;
               WAIT and DEC -> P2), or an override set with `set-base`.
    silence    days since the last SIGNAL, the last time the item came up anywhere.
               14+ days silent -> one level down. 30+ days -> two levels down, and
               the record goes on the Saturday review list ("close or drop?"),
               because docs/ledger_hygiene.md rule 2 says no record sits 30 days.
    signal     any of these, newest wins (so a new signal restores the base):
                 - the record was opened
                 - the owner chased it (`last_nudge_at`, WAIT)
                 - a reply in its Slack thread (slack_mention_ledger.json)
                 - a meeting note (Clients/Work/meetings/*.md) that cites its id
                 - a decision that cites its id, or the decision itself changed (DEC)
                 - a ticket it names was updated (autoclose_check.py, autoclose_state.json)
                 - `priority_decay.py signal <ID> --why "..."`, for a call or a chat
               Our own briefings, Dashboard sections and the tracker are NOT signals.
               If they were, the daily update would keep every record alive by
               listing it.
    exempt     never decays while either of these holds:
                 - due date or deadline within the next 7 days
                 - YourManager or Julie is the recipient, owner, decider or stakeholder
    slowed     drops at most ONE level, and never reaches the review list, while its
               own work-tree node is live: named on an open P0/P1 line in
               journal/todo.md, or node status `critical`. Exact node only; a child
               does not inherit it, or one P1 line on a domain root would protect
               hundreds of records. Not a full exemption: a record nobody raised for
               a month under a hot initiative is usually superseded.
               A meeting is not an exemption. Premeeting cards list every open record
               for each attendee, so they would protect most of the ledger. If the
               record comes up in the meeting, the MoM cites it and that is a signal.

    priority_decay.py report                 # counts per level, what moved, review list size
    priority_decay.py report --list P1       # every record at P1 (or P2, P3, P4, review)
    priority_decay.py report --markdown      # the tracker section
    priority_decay.py report --json          # every annotation, for other scripts
    priority_decay.py show COM-1234
    priority_decay.py signal WAIT-0930 --why "Palak raised it in the sprint review"
    priority_decay.py set-base COM-1234 P1   # or `clear` to go back to the default

Other scripts import `annotate()`. Nothing here writes a ledger; `signal` and
`set-base` write only journal/state/priority_signals.json, under the ledger lock.
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import subprocess
import sys
import time

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
STATE = os.path.join(BASE, 'journal', 'state')
SIGNALS_FILE = os.path.join(STATE, 'priority_signals.json')
MOM_DIR = os.path.join(BASE, 'Clients', 'Work', 'meetings')
TODO = os.path.join(BASE, 'journal', 'todo.md')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

WIB = dt.timezone(dt.timedelta(hours=7))
DAY = 86400.0
LEVELS = ('P1', 'P2', 'P3', 'P4')
STEP_DAYS = (14, 30)          # silent this long -> one level down per threshold passed
REVIEW_DAYS = 30              # silent this long, not exempt -> Saturday review
DUE_SOON_DAYS = 7
VIP = {'YourManager-Teammate', 'u08fvmp2gkw', 'julie-barbier-leblan', 'julie-barbier', 'julie', 'usu5yjhnd'}
ID_RE = re.compile(r'\b(?:COM|WAIT|DEC)-\d{4}\b')
SLACK_RE = re.compile(r'archives/([A-Z0-9]+)/p(\d{10})(\d{6})(?:[^\s]*?thread_ts=(\d+\.\d+))?')

def load(path, default):
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default

def wib_date(ts):
    return dt.datetime.fromtimestamp(ts, WIB).strftime('%Y-%m-%d') if ts else None

def date_ts(s):
    """'YYYY-MM-DD' -> noon WIB that day, or None."""
    try:
        return dt.datetime.fromisoformat(str(s)[:10]).replace(hour=12, tzinfo=WIB).timestamp()
    except (TypeError, ValueError):
        return None

# ------------------------------------------------------------ sources ---

def live_records():
    """id -> (ledger, record) for every open record in the three ledgers."""
    out = {}
    for fn, ledger, states in (('commitments.json', 'COM', ('open',)),
                               ('waiting_on.json', 'WAIT', ('open', 'breached')),
                               ('decisions.json', 'DEC', ('open',))):
        for rid, rec in load(os.path.join(STATE, fn), {}).get('items', {}).items():
            if rec.get('status') in states:
                out[rid] = (ledger, rec)
    return out

def slack_threads(rec):
    """(channel, thread_ts) pairs a record points at."""
    pairs = set()
    if rec.get('channel') and rec.get('thread_ts'):
        pairs.add((rec['channel'], rec['thread_ts']))
    src = rec.get('source') or {}
    urls = [rec.get('permalink'), src.get('permalink'), src.get('ref'), src.get('url')]
    urls += [s.get('url') for s in rec.get('sources') or [] if isinstance(s, dict)]
    for u in urls:
        m = SLACK_RE.search(str(u or ''))
        if m:
            pairs.add((m.group(1), m.group(4) or f'{m.group(2)}.{m.group(3)}'))
    return pairs

def slack_activity():
    """(channel, thread_ts) -> newest human message ts seen in that thread."""
    led = load(os.path.join(STATE, 'slack_mention_ledger.json'), {})
    act = {}
    for key, t in (led.get('threads') or {}).items():
        ch, _, ts = key.partition(':')
        if t.get('last_activity'):
            act[(ch, ts)] = max(act.get((ch, ts), 0), float(t['last_activity']))
    for it in (led.get('items') or {}).values():
        if str(it.get('author') or '').startswith('B'):   # bot users
            continue
        root = it.get('thread_ts') or it.get('ts')
        try:
            ts = float(it.get('ts'))
        except (TypeError, ValueError):
            continue
        k = (it.get('channel'), root)
        act[k] = max(act.get(k, 0), ts)
    return act

def citations():
    """id -> (newest ts, where) for meeting notes and decisions that cite the id."""
    cite = {}

    def bump(rid, ts, where):
        if ts and ts > cite.get(rid, (0, ''))[0]:
            cite[rid] = (ts, where)
    for path in glob.glob(os.path.join(MOM_DIR, '*.md')):
        m = re.search(r'(\d{4}-\d{2}-\d{2})', os.path.basename(path))
        if not m:
            continue
        try:
            text = open(path, encoding='utf-8', errors='replace').read()
        except OSError:
            continue
        ts = date_ts(m.group(1))
        for rid in set(ID_RE.findall(text)):
            bump(rid, ts, f'meeting notes {os.path.basename(path)}')
    for d in load(os.path.join(STATE, 'decisions.json'), {}).get('items', {}).values():
        ts = d.get('decided_at') or d.get('updated_at') or d.get('created_at')
        blob = json.dumps({k: d.get(k) for k in ('title', 'decision', 'notes', 'sources')}, ensure_ascii=False)
        for rid in set(ID_RE.findall(blob)) - {d.get('id')}:
            bump(rid, ts, f"decision {d.get('id')}")
    return cite

def live_nodes():
    """Nodes named on open P0/P1 todo lines, plus nodes marked critical. Roots excluded."""
    tree = load(os.path.join(STATE, 'work_tree.json'), {})
    roots, live = set(), set()

    def walk(n, p):
        if p is None:
            roots.add(n['id'])
        if n.get('status') == 'critical':
            live.add(n['id'])
        for c in n.get('children') or []:
            walk(c, n['id'])
    for r in tree.get('roots', []):
        walk(r, None)
    try:
        for line in open(TODO, encoding='utf-8'):
            if line.lstrip().startswith('- [ ]') and re.search(r'<!-- P[01] -->', line):
                live |= set(re.findall(r'\[node:([^\]]+)\]', line))
    except OSError:
        pass
    return live - roots

# --------------------------------------------------------------- core ---

def base_level(ledger, rec, override):
    if override in LEVELS[:3]:
        return override, 'set by hand'
    if ledger == 'COM' and rec.get('priority'):
        return 'P1', 'flagged priority'
    return 'P2', 'default'

def due_of(ledger, rec):
    return rec.get('due') if ledger == 'COM' else rec.get('deadline') if ledger == 'DEC' else None

def is_vip(rec):
    keys = [rec.get('to'), rec.get('to_slug'), rec.get('owner_slug'), rec.get('decider_slug')]
    keys += list(rec.get('stakeholder_slugs') or [])
    return any(str(k or '').lower() in VIP for k in keys)

def annotate(now=None):
    """id -> dict(ledger, base, level, silent_days, last_signal, last_signal_src, steps,
    exempt, review, node, title). Pure read; safe to call from any script."""
    now = now or time.time()
    today = wib_date(now)
    recs = live_records()
    side = load(SIGNALS_FILE, {})
    manual = side.get('signals') or {}
    overrides = side.get('base') or {}
    auto = (load(os.path.join(STATE, 'autoclose_state.json'), {}).get('records') or {})
    act = slack_activity()
    cite = citations()
    live = live_nodes()

    out = {}
    for rid, (ledger, rec) in recs.items():
        cands = [(rec.get('first_seen') or rec.get('since') or rec.get('created_at') or now, 'opened')]
        if rec.get('last_nudge_at'):
            cands.append((rec['last_nudge_at'], 'the owner chased it'))
        if ledger == 'DEC' and rec.get('updated_at'):
            cands.append((rec['updated_at'], 'decision record updated'))
        for pair in slack_threads(rec):
            if act.get(pair):
                cands.append((act[pair], 'reply in its Slack thread'))
        if rid in cite:
            cands.append(cite[rid])
        a = auto.get(rid) or {}
        if a.get('last_signal_at'):
            cands.append((a['last_signal_at'], a.get('last_signal_src') or 'ticket moved'))
        for s in manual.get(rid) or []:
            cands.append((s.get('at') or 0, f"noted: {s.get('why') or 'raised again'}"))
        last, src = max(cands, key=lambda c: c[0] or 0)
        silent = max(0.0, (now - (last or now)) / DAY)

        exempt = []
        due = due_of(ledger, rec)
        if due and today <= str(due)[:10] <= wib_date(now + DUE_SOON_DAYS * DAY):
            exempt.append(f'due {str(due)[:10]}')
        if is_vip(rec):
            exempt.append('YourManager or Julie')
        node = rec.get('node')
        slowed = node in live

        base, base_why = base_level(ledger, rec, overrides.get(rid))
        steps = 0 if exempt else sum(1 for d in STEP_DAYS if silent >= d)
        if slowed:
            steps = min(steps, 1)
        level = LEVELS[min(len(LEVELS) - 1, LEVELS.index(base) + steps)]
        out[rid] = dict(ledger=ledger, base=base, base_why=base_why, level=level, steps=steps,
                        silent_days=round(silent, 1), last_signal=wib_date(last), last_signal_src=src,
                        exempt=exempt, slowed=f'live node {node}' if slowed else None,
                        review=(not exempt and not slowed and silent >= REVIEW_DAYS), node=node,
                        title=(rec.get('text') or rec.get('what') or rec.get('title') or '')[:160])
    return out

# ---------------------------------------------------------------- CLI ---

def _order(rid, a):
    return (LEVELS.index(a['level']), -a['silent_days'] if a['review'] else a['silent_days'], rid)

def report(args):
    ann = annotate()
    if args.json:
        print(json.dumps(ann, ensure_ascii=False, indent=1))
        return 0
    if args.list:
        want = args.list.upper()
        rows = [(r, a) for r, a in ann.items()
                if (a['review'] if want == 'REVIEW' else a['level'] == want)]
        for rid, a in sorted(rows, key=lambda x: _order(*x)):
            ex = f" exempt: {', '.join(a['exempt'])}" if a['exempt'] else ''
            ex += f" slowed: {a['slowed']}" if a['slowed'] and not a['exempt'] else ''
            print(f"{rid:9s} {a['level']} (base {a['base']}) silent {a['silent_days']:>5.1f}d, "
                  f"last: {a['last_signal']} {a['last_signal_src']}{ex} [node:{a['node']}]  {a['title'][:90]}")
        print(f'{len(rows)} records')
        return 0

    grid = {lg: {lv: 0 for lv in LEVELS} for lg in ('COM', 'WAIT', 'DEC')}
    for a in ann.values():
        grid[a['ledger']][a['level']] += 1
    ex = [a for a in ann.values() if a['exempt']]
    slow = [a for a in ann.values() if a['slowed'] and not a['exempt']]
    down = [a for a in ann.values() if a['steps']]
    review = [a for a in ann.values() if a['review']]
    why = {}
    for a in ex:
        k = a['exempt'][0].split(' ')[0] if not a['exempt'][0].startswith('YourManager') else 'YourManager/Julie'
        k = {'due': 'due soon'}.get(k, k)
        why[k] = why.get(k, 0) + 1
    if args.markdown:
        print('## 🧭 Relevance (priority decay)\n')
        print('Priority drops one level after 14 days with no signal, and again after 30. '
              'A signal is a Slack reply in the thread, a meeting note or decision citing the record, '
              'a ticket update, or a chase. Briefings do not count. '
              '`python3 .agent/scripts/priority_decay.py report --list P1` lists a level.\n')
        print('| Ledger | P1 | P2 | P3 | P4 dormant |')
        print('|:---|---:|---:|---:|---:|')
        for lg, name in (('COM', 'Commitments'), ('WAIT', 'Waiting-on'), ('DEC', 'Decisions')):
            print(f"| {name} | " + ' | '.join(str(grid[lg][lv]) for lv in LEVELS) + ' |')
        print(f"\n- **Protected from decay**: {len(ex)} ({', '.join(f'{v} {k}' for k, v in sorted(why.items()))})")
        print(f'- **Slowed, under a live node (one level at most)**: {len(slow)}')
        print(f'- **Dropped one or more levels**: {len(down)}')
        print(f'- **Silent 30+ days, for the Saturday review**: {len(review)}')
        p1 = sorted(((r, a) for r, a in ann.items() if a['level'] == 'P1'), key=lambda x: _order(*x))
        if p1:
            print(f'\n**Still P1 ({len(p1)})**\n')
            for rid, a in p1[:25]:
                print(f"- {rid}: {a['title'][:120]} (last signal {a['last_signal']}, {a['last_signal_src']})")
            if len(p1) > 25:
                print(f'- ... {len(p1) - 25} more: `priority_decay.py report --list P1`')
        return 0

    print(f'Relevance on {wib_date(time.time())}: {len(ann)} open records')
    print(f"{'':6s}" + ''.join(f'{lv:>6s}' for lv in LEVELS))
    for lg in ('COM', 'WAIT', 'DEC'):
        print(f'{lg:6s}' + ''.join(f'{grid[lg][lv]:>6d}' for lv in LEVELS))
    print(f"Protected from decay: {len(ex)} ({', '.join(f'{v} {k}' for k, v in sorted(why.items()))})")
    print(f'Slowed under a live node (one level at most): {len(slow)}')
    print(f'Dropped one or more levels: {len(down)}')
    print(f'Silent 30+ days, not protected (Saturday review): {len(review)}')
    return 0

def _write_side(mutate, reason):
    from ledger_lock import hold_ledger_lock
    hold_ledger_lock('priority_signals')
    side = load(SIGNALS_FILE, {'signals': {}, 'base': {}})
    side.setdefault('signals', {})
    side.setdefault('base', {})
    mutate(side)
    tmp = SIGNALS_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(side, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, SIGNALS_FILE)
    if os.environ.get('LEDGER_SYNC_DISABLE') != '1':
        subprocess.run([sys.executable, os.path.join(BASE, '.agent', 'scripts', 'ledger_sync.py'),
                        'sync', '--reason', reason], cwd=BASE)

def _known(rid):
    if rid not in live_records():
        print(f'{rid} is not an open COM, WAIT or DEC record', file=sys.stderr)
        return False
    return True

def cmd_signal(args):
    if not _known(args.id):
        return 1
    at = date_ts(args.at) if args.at else time.time()
    if at is None:
        print('--at must be YYYY-MM-DD', file=sys.stderr)
        return 1

    def m(side):
        lst = side['signals'].setdefault(args.id, [])
        lst.append({'at': at, 'why': args.why[:200], 'logged_at': time.time()})
        side['signals'][args.id] = lst[-10:]
    _write_side(m, f'priority signal {args.id}')
    a = annotate().get(args.id)
    print(f"{args.id} now {a['level']} (base {a['base']}), last signal {a['last_signal']}")
    return 0

def cmd_set_base(args):
    if not _known(args.id):
        return 1
    lv = args.level.upper()
    if lv not in ('P1', 'P2', 'P3', 'CLEAR'):
        print('level must be P1, P2, P3 or clear', file=sys.stderr)
        return 1

    def m(side):
        if lv == 'CLEAR':
            side['base'].pop(args.id, None)
        else:
            side['base'][args.id] = lv
    _write_side(m, f'priority base {args.id} {lv}')
    a = annotate().get(args.id)
    print(f"{args.id} base {a['base']} ({a['base_why']}), now {a['level']}")
    return 0

def cmd_show(args):
    a = annotate().get(args.id)
    if not a:
        print(f'{args.id} is not an open COM, WAIT or DEC record', file=sys.stderr)
        return 1
    print(json.dumps(a, ensure_ascii=False, indent=1))
    return 0

def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('report')
    g = r.add_mutually_exclusive_group()
    g.add_argument('--list', help='P1, P2, P3, P4 or review')
    g.add_argument('--markdown', action='store_true')
    g.add_argument('--json', action='store_true')
    s = sub.add_parser('signal')
    s.add_argument('id')
    s.add_argument('--why', required=True)
    s.add_argument('--at', help='YYYY-MM-DD, default now')
    b = sub.add_parser('set-base')
    b.add_argument('id')
    b.add_argument('level')
    sh = sub.add_parser('show')
    sh.add_argument('id')
    args = ap.parse_args()
    return {'report': report, 'signal': cmd_signal, 'set-base': cmd_set_base, 'show': cmd_show}[args.cmd](args)

if __name__ == '__main__':
    sys.exit(main())
