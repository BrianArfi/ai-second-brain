#!/usr/bin/env python3
"""Find decisions that contradict each other, or that were decided elsewhere and
never closed.

Why this exists
---------------
On 22 Aug 2026 the morning briefing proposed five priorities and three of them
stood on decision records that were already dead: DEC-0173 had been decided on
19 Aug and its own source document said "Decided", DEC-0179 had been replaced by
DEC-0196 the next day, and DEC-0182 had been resolved in engineering without any
product call at all. Nothing was corrupt. The ledger simply kept saying "open"
because closing a record is a separate act from making the decision, and nobody
performed it.

memory feedback-check-for-superseding-decisions says repo documents go stale
silently. This is the mirror case: the ledger goes stale too. So the check runs
over the ledger against itself and against the local mirrors of its own sources.

Idea borrowed from silverstein/minutes (`minutes consistency`), which flags
contradictory decisions across a meeting corpus. This version is deterministic
by default: it reports candidate pairs and lets the owner judge, because an LLM
verdict on whether two decisions conflict is exactly the kind of plausible
wrong answer that started the problem. `--llm` adds a judged verdict on the
candidates it already found, and never widens the candidate set.

Checks
------
  supersede-not-closed  superseded_by is set, status is still open
  decided-in-source     open record, and the local mirror of its source says decided
  overtaken             open record, and a NEWER decided record shares its node
                        and enough of its title
  contradiction         two decided records on one node, close in wording,
                        opposite in outcome
  stale-open            open past its deadline, with no note since

Usage:
  decision_consistency.py check [--json] [--llm] [--strict]
  decision_consistency.py check --id DEC-0173
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
DECISIONS_PATH = os.path.join(BASE_DIR, 'journal', 'state', 'decisions.json')
AGY_BRIDGE = os.path.join(BASE_DIR, '.agent', 'skills', 'agy-bridge', 'scripts', 'agy_bridge.py')
DASHBOARD_URL = 'http://localhost:3737/#find/'

TITLE_STOPWORDS = {
    'the', 'a', 'an', 'and', 'or', 'of', 'for', 'to', 'in', 'on', 'at', 'by',
    'with', 'is', 'are', 'be', 'do', 'we', 'it', 'that', 'this', 'from', 'as',
    'decision', 'decide', 'whether', 'should', 'work', 'new', 'use', 'via',
}

# Word pairs that secondary an outcome. A contradiction is only reported when two
# records agree on subject and disagree here, which keeps "we will ship X" and
# "we will not ship X" apart without needing a model to read them.
OPPOSITES = [
    ({'yes', 'approve', 'approved', 'adopt', 'adopted', 'proceed', 'ship',
      'include', 'included', 'build', 'keep', 'enable', 'enabled', 'in'},
     {'no', 'reject', 'rejected', 'drop', 'dropped', 'defer', 'deferred',
      'exclude', 'excluded', 'skip', 'remove', 'disable', 'disabled', 'out'}),
    ({'phase1', 'mvp', 'now', 'immediately'},
     {'post-mvp', 'postmvp', 'later', 'backlog', 'phase2'}),
]

DECIDED_IN_DOC_RE = re.compile(
    r'^\s*(?:[|>*\-\s]*)(?:status|decision)\s*[:|]\s*\**\s*(decided|closed|approved|agreed)\b',
    re.IGNORECASE | re.MULTILINE)

# ---------------------------------------------------------------- utilities --

def load_decisions():
    with open(DECISIONS_PATH, encoding='utf-8') as f:
        return json.load(f).get('items', {})

def tokens(text):
    words = re.findall(r"[a-z0-9']+", (text or '').lower())
    return {w for w in words if w not in TITLE_STOPWORDS and len(w) > 2}

def overlap(a, b):
    """Jaccard on the significant words of two titles. Cheap, and good enough to
    pick candidates a human then reads: this number never decides anything on
    its own."""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)

def when(rec, field):
    v = rec.get(field)
    if isinstance(v, (int, float)):
        return datetime.datetime.utcfromtimestamp(v).date()
    if isinstance(v, str):
        try:
            return datetime.date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None

def sort_date(rec):
    return (when(rec, 'decided_at') or when(rec, 'updated_at')
            or when(rec, 'created_at') or datetime.date.min)

def last_note_date(rec):
    dates = re.findall(r'(\d{4}-\d{2}-\d{2})', rec.get('notes') or '')
    return max((datetime.date.fromisoformat(d) for d in dates), default=None)

GDOC_ID_RE = re.compile(r'/document/d/([A-Za-z0-9_-]{20,})')
_MIRROR_INDEX = None

def mirror_index():
    """doc id -> repo files that cite it. Every published Work doc keeps a local
    mirror under Clients/, and the mirror carries the Drive URL in its header, so
    the repo can be searched for a Doc without touching the network. Built once."""
    global _MIRROR_INDEX
    if _MIRROR_INDEX is not None:
        return _MIRROR_INDEX
    idx = {}
    for root, dirs, files in os.walk(os.path.join(BASE_DIR, 'Clients')):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for fn in files:
            if not fn.endswith('.md'):
                continue
            path = os.path.join(root, fn)
            try:
                with open(path, encoding='utf-8', errors='replace') as f:
                    head = f.read(20000)
            except OSError:
                continue
            for doc_id in set(GDOC_ID_RE.findall(head)):
                idx.setdefault(doc_id, []).append(path)
    _MIRROR_INDEX = idx
    return idx

def local_mirrors(rec):
    """Repo files that mirror this decision's sources. A Google Doc cannot be
    opened from here without the network, so the check reads the repo's own
    mirror of it instead. Sources with neither a path nor a Doc id contribute
    nothing: an offline check that reports less is better than one that guesses."""
    out = []
    for s in (rec.get('sources') or []):
        p = s.get('path') or (s.get('url') if str(s.get('url', '')).startswith('/') else None)
        if p:
            full = p if os.path.isabs(p) else os.path.join(BASE_DIR, p)
            if os.path.exists(full) and full.endswith(('.md', '.txt')):
                out.append(full)
            continue
        m = GDOC_ID_RE.search(str(s.get('url') or ''))
        if m:
            out.extend(mirror_index().get(m.group(1), []))
    return list(dict.fromkeys(out))

# ------------------------------------------------------------------- checks --

def check_supersede_not_closed(items):
    for did, rec in items.items():
        if rec.get('superseded_by') and rec.get('status') == 'open':
            yield {
                'check': 'supersede-not-closed', 'severity': 'high', 'id': did,
                'title': rec.get('title'),
                'detail': f"points at {rec['superseded_by']} as its replacement, "
                          f"and is still open",
                'fix': f"decision_log.py supersede {did} --by {rec['superseded_by']}",
            }

def check_decided_in_source(items):
    for did, rec in items.items():
        if rec.get('status') != 'open':
            continue
        for path in local_mirrors(rec):
            with open(path, encoding='utf-8', errors='replace') as f:
                m = DECIDED_IN_DOC_RE.search(f.read())
            if m:
                yield {
                    'check': 'decided-in-source', 'severity': 'high', 'id': did,
                    'title': rec.get('title'),
                    'detail': f"open here, but {os.path.relpath(path, BASE_DIR)} "
                              f'reads "{m.group(0).strip()}"',
                    'fix': f'decision_log.py decide {did} --decision "..."',
                }
                break

def check_overtaken(items, threshold=0.45):
    decided = [(d, r) for d, r in items.items() if r.get('status') == 'decided']
    for did, rec in items.items():
        if rec.get('status') != 'open':
            continue
        opened = sort_date(rec)
        best = None
        for od, other in decided:
            if od == did:
                continue
            if rec.get('node') and other.get('node') and rec['node'] != other['node']:
                continue
            if sort_date(other) < opened:
                continue
            score = overlap(rec.get('title'), other.get('title'))
            if score >= threshold and (best is None or score > best[0]):
                best = (score, od, other)
        if best:
            score, od, other = best
            yield {
                'check': 'overtaken', 'severity': 'medium', 'id': did,
                'title': rec.get('title'),
                'detail': f"{od} ({sort_date(other)}) covers the same ground and is "
                          f"decided: \"{other.get('title')}\" (title overlap "
                          f"{score:.0%}, node {other.get('node') or '-'})",
                'fix': f"decision_log.py supersede {did} --by {od}   # if it is the same call",
                'pair': od,
            }

def check_contradiction(items, threshold=0.5):
    decided = [(d, r) for d, r in items.items() if r.get('status') == 'decided']
    seen = set()
    for i, (da, ra) in enumerate(decided):
        for db, rb in decided[i + 1:]:
            if ra.get('node') != rb.get('node') or not ra.get('node'):
                continue
            key = tuple(sorted((da, db)))
            if key in seen:
                continue
            text_a = f"{ra.get('title')} {ra.get('decision') or ''}"
            text_b = f"{rb.get('title')} {rb.get('decision') or ''}"
            if overlap(ra.get('title'), rb.get('title')) < threshold:
                continue
            ta, tb = tokens(text_a), tokens(text_b)
            secondaryped = any((ta & pos and tb & neg) or (ta & neg and tb & pos)
                          for pos, neg in OPPOSITES)
            if not secondaryped:
                continue
            seen.add(key)
            yield {
                'check': 'contradiction', 'severity': 'high', 'id': da,
                'title': ra.get('title'),
                'detail': f"reads as the opposite of {db} on node {ra.get('node')}: "
                          f"\"{rb.get('title')}\"",
                'fix': f"open both, then supersede whichever is dead",
                'pair': db,
            }

def check_stale_open(items, days=21):
    today = datetime.date.today()
    for did, rec in items.items():
        if rec.get('status') != 'open':
            continue
        deadline = when(rec, 'deadline')
        if not deadline or deadline >= today:
            continue
        touched = last_note_date(rec) or when(rec, 'updated_at') or sort_date(rec)
        idle = (today - touched).days if touched else None
        if idle is None or idle < days:
            continue
        yield {
            'check': 'stale-open', 'severity': 'low', 'id': did,
            'title': rec.get('title'),
            'detail': f"deadline {deadline} passed, nothing written on it for {idle} days",
            'fix': f"decision_log.py decide {did} ... or drop it",
        }

CHECKS = [check_supersede_not_closed, check_decided_in_source,
          check_overtaken, check_contradiction, check_stale_open]

# ---------------------------------------------------------------------- llm --

def judge(findings):
    """Ask a model whether each candidate pair really conflicts. Advisory only:
    it annotates findings, it never adds or removes one."""
    if not os.path.exists(AGY_BRIDGE):
        print('agy-bridge not found, skipping --llm', file=sys.stderr)
        return findings
    items = load_decisions()
    for f in findings:
        if 'pair' not in f:
            continue
        a, b = items.get(f['id'], {}), items.get(f['pair'], {})
        prompt = (
            'Two decision records from one product ledger are below. Answer in one '
            'line: SAME if they are the same call, CONFLICT if they contradict, '
            'SEPARATE if they are unrelated. Then one sentence of reason.\n\n'
            f"A ({f['id']}): {a.get('title')} :: {a.get('decision')}\n"
            f"B ({f['pair']}): {b.get('title')} :: {b.get('decision')}\n")
        try:
            r = subprocess.run([sys.executable, AGY_BRIDGE, '--task', 'critic',
                                '--prompt', prompt],
                               capture_output=True, text=True, timeout=180)
            if r.returncode == 0:
                f['verdict'] = r.stdout.strip().splitlines()[0][:200]
        except Exception as e:                                # noqa: BLE001
            print(f"llm judge failed on {f['id']}: {e}", file=sys.stderr)
    return findings

# ------------------------------------------------------------------ command --

SEV_ORDER = {'high': 0, 'medium': 1, 'low': 2}

def cmd_check(args):
    items = load_decisions()
    if args.id:
        keep = {args.id}
        findings = [f for c in CHECKS for f in c(items)
                    if f['id'] in keep or f.get('pair') in keep]
    else:
        findings = [f for c in CHECKS for f in c(items)]
    findings.sort(key=lambda f: (SEV_ORDER.get(f['severity'], 9), f['id']))

    if args.llm:
        findings = judge(findings)

    if args.json:
        print(json.dumps(findings, indent=1, ensure_ascii=False))
        return 1 if (findings and args.strict) else 0

    if not findings:
        print(f'{len(items)} decisions, nothing inconsistent')
        return 0
    high = sum(1 for f in findings if f['severity'] == 'high')
    print(f"{len(findings)} finding(s) across {len(items)} decisions, {high} high\n")
    for f in findings:
        print(f"[{f['severity']}] {f['check']}  {f['id']}  {DASHBOARD_URL}{f['id']}")
        print(f"  {f['title']}")
        print(f"  {f['detail']}")
        if f.get('verdict'):
            print(f"  verdict: {f['verdict']}")
        print(f"  fix: {f['fix']}\n")
    # Exit 0 unless asked to gate. The daily runner labels any non-zero step as
    # FAILED, which would read as "the check broke" instead of "the check found
    # something". Use --strict in a hook or a CI job that must stop.
    return 1 if args.strict else 0

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd')
    c = sub.add_parser('check')
    c.add_argument('--id', help='only findings touching this decision')
    c.add_argument('--json', action='store_true')
    c.add_argument('--llm', action='store_true',
                   help='annotate candidate pairs with a model verdict')
    c.add_argument('--strict', action='store_true',
                   help='exit 1 when anything is found, for gating')
    args = ap.parse_args()
    if args.cmd != 'check':
        ap.print_help()
        return 1
    return cmd_check(args)

if __name__ == '__main__':
    sys.exit(main())
