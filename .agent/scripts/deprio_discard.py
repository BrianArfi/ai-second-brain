#!/usr/bin/env python3
"""deprio_discard.py - the lifecycle between "past SLA" and "dropped".

The problem
-----------
A waiting-on record past its SLA has exactly two fates today: it gets chased
forever, or somebody deletes it. Neither is right. Chasing forever is how 297
records reached breached status and stopped meaning anything. Deleting on age
alone throws away real work, because age is not evidence: a record can be 54
days old and answered, or 54 days old and load-bearing.

So there is a middle. A record earns its way out, through stages, and the only
thing that can push it to the last stage is evidence plus a notice that went
out and went unanswered.

The stages
----------
  ACTIVE          under 21 days. Normal chasing.
  DEPRIORITISED   21 days or older with no response. Stops competing with live
                  work in briefings and reports. Still visible, still real.
  NOTICE_DUE      deprioritised, hunted, and the hunt found NO_TRACE. A final
                  notice is owed to the owner: here is everything of yours I am
                  still waiting on, and here is the date I will drop it.
  NOTICE_SENT     the notice went out. A 7 day clock starts.
  DISCARD_READY   30 days or older, notice sent 7 or more days ago, still no
                  response, and the hunt still says NO_TRACE. Only now may it
                  be dropped.

Four gates stand between a record and deletion, and all four must hold:

  1. age          at least 30 days since the ask
  2. evidence     answer_hunt returned NO_TRACE, not LEAD and not UNVERIFIABLE.
                  A LEAD means somebody has to read it first. UNVERIFIABLE
                  means the check could not run, which is not permission.
  3. notice       a final notice naming this record went to the owner
  4. grace        7 days passed since that notice with no response

A record that fails gate 2 is never discarded automatically, however old it is.
That is the whole point: the answer to "how do you know they did not reply" is
that four independent things have to agree, not that a timer ran out.

State lives in journal/state/waiting_lifecycle.json, which is this script's own
file. It deliberately does not add fields to waiting_on.json: that ledger is
written by seven cron jobs under a lock, and widening its schema to carry
workflow state would be a change to all of them.

Usage
-----
    python3 .agent/scripts/deprio_discard.py report --hunt /tmp/hunt_21d.json
    python3 .agent/scripts/deprio_discard.py notices --hunt /tmp/hunt_21d.json
    python3 .agent/scripts/deprio_discard.py mark-notice-sent --owner "Teammate Meer"
    python3 .agent/scripts/deprio_discard.py discard --hunt /tmp/hunt_21d.json [--apply]
"""
import argparse
import json
import os
import subprocess
import sys
import time
from collections import defaultdict

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
STATE = os.path.join(BASE_DIR, 'journal', 'state')
LIFECYCLE = os.path.join(STATE, 'waiting_lifecycle.json')
ESCALATIONS = os.path.join(BASE_DIR, 'journal', 'escalations')
WAIT_CLI = os.path.join(BASE_DIR, '.agent', 'skills', 'waiting-watchdog',
                        'scripts', 'waiting_watchdog.py')

sys.path.insert(0, os.path.join(BASE_DIR, '.agent', 'scripts'))
from waiting_owner_alias import canonical  # noqa: E402

DEPRIO_DAYS = 21.0
DISCARD_DAYS = 30.0
GRACE_DAYS = 7.0
DAY = 86400.0

def load_items():
    with open(os.path.join(STATE, 'waiting_on.json'), encoding='utf-8') as fh:
        return json.load(fh)['items']

def load_lifecycle():
    try:
        with open(LIFECYCLE, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {'notices': {}, 'version': 1}

def save_lifecycle(data):
    os.makedirs(os.path.dirname(LIFECYCLE), exist_ok=True)
    tmp = LIFECYCLE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
    os.replace(tmp, LIFECYCLE)

def load_hunt(path):
    if not path:
        return {}
    with open(path, encoding='utf-8') as fh:
        return {r['id']: r for r in json.load(fh)}

def age_days(rec):
    return (time.time() - (rec.get('since') or time.time())) / DAY

def clip(text, n):
    """Cut at a word boundary, and at the first sentence or clause if one ends
    early. A bullet that stops mid-word reads as a broken message, not a short
    one, and this text goes out as the owner."""
    s = ' '.join((text or '').split())
    for stop in ('. ', '; '):
        i = s.find(stop)
        if 0 < i <= n:
            return s[:i]
    if len(s) <= n:
        return s
    cut = s[:n].rsplit(' ', 1)[0]
    return cut.rstrip(',;:') + '...'

def open_items(items):
    return {k: r for k, r in items.items()
            if (r.get('status') or 'open') in ('open', 'breached')}

def stage(rec, hunt, lifecycle):
    """Which stage this record is in, and why."""
    a = age_days(rec)
    if a < DEPRIO_DAYS:
        return 'ACTIVE', f'{a:.0f}d, under the {DEPRIO_DAYS:.0f} day line'

    h = hunt.get(rec['id'])
    notice = lifecycle['notices'].get(rec['id'])

    if notice:
        since_notice = (time.time() - notice['sent_at']) / DAY
        if (a >= DISCARD_DAYS and since_notice >= GRACE_DAYS
                and h and h['verdict'] == 'NO_TRACE'):
            return 'DISCARD_READY', (f'{a:.0f}d, notice sent {since_notice:.0f}d ago, '
                                     f'hunt found no trace in {len(h["probes_run"])} sources')
        return 'NOTICE_SENT', f'notice sent {since_notice:.0f}d ago, grace period running'

    if not h:
        return 'DEPRIORITISED', f'{a:.0f}d, not hunted yet'
    if h['verdict'] == 'ANSWERED_LIKELY':
        return 'ANSWERED_LIKELY', 'the hunt found the owner answering: ' + (
            h['detail'].get(h['strong'][0], '') if h['strong'] else '')
    if h['verdict'] == 'LEAD':
        return 'READ_THE_LEAD', 'weak evidence exists, somebody has to read it: ' + ', '.join(h['weak'])
    if h['verdict'] == 'UNVERIFIABLE':
        return 'UNVERIFIABLE', f'only {len(h["probes_run"])} of 6 sources could be checked'
    return 'NOTICE_DUE', f'{a:.0f}d, no trace in {len(h["probes_run"])} sources, notice owed'

def classify(items, hunt, lifecycle):
    out = defaultdict(list)
    for rec in open_items(items).values():
        s, why = stage(rec, hunt, lifecycle)
        out[s].append((rec, why))
    for v in out.values():
        v.sort(key=lambda x: -age_days(x[0]))
    return out

ORDER = ['ANSWERED_LIKELY', 'READ_THE_LEAD', 'NOTICE_DUE', 'NOTICE_SENT',
         'DISCARD_READY', 'UNVERIFIABLE', 'DEPRIORITISED', 'ACTIVE']

def cmd_report(args):
    items, hunt, lc = load_items(), load_hunt(args.hunt), load_lifecycle()
    groups = classify(items, hunt, lc)
    total = sum(len(v) for v in groups.values())
    print(f'{total} open waiting-on records\n')
    for s in ORDER:
        rows = groups.get(s) or []
        if not rows:
            continue
        print(f'== {s}: {len(rows)}')
        for rec, why in rows[:args.show]:
            print(f'   {rec["id"]:10s} {age_days(rec):5.0f}d  {canonical(rec.get("owner")):22s} {why[:90]}')
        if len(rows) > args.show:
            print(f'   ... and {len(rows) - args.show} more')
        print()

def cmd_notices(args):
    """Draft one final notice per owner, covering every NOTICE_DUE record."""
    items, hunt, lc = load_items(), load_hunt(args.hunt), load_lifecycle()
    groups = classify(items, hunt, lc)
    due = groups.get('NOTICE_DUE') or []
    if not due:
        print('nothing is owed a final notice')
        return
    byo = defaultdict(list)
    for rec, _ in due:
        byo[canonical(rec.get('owner'))].append(rec)

    os.makedirs(ESCALATIONS, exist_ok=True)
    deadline = time.strftime('%d %B', time.localtime(time.time() + GRACE_DAYS * DAY))
    written = []
    for owner, recs in sorted(byo.items(), key=lambda x: -len(x[1])):
        recs.sort(key=lambda r: -age_days(r))
        slug = owner.lower().replace(' ', '-').replace('/', '-')
        path = os.path.join(ESCALATIONS, f'notice_{slug}.md')
        L = [f'# Final notice: {owner}, {len(recs)} unanswered items\n',
             'Every item below was checked across six sources and no reply was found.\n',
             '**Purpose:** one message, listing everything of theirs that is still open, '
             f'with a date. If nothing comes back by {deadline}, these get dropped.\n',
             '\n## The items\n']
        for r in recs:
            L.append(f'- `{r["id"]}` **{age_days(r):.0f}d**: {(r.get("what") or "").strip()[:200]}')
        L.append('\n## Draft message\n')
        first = recs[0]
        if len(recs) == 1:
            L.append(f'> hi, still need one thing from you: {clip(first.get("what"), 110)} '
                     f'been {age_days(first):.0f} days on this. if it is not needed any more just '
                     f'say so and i will close it, otherwise can you get to it before {deadline} ya')
        else:
            L.append(f'> hi, i have {len(recs)} things still open on your side, oldest is '
                     f'{age_days(first):.0f} days. listing them so you can see them in one place. '
                     f'anything that is no longer needed, say so and i will close it. '
                     f'the rest i need before {deadline} ya')
            L.append('>')
            for r in recs[:8]:
                L.append(f'> - {clip(r.get("what"), 90)}')
            if len(recs) > 8:
                L.append(f'> - and {len(recs) - 8} more')
        L.append('\n## After it goes out\n')
        L.append('```bash')
        L.append(f'python3 .agent/scripts/deprio_discard.py mark-notice-sent --owner "{owner}"')
        L.append('```')
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(L) + '\n')
        written.append((owner, len(recs), path))

    print(f'{len(written)} notice(s) drafted in {ESCALATIONS}')
    for owner, n, path in written:
        print(f'  {owner:24s} {n:3d} items  {path}')
    print('\nNothing has been sent. Each of these is approval-gated like any other message.')

def cmd_mark_notice_sent(args):
    """Record that the final notice went out, which starts the grace clock."""
    items, hunt, lc = load_items(), load_hunt(args.hunt), load_lifecycle()
    groups = classify(items, hunt, lc)
    pool = (groups.get('NOTICE_DUE') or [])
    target = canonical(args.owner)
    ids = [r['id'] for r, _ in pool if canonical(r.get('owner')) == target]
    if args.ids:
        ids = args.ids
    if not ids:
        print(f'no NOTICE_DUE records for {target}')
        return
    now = time.time()
    for i in ids:
        lc['notices'][i] = {'sent_at': now, 'owner': target,
                            'permalink': args.permalink or None}
    save_lifecycle(lc)
    print(f'marked {len(ids)} record(s) as noticed for {target}')
    for i in ids:
        subprocess.run(['python3', WAIT_CLI, 'touch', i], cwd=BASE_DIR,
                       capture_output=True, text=True)
    print('stamped last_nudge_at on each, so the chase queue stops re-chasing them')

def cmd_discard(args):
    items, hunt, lc = load_items(), load_hunt(args.hunt), load_lifecycle()
    groups = classify(items, hunt, lc)
    ready = groups.get('DISCARD_READY') or []
    print(f'{len(ready)} record(s) pass all four gates')
    for rec, why in ready:
        print(f'  {rec["id"]:10s} {age_days(rec):5.0f}d {canonical(rec.get("owner")):22s} {why[:80]}')
    if not ready or not args.apply:
        if ready:
            print('\ndry run. re-run with --apply to drop these')
        return
    env = dict(os.environ, LEDGER_SYNC_OFFLINE='1')
    for rec, why in ready:
        note = (f'Discarded under the deprioritise-and-discard policy: {why}. '
                f'A final notice went to {canonical(rec.get("owner"))} and went unanswered. '
                f'Reopen if it turns out to matter.')
        p = subprocess.run(['python3', WAIT_CLI, 'drop', rec['id'], '--note', note],
                           cwd=BASE_DIR, env=env, capture_output=True, text=True)
        print(('  ok drop ' if p.returncode == 0 else '  FAIL ') + rec['id'])

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    def common(p):
        p.add_argument('--hunt', help='answer_hunt.py --json output')

    r = sub.add_parser('report', help='what is in which stage')
    common(r)
    r.add_argument('--show', type=int, default=12)
    r.set_defaults(func=cmd_report)

    n = sub.add_parser('notices', help='draft one final notice per owner')
    common(n)
    n.set_defaults(func=cmd_notices)

    m = sub.add_parser('mark-notice-sent', help='start the grace clock')
    common(m)
    m.add_argument('--owner', required=True)
    m.add_argument('--ids', nargs='*')
    m.add_argument('--permalink')
    m.set_defaults(func=cmd_mark_notice_sent)

    d = sub.add_parser('discard', help='drop what passes all four gates')
    common(d)
    d.add_argument('--apply', action='store_true')
    d.set_defaults(func=cmd_discard)

    args = ap.parse_args()
    args.func(args)

if __name__ == '__main__':
    main()
