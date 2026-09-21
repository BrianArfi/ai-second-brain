#!/usr/bin/env python3
"""make_batches.py - regenerate journal/batches/ from the live ledgers.

One file per batch, one batch per sitting. Waiting-on records group by
counterpart, because one conversation clears the whole group. Commitments group
by work-tree node, because one work block does.

Link hygiene is built in, because these files are read in the app:
  - every local link is absolute for the machine this runs on, since the app
    resolves a relative link from the workspace root, not the file's folder
  - only http and https source links are rendered as links. The ledger holds
    some malformed ones (`slack:#channel/p123` is not a URL, and a few source
    refs are relative paths that no longer resolve); those print as plain text
    instead of becoming a link that fails when clicked.

    python3 .agent/scripts/make_batches.py
    python3 .agent/scripts/make_batches.py --out /some/other/dir
"""
import argparse
import json
import os
import sys
import time
from collections import defaultdict

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(BASE_DIR, '.agent', 'scripts'))
from waiting_owner_alias import canonical  # noqa: E402

DAY = 86400.0
DASH = 'http://localhost:3737/#find/'
MIN_OWNER = 5
MIN_NODE = 6

def items(name):
    with open(os.path.join(BASE_DIR, 'journal', 'state', name + '.json'), encoding='utf-8') as fh:
        return json.load(fh)['items']

def age(rec, now):
    return (now - (rec.get('since') or rec.get('first_seen') or now)) / DAY

def card(rid):
    return f'[`{rid}`]({DASH}{rid})'

def slug(s):
    out = ''.join(c if c.isalnum() or c in ' -' else '' for c in (s or '')).strip()
    return out.lower().replace(' ', '-') or 'unknown'

def safe(text):
    """Record text is data, not markdown.

    Some records carry a markdown link inside their own text, pointing at a
    path that was valid wherever the record was written and is not valid here.
    Rendering it as a link gives the reader something that fails when clicked,
    so the link syntax is neutralised and the text is kept.
    """
    return (text or '').replace('](', ') (').strip()

def source_line(rec, label):
    """A link only when it is a real URL. Anything else prints as text."""
    s = rec.get('source') or {}
    ref = (s.get('permalink') or s.get('ref') or '') if isinstance(s, dict) else str(s)
    ref = (ref or '').strip()
    if not ref:
        return None
    if ref.startswith(('http://', 'https://')):
        return f'[{label}]({ref})'
    return f'{label}: `{ref}`'

def write_both(roots, name, body):
    for root in roots:
        os.makedirs(root, exist_ok=True)
        with open(os.path.join(root, name), 'w', encoding='utf-8') as fh:
            fh.write(body)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', action='append', default=None,
                    help='output directory; repeatable. Defaults to journal/batches here.')
    args = ap.parse_args()
    roots = args.out or [os.path.join(BASE_DIR, 'journal', 'batches')]
    now = time.time()

    com = {k: r for k, r in items('commitments').items() if r.get('status') == 'open'}
    wait = {k: r for k, r in items('waiting_on').items()
            if (r.get('status') or 'open') in ('open', 'breached')}

    byo = defaultdict(list)
    for k, r in wait.items():
        byo[canonical(r.get('owner'))].append(k)
    byn = defaultdict(list)
    for k, r in com.items():
        byn[r.get('node') or 'unfiled'].append(k)

    written = []

    owners = sorted(((o, i) for o, i in byo.items() if len(i) >= MIN_OWNER),
                    key=lambda x: -len(x[1]))
    for n, (owner, ids) in enumerate(owners, 1):
        ids.sort(key=lambda k: -age(wait[k], now))
        L = [f'# Waiting on {owner}: {len(ids)} items\n',
             f'One conversation clears all of these. Oldest is {age(wait[ids[0]], now):.0f} days.\n',
             'Work down the list, mark each answered or dropped, then run the command at the bottom.\n']
        for k in ids:
            r = wait[k]
            L.append(f"\n### {card(k)} - {age(r, now):.0f}d, SLA {r.get('sla_hours') or '?'}h")
            L.append(safe(r.get('what')))
            src = source_line(r, 'thread')
            if src:
                L.append('\n' + src)
            if r.get('escalate_to'):
                L.append(f"\nEscalates to: {r['escalate_to']}")
            if (r.get('notes') or '').strip():
                L.append(f"\nNotes: {r['notes'].strip()}")
        L += ['\n\n---\n', '```bash',
              'python3 .agent/skills/waiting-watchdog/scripts/waiting_watchdog.py '
              'close <ID> --note "<the answer they gave>"',
              'python3 .agent/skills/waiting-watchdog/scripts/waiting_watchdog.py '
              'drop <ID> --note "<why it no longer matters>"', '```']
        name = f'wait_{n:02d}_{slug(owner)}.md'
        write_both(roots, name, '\n'.join(L) + '\n')
        written.append(name)

    tail = sorted(((o, i) for o, i in byo.items() if len(i) < MIN_OWNER), key=lambda x: -len(x[1]))
    L = [f'# Waiting on everyone else: {sum(len(i) for _, i in tail)} items '
         f'across {len(tail)} people\n', 'One, two, three or four items each. A person at a time.\n']
    for owner, ids in tail:
        L.append(f'\n## {owner} ({len(ids)})')
        for k in sorted(ids, key=lambda k: -age(wait[k], now)):
            L.append(f"- {card(k)} {age(wait[k], now):.0f}d: {safe(wait[k].get('what'))[:150]}")
    write_both(roots, 'wait_99_long_tail.md', '\n'.join(L) + '\n')
    written.append('wait_99_long_tail.md')

    nodes = sorted(((x, i) for x, i in byn.items() if len(i) >= MIN_NODE), key=lambda x: -len(x[1]))
    for n, (node, ids) in enumerate(nodes, 1):
        ids.sort(key=lambda k: -age(com[k], now))
        L = [f'# Your commitments under `{node}`: {len(ids)} items\n',
             f'One work block on this workstream. Oldest is {age(com[ids[0]], now):.0f} days.\n']
        for k in ids:
            r = com[k]
            due = f", due {r['due']}" if r.get('due') else ''
            L.append(f'\n### {card(k)} - {age(r, now):.0f}d{due}')
            L.append(safe(r.get('text')))
            src = source_line(r, 'source')
            if src:
                L.append('\n' + src)
        L += ['\n\n---\n', '```bash',
              'python3 .agent/skills/commitment-ledger/scripts/commitment_ledger.py '
              'close <ID> --note "<what you delivered, and where>"',
              'python3 .agent/skills/commitment-ledger/scripts/commitment_ledger.py '
              'drop <ID> --note "<why it is no longer yours to do>"', '```']
        name = f'com_{n:02d}_{node}.md'
        write_both(roots, name, '\n'.join(L) + '\n')
        written.append(name)

    small = sorted(((x, i) for x, i in byn.items() if len(i) < MIN_NODE), key=lambda x: -len(x[1]))
    L = [f'# Your commitments, long tail: {sum(len(i) for _, i in small)} items '
         f'across {len(small)} nodes\n']
    for node, ids in small:
        L.append(f'\n## `{node}` ({len(ids)})')
        for k in sorted(ids, key=lambda k: -age(com[k], now)):
            L.append(f"- {card(k)} {age(com[k], now):.0f}d: {safe(com[k].get('text'))[:150]}")
    write_both(roots, 'com_99_long_tail.md', '\n'.join(L) + '\n')
    written.append('com_99_long_tail.md')

    # The index is generated here, from the same pass that names the files. A
    # hand-written index goes stale the moment a record closes and the owner
    # ranking shifts, and every one of its links then points at a filename that
    # no longer exists.
    for root in roots:
        write_both([root], 'README.md', readme(root, owners, tail, nodes, small, wait, com, now))
    written.append('README.md')

    print(f'wrote {len(written)} batch file(s) to: ' + ', '.join(roots))

def readme(root, owners, tail, nodes, small, wait, com, now):
    def link(name):
        return f'[`{name}`]({os.path.join(root, name).replace(os.sep, "/").replace(" ", "%20")})'

    total_wait = sum(len(i) for _, i in owners) + sum(len(i) for _, i in tail)
    total_com = sum(len(i) for _, i in nodes) + sum(len(i) for _, i in small)
    top6 = sum(len(i) for _, i in owners[:6])
    L = ['# How to clear the ledger, one batch at a time\n',
         f'{total_com} commitments and {total_wait} waiting-on records are open. As one list '
         'that is hopeless, which is why nothing has moved. The records cluster hard, so as '
         'batches it is about ten sittings.\n',
         f'**Six people hold {top6} of the {total_wait} things you are waiting on.** Talk to '
         'those six and the pile drops by a third, without you doing any of the work yourself.\n',
         "Every ticket id is a link. Clicking it opens that record's card: owner, how long it "
         'has been silent, the original thread, the whole note history. That card is how you '
         'read one item. This page is how you decide which ones to open.\n',
         '\n> Regenerate this folder after any batch: `python3 .agent/scripts/make_batches.py`\n',
         '\n---\n', '\n## Who you are waiting on\n',
         'Sorted by how much one conversation clears. Three oldest items each, so you can tell '
         'in ten seconds whether it is worth a call.\n']
    for n, (owner, ids) in enumerate(owners[:6], 1):
        ids = sorted(ids, key=lambda k: -age(wait[k], now))
        L.append(f'\n### {n}. {owner} - {len(ids)} items, oldest {age(wait[ids[0]], now):.0f} days\n')
        for k in ids[:3]:
            L.append(f"- {card(k)} **{age(wait[k], now):.0f}d** {safe(wait[k].get('what'))[:165]}")
        if len(ids) > 3:
            L.append(f'- ...and {len(ids) - 3} more, in {link(f"wait_{n:02d}_{slug(owner)}.md")}')
    L.append('\n### The rest\n')
    for n, (owner, ids) in enumerate(owners[6:], 7):
        oldest = max(age(wait[k], now) for k in ids)
        L.append(f'- **{owner}** {len(ids)} items, oldest {oldest:.0f}d, '
                 f'{link(f"wait_{n:02d}_{slug(owner)}.md")}')
    L.append(f'- **Everyone else**: {sum(len(i) for _, i in tail)} items across {len(tail)} '
             f'people, {link("wait_99_long_tail.md")}')

    L += ['\n---\n', '\n## Your own commitments\n',
          'Grouped by work-tree node, because one work block clears a node. No conversation '
          'makes these go away.\n']
    for n, (node, ids) in enumerate(nodes, 1):
        oldest = max(age(com[k], now) for k in ids)
        extra = ' - these only need you to name the node' if node == 'unfiled' else ''
        L.append(f'- **`{node}`** {len(ids)} items, oldest {oldest:.0f}d, '
                 f'{link(f"com_{n:02d}_{node}.md")}{extra}')
    L.append(f'- **Long tail**: {sum(len(i) for _, i in small)} items across {len(small)} '
             f'nodes, {link("com_99_long_tail.md")}')

    L += ['\n---\n', '\n## What each item can be\n',
          'Open the card, read the original thread, then one of three:\n',
          '\n**They answered.** Close it with what they actually said, not "done".\n',
          '```bash',
          'python3 .agent/skills/waiting-watchdog/scripts/waiting_watchdog.py close WAIT-xxxx '
          '--note "<their answer>"', '```\n',
          '**Nobody needs it now.** Drop it with the reason.\n', '```bash',
          'python3 .agent/skills/waiting-watchdog/scripts/waiting_watchdog.py drop WAIT-xxxx '
          '--note "<why>"', '```\n',
          '**Still open.** Leave it, and say the one line you need back. One message per person '
          'carrying all their items, never one chase per item.\n',
          'Commitments work the same way with `commitment_ledger.py`, plus a third option: if it '
          'is still yours, put a date on it. A commitment with no date is how these reached 70 '
          'days old.\n',
          '\nPast 21 days with no response there is a separate path: deprioritise, final notice, '
          'then discard only against evidence. See `docs/waiting_on_lifecycle.md`.\n']
    return '\n'.join(L) + '\n'

if __name__ == '__main__':
    main()
