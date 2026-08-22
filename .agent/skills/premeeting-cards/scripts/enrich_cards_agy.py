#!/usr/bin/env python3
"""Enrich pre-meeting cards into walk-in briefs using agy-bridge (GLM), not Claude.

Replaces enrich_meeting_cards.workflow.js (retired 17 Jul 2026). the owner's standing
rule: card enrichment is bulk draft work over already-known context, so it belongs
on a cheap non-Claude model per the agy-bridge routing table. The old Workflow
fan-out spent Claude subagent tokens per meeting every working day, and it needed
per-run Workflow opt-in - so on non-interactive runs it silently got skipped and
left the cards mentah.

Division of labour, and it matters:
  - PYTHON gathers. It has Slack, the repo, and the ledgers. Deterministic.
  - GLM writes. It has no tools and no ground truth beyond this prompt.

That split is not stylistic. The live-status check (cancelled / rescheduled / on)
is the 14 Jul ExamplePartner miss, where a meeting was already cancelled in Slack and the
card never said so. A model with no Slack access cannot check that, and asking it
to would just invite a confident guess. So status is resolved here, in code, and
handed to GLM as a fact it must not contradict.

Usage:
  enrich_cards_agy.py --date 2026-07-17            # enrich all substantive cards
  enrich_cards_agy.py --date 2026-07-17 --dry-run  # print briefs, write nothing
  enrich_cards_agy.py --date 2026-07-17 --force-glm  # DEPRECATED no-op (z.ai retired 2026-07-27)
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
STATE_PATH = os.path.join(BASE_DIR, 'journal', 'state', 'premeeting.json')
AGY_BRIDGE = os.path.join(BASE_DIR, '.agent', 'skills', 'agy-bridge', 'run.py')
SLACK_CLIENT = os.path.join(BASE_DIR, '.agent', 'skills', 'slack-connector', 'scripts', 'slack_client.py')
MASTER_LINKS = os.path.join(BASE_DIR, 'Clients', 'Work', 'master_links.md')

# Blocks that are not meetings. Enriching these burns tokens to say nothing.
SKIP_RE = re.compile(
    r'^\s*(prayer|home|focus time|lunch|break|ooo|out of office|holiday)\s*$',
    re.IGNORECASE)

# A card the generator produced but that carries no joins at all has nothing to
# brief from; GLM would pad it with plausible filler. Cheaper and safer to leave
# the mechanical card alone.
def _is_substantive(card):
    title = (card.get('title') or '').strip()
    if SKIP_RE.match(title):
        return False
    if title.startswith(('🔧', '✅')) and not card.get('attendee_slugs'):
        return False   # self-assigned focus block, not a room to walk into
    signal = (card.get('n_decisions', 0) + card.get('n_pings', 0)
              + card.get('n_you_owe', 0) + card.get('n_they_owe', 0)
              + card.get('n_tickets', 0))
    return bool(card.get('attendee_slugs')) or signal > 0

# Slack search is OR-matching, so a bare "<title> cancel" query returns anything
# containing any of those words. At Work that is catastrophic: "cancel" is
# routine business vocabulary (order cancellations, an entire
# #exampleprogram-store-cancellations channel), so a keyword match flagged all 5 cards
# "possibly cancelled" on the first run. A flag that fires on everything is worse
# than no flag - it teaches the owner to ignore the one time it is real.
#
# So a hit must clear THREE independent bars: an explicit phrase about calling a
# MEETING off, a distinctive term from this meeting's title, and recency.
# "cancel" must attach to a MEETING noun. Matching "cancel" + any following word
# lets "70+ order cancelled this week" through on "this", which is precisely the
# business-noise case this check exists to reject.
_MEETING_NOUN = (r"(?:call|meeting|standup|stand-up|scrum|sync|session|workshop"
                 r"|demo|invite|catchup|catch-up|1:1)")
_DET = r"(?:today'?s?\s+|tomorrow'?s?\s+|the\s+|this\s+|our\s+)?"
_CANCEL_RE = re.compile(
    "|".join([
        rf"cancel\w*\s+{_DET}{_MEETING_NOUN}",           # "cancelling today's call"
        rf"{_MEETING_NOUN}\s+(?:is\s+|has\s+been\s+)?cancel\w*",  # "the meeting is cancelled"
        rf"skip(?:ping)?\s+{_DET}{_MEETING_NOUN}",       # "skip today's scrum"
        r"no\s+(?:standup|scrum|call|meeting|sync)\b",   # "no standup today"
        r"call(?:ing)?\s+it\s+off\b",
        r"won'?t\s+(?:be\s+)?(?:able\s+to\s+)?(?:join|make\s+it)\b",
        r"let'?s\s+skip\b",
    ]),
    re.IGNORECASE)
_RESCHED_RE = re.compile(
    r"(reschedul(e|ing|ed)"
    r"|mov(e|ing|ed)\s+(it\s+|the\s+|this\s+|today'?s\s+)?(to|for)\b"
    r"|push(ing|ed)?\s+(it\s+)?(to|back)\b"
    r"|shift(ing|ed)?\s+(it\s+)?to\b"
    r"|new\s+time\b)",
    re.IGNORECASE)

_TITLE_STOP = {'daily', 'weekly', 'scrum', 'standup', 'meeting', 'call', 'sync',
               'kickoff', 'demo', 'work', 'the', 'and', 'for', 'with', 'time'}

_MSG_RE = re.compile(r'^\[(\d+\.\d+)\]\s+\S+\s+in\s+(\S+)[^:]*:\s*(.*)$')

def slack_search(query, limit=8):
    """Best-effort Slack search. Returns parsed (ts, channel, text) tuples.

    Returns [] on ANY failure: a missing status signal must degrade to 'unknown',
    never to a fabricated 'on' or a false 'cancelled'.
    """
    try:
        out = subprocess.run(
            [sys.executable, SLACK_CLIENT, '--action', 'search',
             '--query', query, '--limit', str(limit)],
            capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            return []
        rows = []
        for line in out.stdout.splitlines():
            m = _MSG_RE.match(line.strip())
            if m:
                rows.append((float(m.group(1)), m.group(2), m.group(3)))
        return rows
    except Exception:
        return []

def _title_terms(title):
    """Distinctive words from a meeting title, generic scheduling nouns removed.

    'Marketplace - Daily Scrum' -> {'marketplace'}. Without dropping the generic
    words, 'no standup today' in an unrelated team's channel would match every
    standup on the calendar.

    Alphanumeric and >=3 chars, because Work's distinctive terms are short
    acronyms with digits: a [A-Za-z]{4,} pattern silently yields NOTHING for
    'B2C + SP + PIM | Standup', leaving that meeting permanently unresolvable.
    """
    words = re.findall(r'[A-Za-z0-9]{3,}', title or '')
    return {w.lower() for w in words if w.lower() not in _TITLE_STOP}

GCAL = os.path.join(BASE_DIR, '.agent', 'skills', 'google-calendar-connector',
                    'gcal_manager.py')
OWNER_EMAIL = os.environ.get('WORK_OWNER_EMAIL', 'you@yourcompany.com').lower()
_CAL_EVENTS_CACHE = {}

def _calendar_events(date_str):
    """Today's Work events, once per process. [] on any failure, never a guess."""
    if date_str in _CAL_EVENTS_CACHE:
        return _CAL_EVENTS_CACHE[date_str]
    events = []
    try:
        out = subprocess.run(
            [sys.executable, GCAL, 'list', '--profile', 'work',
             '--days-back', '0', '--days-forward', '1', '--json'],
            capture_output=True, text=True, timeout=90)
        if out.returncode == 0:
            m = re.search(r'\[.*\]', out.stdout, re.S)
            if m:
                events = json.loads(m.group(0))
    except Exception:
        events = []
    _CAL_EVENTS_CACHE[date_str] = events
    return events

def calendar_status(card, date_str):
    """Authoritative status from the calendar itself, not from Slack prose.

    Two signals Slack search structurally cannot see:
      - event status == 'cancelled', the organiser killed it.
      - an attendee whose responseStatus is 'declined'. On 28 Jul Teammate
        Chennupati declined the 18:15 ABC-123 scoping by email while the card
        still read 'unknown', and he was the person the session depended on.

    Returns (status, evidence) or (None, []) when the calendar says nothing,
    in which case the caller falls back to the Slack heuristic.
    """
    title = (card.get('title') or '').strip().lower()
    want_time = (card.get('time_wib') or '').strip()
    if not title:
        return None, []
    for ev in _calendar_events(date_str):
        summary = (ev.get('summary') or '').strip().lower()
        if not summary or summary[:40] != title[:40]:
            continue
        start = ev.get('start') or ''
        # The fetch spans two days, and recurring meetings repeat at the same time
        # with the same title, so the date has to match or tomorrow's copy wins.
        if date_str and not start.startswith(date_str):
            continue
        if want_time and 'T' in start and start.split('T')[1][:5] != want_time:
            continue
        if (ev.get('status') or '').lower() == 'cancelled':
            return 'CANCELLED on the calendar', [
                'calendar event status = cancelled for "{}"'.format(ev.get('summary'))]
        # the owner declining is not a warning about the room, it is his own choice.
        declined = [a for a in (ev.get('attendees') or [])
                    if (a.get('responseStatus') or '').lower() == 'declined'
                    and OWNER_EMAIL not in (a.get('email') or '').lower()]
        if declined:
            who = ', '.join((a.get('displayName') or a.get('email') or '?')
                            for a in declined)
            return 'on, but attendee declined', [
                'calendar RSVP: {} declined "{}"'.format(who, ev.get('summary'))]
        return None, []
    return None, []

def live_status(card, window_h=18):
    """Scripted cancelled / rescheduled / on check. Never delegated to the model.

    Returns (status, evidence). 'unknown' is an honest answer and is surfaced as
    such; the model is explicitly told it may not upgrade it.

    This is now the FALLBACK. `calendar_status()` runs first and is authoritative:
    since 28 Jul 2026 it reads the event's own status plus attendee responseStatus,
    which covers organiser cancellations and declines (Teammate declining the 18:15
    ABC-123 scoping was invisible to this function and obvious to that one).

    REMAINING LIMITATION, do not mistake 'unknown' for 'on'. Reached only when the
    calendar is silent. It catches a cancellation only when the message names the
    meeting or lands in a channel whose name does. It still MISSES a key attendee
    writing "I won't be able to join today" in a DM while leaving the invite
    accepted, which is how YourManager cancelled the 16 Jul Weekly PMO. 'unknown' means
    unchecked, and a human still has to eyeball the day.
    """
    title = (card.get('title') or '').strip()
    terms = _title_terms(title)
    if not terms:
        return 'unknown', []

    cutoff = time.time() - window_h * 3600
    rows = []
    for term in sorted(terms)[:2]:
        for probe in ('cancel', 'reschedule', 'skip'):
            rows += slack_search(f'{term} {probe}', limit=10)

    cancelled, resched = [], []
    for ts, chan, text in rows:
        if ts < cutoff:
            continue                       # stale: last month's cancellation is not today's
        low = text.lower()
        chan_low = (chan or '').lower()
        # Tie the message to THIS meeting via the text OR the channel it landed
        # in. Channel matters because people cancel with "no standup today" and
        # never name the meeting; the room is the only context.
        if not (any(t in low for t in terms) or any(t in chan_low for t in terms)):
            continue
        if _CANCEL_RE.search(text):
            cancelled.append(f'[{chan}] {text[:180]}')
        elif _RESCHED_RE.search(text):
            resched.append(f'[{chan}] {text[:180]}')

    if cancelled:
        return 'possibly cancelled', cancelled[:3]
    if resched:
        return 'possibly rescheduled', resched[:3]
    return 'unknown', []

def hunt_docs(card, card_text):
    """Find real, existing docs for this meeting. Only verified paths are passed
    to GLM, because a model handed a topic will otherwise invent a plausible
    filename and the card ships a dead link."""
    found = []
    title = (card.get('title') or '')
    terms = [w for w in re.findall(r'[A-Za-z]{4,}', title)
             if w.lower() not in {'daily', 'weekly', 'scrum', 'standup', 'meeting',
                                  'call', 'sync', 'kickoff', 'demo', 'work'}]
    for term in terms[:3]:
        try:
            out = subprocess.run(
                ['grep', '-ril', '--include=*.md', term,
                 os.path.join(BASE_DIR, 'Clients', 'Work')],
                capture_output=True, text=True, timeout=30)
            for p in out.stdout.splitlines()[:4]:
                rel = os.path.relpath(p, BASE_DIR)
                if rel not in found and '/meetings/' not in rel:
                    found.append(rel)
        except Exception:
            pass
    # Links the mechanical card already resolved are trustworthy; keep them.
    for m in re.finditer(r'\((https?://[^\s)]+)\)', card_text):
        if m.group(1) not in found:
            found.append(m.group(1))
    return found[:10]

def build_prompt(card, card_text, status, status_evidence, docs):
    doc_block = '\n'.join(f'- {d}' for d in docs) or '- (none found)'
    ev_block = '\n'.join(f'- {e[:200]}' for e in status_evidence) or '- (no signal found)'
    return f"""You are writing a pre-meeting brief for the owner, a product leader at Work (Saudi Arabia).
He walks into this meeting and reads ONLY this card. Write what he needs to drive the room.

MEETING: {card.get('title')} at {card.get('time_wib')} WIB
LIVE STATUS (resolved from Slack, treat as fact): {status}
STATUS EVIDENCE:
{ev_block}

VERIFIED DOCS AND LINKS (these exist; do NOT invent others):
{doc_block}

MECHANICAL CARD (the joined ledger data - this is your ground truth):
{card_text}

Write these sections in this exact order, in English:

## 🎯 Goal
One sentence: what the owner must walk out having achieved.

## 📌 What this is
Two or three sentences of context. Why this meeting matters now.

## ✅ Drive in the room
The concrete things the owner should push. Weight by what HE decides or unblocks, not
by what is most recent. One per line, each starting with a verb.

## ⚠️ Watch
Risks, traps, or things likely to go sideways. One per line. If the live status is
not "on", make that the FIRST line.

## 🔗 Sources
Only the verified docs and links listed above, as markdown links. If none, write "None found".

## 🧾 Open items
The commitments and waiting-on items from the mechanical card that are live for
this meeting, each keeping its `COM-xxxx` / `WAIT-xxxx` id.

HARD RULES:
- Never use an em-dash. Use a comma, a full stop, or rephrase.
- Never invent a document, URL, ticket id, person, or decision. If the mechanical
  card does not support a claim, leave it out.
- Do not upgrade the live status. If it says unknown, say unknown.
- No preamble and no sign-off. Start at "## 🎯 Goal".
"""

def call_agy(prompt, force_glm=False):
    """Returns (text, backend_note) or (None, reason). Fails closed: on any error
    the caller keeps the mechanical card rather than shipping a half-written one."""
    tmp = os.path.join(tempfile.gettempdir(), f'pmcard_{os.getpid()}_{int(time.time()*1000)}.txt')
    try:
        with open(tmp, 'w') as f:
            f.write(prompt)
        cmd = [sys.executable, AGY_BRIDGE, '--task', 'draft', '--prompt-file', tmp]
        if force_glm:
            # z.ai/GLM retired 2026-07-27. Pinning it now guarantees a failed
            # attempt, so the flag is accepted and ignored rather than honoured.
            print('[enrich] --force-glm ignored: z.ai/GLM retired 2026-07-27, '
                  'running the normal Gemini-first chain', file=sys.stderr)
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
        if out.returncode == 3:
            return None, 'fallback_to_claude sentinel'
        if out.returncode != 0:
            return None, f'agy-bridge rc={out.returncode}: {out.stderr.strip()[:160]}'
        body = out.stdout.strip()
        if '## 🎯 Goal' not in body:
            return None, 'model did not return the required card shape'
        body = body[body.index('## 🎯 Goal'):]
        note = ''
        for line in out.stderr.splitlines():
            if 'answered by' in line:
                note = line.strip()
        return body, note
    except Exception as e:
        return None, f'{type(e).__name__}: {e}'
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

def enrich_card(card, args):
    path = os.path.join(BASE_DIR, card['file'])
    if not os.path.exists(path):
        return 'missing', card['file'], None
    with open(path) as f:
        card_text = f.read()
    if '## 🎯 Goal' in card_text and not args.regenerate:
        return 'already', card['file'], None

    # Calendar first: it is authoritative and cheap. Slack search is the fallback
    # guess, and it only ever produced 'unknown' for a declined or cancelled event.
    status, evidence = calendar_status(card, args.date)
    if not status:
        status, evidence = live_status(card)
    docs = hunt_docs(card, card_text)
    body, note = call_agy(build_prompt(card, card_text, status, evidence, docs),
                          force_glm=args.force_glm)
    if body is None:
        return 'failed', f"{card['file']}: {note}", status

    if '—' in body or '--' in body:
        body = body.replace('—', ', ').replace('--', '-')

    header = f"# {card['time_wib']} WIB - {card.get('title')}\n"
    # The mechanical join is kept underneath: it is the audit trail for every
    # claim above it, and the owner has to be able to check the brief against it.
    out_text = (header + '\n' + body.strip() + '\n\n---\n\n'
                '<details>\n<summary>Mechanical card (source data)</summary>\n\n'
                + card_text + '\n</details>\n')
    if args.dry_run:
        print(f"\n{'='*70}\n{card['file']}  [status={status}]\n{'='*70}\n{body[:1200]}")
        return 'dry', card['file'], status
    with open(path, 'w') as f:
        f.write(out_text)
    return 'ok', f"{card['file']}  [{status}] {note}", status

def main():
    p = argparse.ArgumentParser(description='Enrich pre-meeting cards via agy-bridge/GLM')
    p.add_argument('--date', required=True, help='WIB date, YYYY-MM-DD')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--regenerate', action='store_true',
                   help='re-enrich cards that already have a brief')
    p.add_argument('--force-glm', action='store_true',
                   help='DEPRECATED no-op: z.ai/GLM retired 2026-07-27, always runs the Gemini-first chain')
    args = p.parse_args()

    with open(STATE_PATH) as f:
        state = json.load(f)
    day = state.get('dates', {}).get(args.date)
    if not day:
        sys.exit(f'no cards generated for {args.date}; run premeeting_cards.py generate first')

    cards = [c for c in day.get('cards', []) if _is_substantive(c)]
    skipped = len(day.get('cards', [])) - len(cards)
    print(f'enrich: {len(cards)} substantive card(s) for {args.date} '
          f'({skipped} skipped as non-meetings)')

    counts = {}
    status_flags = []
    for card in cards:
        result, detail, status = enrich_card(card, args)
        counts[result] = counts.get(result, 0) + 1
        icon = {'ok': '✓', 'dry': '·', 'already': '=', 'failed': '!', 'missing': '?'}[result]
        print(f'  {icon} {detail}')
        if result in ('ok', 'dry') and status and status != 'unknown':
            status_flags.append(f"{card['time_wib']} {card.get('title')}: {status}")

    print('\n' + ', '.join(f'{k}={v}' for k, v in sorted(counts.items())))
    if status_flags:
        # Surfaced loudly: a cancelled meeting the owner still prepares for is the
        # exact failure this script exists to prevent.
        print('\n⚠️  STATUS FLAGS (surface these at the top of the briefing):')
        for f_ in status_flags:
            print(f'  - {f_}')
    if counts.get('failed'):
        print('\nSome cards failed. They keep their mechanical card; enrich those '
              'inline rather than shipping them mentah.', file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()
