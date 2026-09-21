#!/usr/bin/env python3
"""answer_hunt.py - hunt for an answer to a waiting-on record across every source.

Why this exists
---------------
The chase queue's verifier looks in exactly one place: the ask's own Slack
thread, plus the channel it was posted in. That is deliberately narrow, because
a loose check silently buries real breaches. But narrow also means it returns
"no reply" for an answer that arrived anywhere else, and it returns nothing at
all for the 199 records that carry no Slack permalink.

So "no reply" from that verifier is not evidence of no answer. It is evidence of
no answer *in one place*. Discarding a record on that basis throws away real
work. This script exists so a discard decision rests on a wider search.

What it does
------------
For one record it runs up to six independent probes:

STRONG probes. A hit here is attributable to the owner, on this ask.

  1. thread     the ask's own Slack thread, any non-the owner reply after the ask
  2. channel    the ask's channel, an owner message after the ask carrying at
                least two of the record's distinctive terms
  3. dm         the direct message with the owner, same two-term bar

WEAK probes. A hit here is a lead, never a verdict. Keyword co-occurrence is
not an answer: the first version of this script called WAIT-0150 answered on
the word "implementation" appearing in an unrelated Hero Banner message.

  4. search     Slack search across the workspace, requiring two distinct terms
                in the SAME message, after the ask, not written by the owner
  5. meetings   a later MOM carrying every term
  6. decisions  a decision recorded after the ask carrying every term

It never says "unanswered". It reports which probes ran, which were
unreachable, and what each found. The verdict is one of:

  ANSWERED_LIKELY   a STRONG probe hit. Read it, then close the record.
  LEAD              only WEAK hits. Somebody has to read the lead before
                    anything is closed or discarded.
  NO_TRACE          every probe that could run found nothing, and at least
                    three ran.
  UNVERIFIABLE      fewer than three probes could run. Not a conclusion.

Only NO_TRACE may feed a discard decision, and only after an escalation to the
owner has gone out and gone unanswered.

Usage
-----
    python3 .agent/scripts/answer_hunt.py WAIT-0150 WAIT-0320
    python3 .agent/scripts/answer_hunt.py --older-than 21 --limit 40
    python3 .agent/scripts/answer_hunt.py --older-than 21 --json out.json
"""
import argparse
import json
import os
import re
import sys
import time
import glob

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
STATE = os.path.join(BASE_DIR, 'journal', 'state')
MEETINGS = os.path.join(BASE_DIR, 'Clients', 'Work', 'meetings')
OWNER = os.environ.get('OWNER_SLACK_ID', '<SLACK_ID>')
API_PAUSE = 0.35

sys.path.insert(0, os.path.join(BASE_DIR, '.agent', 'skills', 'chase-queue', 'scripts'))
sys.path.insert(0, os.path.join(BASE_DIR, '.agent', 'scripts'))

PERMALINK_RE = re.compile(r'/archives/([A-Z0-9]+)/p(\d{10})(\d{6})')
TICKET_RE = re.compile(r'\b(?:MP|MPS|MSP|MBA|STOR|SCCB|ML)-\d+\b')
# Vocabulary that appears in half the workspace. A match on one of these is
# noise wearing the costume of evidence, which is how the first version of this
# script produced a false ANSWERED verdict.
GENERIC = set('''implementation implement review reviewed revised estimate estimates
sizing status update updates confirm confirmation plan plans planning ticket tickets
product products service services request requested requirement requirements design
designs build building launch release document documents doc docs meeting meetings
session sessions timeline number numbers detail details answer answers question
questions scope scoping owner ownership approval approve approved feedback change
changes version versions deliver delivery delivered integration testing
'''.split())

STOP = set('''the a an and or but for with from into onto that this these those what
which who whom whose when where why how is are was were be been being do does did
done have has had can could will would shall should may might must not no yes if
then than so as at by in on of to up out over under again once here there all any
both each few more most other some such only own same too very just now also his her
their our your its it he she they we you i me him them us confirm confirms confirmed
need needs needed give gives given send sends sent share shares shared plus per via
whether still after before once about against between during without within along
across behind beyond because since until while
'''.split())

def load_items():
    with open(os.path.join(STATE, 'waiting_on.json'), encoding='utf-8') as fh:
        return json.load(fh)['items']

def load_decisions():
    try:
        with open(os.path.join(STATE, 'decisions.json'), encoding='utf-8') as fh:
            return json.load(fh)['items']
    except (OSError, ValueError):
        return {}

def parse_permalink(url):
    m = PERMALINK_RE.search(url or '')
    if not m:
        return None, None
    return m.group(1), f'{m.group(2)}.{m.group(3)}'

def terms_for(rec):
    """Distinctive terms, strongest first.

    Generic product vocabulary is worse than useless here: "implementation",
    "review" and "estimate" appear in half the workspace, so a match on one of
    them is noise that reads like evidence. Ticket keys and hyphenated compounds
    are kept, mid-sentence capitalised words are kept, everything else must
    survive the stoplist and be long.
    """
    text = ' '.join(str(rec.get(k) or '') for k in ('what', 'notes'))
    out, seen = [], set()

    def push(w):
        lw = w.lower().strip("'s")
        if lw in STOP or lw in GENERIC or lw in seen or len(lw) < 5:
            return
        seen.add(lw)
        out.append(w)

    for t in TICKET_RE.findall(text):
        push(t)
    for w in re.findall(r"\b[a-z]+-[a-z]+(?:-[a-z]+)?\b", text):   # multi-currency
        push(w)
    for w in re.findall(r"(?<![.!?]\s)(?<!^)\b[A-Z][a-zA-Z]{4,}\b", text):  # proper nouns
        push(w)
    rest = re.findall(r"[A-Za-z][A-Za-z_'-]{6,}", text)
    rest.sort(key=len, reverse=True)
    for w in rest:
        push(w)
    return out[:4]

def term_hits(text, terms):
    """How many distinct terms appear in one piece of text."""
    low = (text or '').lower()
    return sum(1 for t in terms if t.lower().strip("'s") in low)

# ------------------------------------------------------------------ probes --

def probe_thread(cq, token, rec):
    chan, ts = parse_permalink((rec.get('source') or {}).get('permalink'))
    if not (chan and ts):
        return None, 'no slack anchor'
    r = cq.slack_api('conversations.replies', {'channel': chan, 'ts': ts, 'limit': 100}, token)
    if not r.get('ok'):
        return None, 'thread unreadable: ' + str(r.get('error'))
    for m in r.get('messages', [])[1:]:
        if m.get('user') and m.get('user') != OWNER:
            return True, f"reply in thread at {m.get('ts')}: {(m.get('text') or '')[:140]}"
    return False, 'thread read, no non-the owner reply'

def probe_channel(cq, token, rec, uid, terms):
    chan, ts = parse_permalink((rec.get('source') or {}).get('permalink'))
    if not (chan and ts):
        return None, 'no slack anchor'
    if not uid:
        return None, 'owner id not resolved'
    if len(terms) < 2:
        return None, 'too few distinctive terms to attribute a channel message'
    r = cq.slack_api('conversations.history', {'channel': chan, 'oldest': ts, 'limit': 100}, token)
    if not r.get('ok'):
        return None, 'channel unreadable: ' + str(r.get('error'))
    best = 0
    for m in r.get('messages', []):
        if m.get('user') != uid or m.get('ts') == ts:
            continue
        n = term_hits(m.get('text'), terms)
        best = max(best, n)
        if n >= 2:
            return True, (f"owner posted in channel at {m.get('ts')} matching {n} terms: "
                          f"{(m.get('text') or '')[:140]}")
    if best:
        return False, f'owner posted since the ask but matched only {best} term, not on topic'
    return False, 'channel read, owner silent since the ask'

def probe_search(cq, token, rec, terms):
    """WEAK. Requires two distinct terms in the SAME message, after the ask,
    not written by the owner. One term matching is the noise that made the first
    version of this script wrong."""
    if len(terms) < 2:
        return None, 'need two distinctive terms to search safely'
    since = float(rec.get('since') or 0)
    hits, scanned = [], 0
    for t in terms[:2]:
        r = cq.slack_api('search.messages', {'query': t, 'count': 20}, token)
        time.sleep(API_PAUSE)
        if not r.get('ok'):
            return None, 'search unavailable: ' + str(r.get('error'))
        for m in (r.get('messages') or {}).get('matches', []):
            scanned += 1
            try:
                mts = float(m.get('ts', 0))
            except (TypeError, ValueError):
                continue
            if mts <= since or m.get('user') == OWNER:
                continue
            if term_hits(m.get('text'), terms) < 2:
                continue
            hits.append(f"@{m.get('ts')} in #{(m.get('channel') or {}).get('name', '?')}: "
                        f"{(m.get('text') or '')[:120]}")
    if hits:
        return True, ' | '.join(hits[:2])
    return False, f'searched {terms[:2]} over {scanned} messages, none matched two terms after the ask'

def probe_dm(cq, token, rec, uid, terms):
    if not uid:
        return None, 'owner id not resolved'
    if len(terms) < 2:
        return None, 'too few distinctive terms to attribute a DM'
    r = cq.slack_api('users.conversations',
                     {'types': 'im', 'user': uid, 'limit': 5}, token)
    if not r.get('ok'):
        return None, 'dm not listable: ' + str(r.get('error'))
    chans = [c.get('id') for c in r.get('channels', []) if c.get('user') == uid]
    if not chans:
        return None, 'no direct message with this person'
    since = rec.get('since') or 0
    for cid in chans[:1]:
        h = cq.slack_api('conversations.history',
                         {'channel': cid, 'oldest': f'{since:.6f}', 'limit': 60}, token)
        if not h.get('ok'):
            return None, 'dm unreadable: ' + str(h.get('error'))
        for m in h.get('messages', []):
            if m.get('user') != uid:
                continue
            if term_hits(m.get('text'), terms) >= 2:
                return True, f"owner wrote in DM at {m.get('ts')}: {(m.get('text') or '')[:140]}"
    return False, 'dm read, nothing from the owner on this topic since the ask'

def probe_meetings(rec, terms):
    """WEAK. Requires every term in one later MOM."""
    if len(terms) < 2 or not os.path.isdir(MEETINGS):
        return None, 'too few terms, or no meetings folder'
    since = rec.get('since') or 0
    hits = []
    for path in glob.glob(os.path.join(MEETINGS, '*.md')):
        if os.path.getmtime(path) <= since:
            continue
        try:
            with open(path, encoding='utf-8', errors='ignore') as fh:
                body = fh.read()
        except OSError:
            continue
        if term_hits(body, terms) == len(terms):
            hits.append(os.path.basename(path))
    if hits:
        return True, 'later MOM carries every term: ' + ', '.join(hits[:3])
    return False, 'no later MOM carries all of these terms'

def probe_decisions(rec, terms, decisions):
    """WEAK. Requires every term in one decision taken after the ask."""
    if len(terms) < 2:
        return None, 'too few terms'
    since = rec.get('since') or 0
    for did, d in decisions.items():
        if (d.get('status') or 'open') == 'open':
            continue
        at = d.get('decided_at') or d.get('updated_at') or 0
        if at and at <= since:
            continue
        blob = ' '.join(str(d.get(k) or '') for k in ('title', 'decision', 'notes'))
        if term_hits(blob, terms) == len(terms):
            return True, f"{did} decided after the ask: {(d.get('decision') or d.get('title') or '')[:120]}"
    return False, 'no later decision carries all of these terms'

# ------------------------------------------------------------------- hunt ---

STRONG = ('thread', 'channel', 'dm')
WEAK = ('search', 'meetings', 'decisions')

def hunt(cq, token, names, decisions, rec):
    uid = cq.resolve_user_id(rec.get('owner') or '', names)
    terms = terms_for(rec)
    probes = {
        'thread': probe_thread(cq, token, rec),
        'channel': probe_channel(cq, token, rec, uid, terms),
        'dm': probe_dm(cq, token, rec, uid, terms),
        'search': probe_search(cq, token, rec, terms),
        'meetings': probe_meetings(rec, terms),
        'decisions': probe_decisions(rec, terms, decisions),
    }
    ran = [k for k, (v, _) in probes.items() if v is not None]
    hits = [k for k, (v, _) in probes.items() if v is True]
    strong = [k for k in hits if k in STRONG]
    weak = [k for k in hits if k in WEAK]

    if strong:
        verdict = 'ANSWERED_LIKELY'
    elif weak:
        verdict = 'LEAD'
    elif len(ran) >= 3:
        verdict = 'NO_TRACE'
    else:
        verdict = 'UNVERIFIABLE'
    return {
        'id': rec['id'], 'owner': rec.get('owner'), 'terms': terms,
        'age_days': round((time.time() - (rec.get('since') or time.time())) / 86400, 1),
        'verdict': verdict, 'probes_run': ran, 'strong': strong, 'weak': weak,
        'detail': {k: v[1] for k, v in probes.items()},
    }

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('ids', nargs='*', help='WAIT ids; omit to use --older-than')
    ap.add_argument('--older-than', type=float, default=None,
                    help='hunt every open record older than N days')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--json', help='write the full result to this path')
    args = ap.parse_args()

    import chase_queue as cq
    os.chdir(BASE_DIR)
    token = cq.slack_token()
    names = cq.load_user_names()
    decisions = load_decisions()
    items = load_items()

    if args.ids:
        recs = [items[i] for i in args.ids if i in items]
    else:
        cutoff = time.time() - (args.older_than or 21) * 86400
        recs = [r for r in items.values()
                if (r.get('status') or 'open') in ('open', 'breached')
                and (r.get('since') or 0) < cutoff]
        recs.sort(key=lambda r: r.get('since') or 0)
    if args.limit:
        recs = recs[:args.limit]

    print(f'hunting {len(recs)} record(s) across six sources')
    out = []
    for i, rec in enumerate(recs, 1):
        res = hunt(cq, token, names, decisions, rec)
        out.append(res)
        marks = ','.join(res['strong']) or ('weak:' + ','.join(res['weak']) if res['weak'] else '-')
        print(f"  {res['id']:10s} {res['age_days']:5.0f}d  {res['verdict']:16s} "
              f"probes {len(res['probes_run'])}/6  {marks}")
        if i % 20 == 0:
            print(f'  ... {i}/{len(recs)}', flush=True)

    tally = {}
    for r in out:
        tally[r['verdict']] = tally.get(r['verdict'], 0) + 1
    print('\n' + json.dumps(tally, indent=1))
    if args.json:
        with open(args.json, 'w', encoding='utf-8') as fh:
            json.dump(out, fh, indent=1)
        print('wrote', args.json)

if __name__ == '__main__':
    main()
