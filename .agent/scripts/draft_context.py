#!/usr/bin/env python3
"""draft_context.py - Everything to read before drafting a Slack reply, in one call.

Two of the five corrections that came back three or more times in W39 (2026-09-21 to 27)
were about context, not wording:

  NOT_NEEDED (7)      "ini udah dijawab di sesi sebelah bukan?", "done udah gw reply sendiri".
                      The draft should not have existed: the owner already answered, by hand or
                      in another Claude session.
  MISSED_CONTEXT (3)  "check the thread again, Teammate requested something", "salah dong, ini
                      goes against apa yang udah gw kirim kemarin". The draft answered the
                      last message and missed the thread, or contradicted what the owner himself
                      said in that channel the day before.

`draft_need_check.py` covered only the first half of NOT_NEEDED (a later the owner message or
an ack reaction). This adds the other session, and prints the context that was missing.

Usage:
  python3 .agent/scripts/draft_context.py --permalink <slack message url> [--session <id>]

Prints, in order:
  VERDICT   DRAFT, or SKIP with the reason (exit 10 already replied, 12 another session)
  SESSIONS  other Claude sessions in the last 72 hours that drafted or SENT into this thread
  THREAD    every message in the thread, oldest first, with names and WIB times
  OWNER     his last 3 messages in this channel outside the thread, so the draft does not
            contradict what he said there

`.claude/hooks/draft_lint_stop.py` checks that this ran for a permalink before a reply
draft to it is shown. Read-only; no Slack write calls.
"""
import argparse
import glob
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, '.agent', 'skills', 'slack-tracker', 'scripts'))

from draft_need_check import parse_permalink, brian_replied_after, is_ack  # noqa: E402

WIB = timezone(timedelta(hours=7))
ROUTER_STATE = os.path.join(BASE_DIR, 'journal', 'state', 'reply_router_state.json')
OPEN_STATUSES = ('open', 'claimed', 'active')
LOOKBACK_HOURS = 72
HOME = os.path.expanduser('~')
TRANSCRIPT_ROOTS = [
    os.path.join(HOME, '.claude/projects/C--Users-the owner--gemini-antigravity-scratch-product-second-brain'),
    os.path.join(HOME, '.claude/projects/-home-you-antigravity-projects-product-second-brain'),
    os.path.join(HOME, '.claude/projects/-Users-you-product-second-brain'),
    '/mnt/c/Users/you/.claude/projects/C--Users-the owner--gemini-antigravity-scratch-product-second-brain',
    '//wsl.localhost/Ubuntu~/.claude/projects/-home-you-antigravity-projects-product-second-brain',
]
SEND_RE = re.compile(r'slack_client\.py[^\n]*--action[ =]post')

def when(ts):
    try:
        return datetime.fromtimestamp(float(ts), WIB).strftime('%d %b %H:%M WIB')
    except (TypeError, ValueError):
        return '?'

def router_owner(channel, thread_ts, session):
    """(conversation key, session id) when another live session owns this conversation."""
    try:
        with open(ROUTER_STATE, encoding='utf-8') as f:
            convs = (json.load(f) or {}).get('conversations') or {}
    except (OSError, ValueError):
        return None
    for key, conv in convs.items():
        if not key.startswith(channel + ':'):
            continue
        if thread_ts and key != f'{channel}:t{thread_ts}':
            msgs = conv.get('messages') or []
            if not any(len(m) > 1 and m[1] == thread_ts for m in msgs):
                continue
        owner = conv.get('session_id')
        if conv.get('status') in OPEN_STATUSES and owner and owner != session:
            return key, owner
    return None

def other_sessions(ts, thread_ts, session):
    """[(session, 'sent'|'drafted', when)] from transcripts that touched this message.

    A message is identified by its ts in either form: `p1790232936726969` in a permalink,
    `1790232936.726969` in a --thread-ts flag.
    """
    needles = {ts, 'p' + ts.replace('.', '')}
    if thread_ts:
        needles |= {thread_ts, 'p' + thread_ts.replace('.', '')}
    cutoff = time.time() - LOOKBACK_HOURS * 3600
    found = {}
    seen_files = set()
    for root in TRANSCRIPT_ROOTS:
        if not os.path.isdir(root):
            continue
        for path in glob.glob(os.path.join(root, '*.jsonl')):
            real = os.path.realpath(path)
            sid = os.path.basename(path)[:-6]
            if real in seen_files or (session and sid.startswith(session)):
                continue
            seen_files.add(real)
            try:
                if os.path.getmtime(path) < cutoff:
                    continue
                with open(path, encoding='utf-8', errors='replace') as fh:
                    for line in fh:
                        if not any(n in line for n in needles):
                            continue
                        kind = 'sent' if SEND_RE.search(line) and '--approved' in line else 'drafted'
                        try:
                            stamp = json.loads(line).get('timestamp', '')[:16]
                        except ValueError:
                            stamp = ''
                        if found.get(sid, ('', ''))[0] != 'sent':
                            found[sid] = (kind, stamp)
            except OSError:
                continue
    return [(sid, k, s) for sid, (k, s) in found.items()]

def names():
    try:
        from slack_text import user_names
        return user_names()
    except Exception:
        return {}

def render(text, nm):
    try:
        from slack_text import render as r
        return r(text or '', collapse=True)
    except Exception:
        return re.sub(r'<@(U\w+)(\|[^>]*)?>', lambda m: '@' + nm.get(m.group(1), m.group(1)), text or '')

def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding='utf-8')
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--permalink', required=True)
    ap.add_argument('--session', default=os.environ.get('CLAUDE_SESSION_ID', ''),
                    help='this session id, excluded from the other-session scan')
    ap.add_argument('--owner-last', type=int, default=3)
    a = ap.parse_args()

    parsed = parse_permalink(a.permalink)
    if not parsed:
        print('VERDICT DRAFT (could not parse the permalink, nothing checked)')
        return 0
    channel, ts, thread_ts = parsed

    import mention_ledger as ml
    token = ml.load_token()
    me = ml.slack('auth.test', token).get('user_id') or ml.BRIAN_ID_DEFAULT
    nm = names()

    verdicts = []
    how = brian_replied_after(token, channel, ts, thread_ts)
    if how:
        verdicts.append((10, f'SKIP already-replied: the owner {how}'))
    # Without --session the caller may itself be the owner, so a claim is only reported.
    owner = router_owner(channel, thread_ts or ts, a.session) if a.session else None
    if not a.session and router_owner(channel, thread_ts or ts, ''):
        print('NOTE reply-router has a live session on this conversation; pass --session '
              '<your session id> to tell whether it is this one')
    if owner:
        verdicts.append((12, f'SKIP other-session: reply-router gave `{owner[0]}` to session {owner[1]}'))
    others = other_sessions(ts, thread_ts, a.session)
    for sid, kind, stamp in others:
        if kind == 'sent':
            verdicts.append((12, f'SKIP other-session: session {sid} SENT into this thread ({stamp} UTC)'))

    anchor = thread_ts or ts
    resp = ml.slack('conversations.replies', token, {'channel': channel, 'ts': anchor, 'limit': 200})
    msgs = resp.get('messages', []) if resp.get('ok') else []
    target = next((m for m in msgs if m.get('ts') == ts), None)
    if not verdicts and target and is_ack(render(target.get('text', ''), nm)):
        verdicts.append((11, 'REACT confirmation: the message only confirms. React, no text reply'))

    if verdicts:
        for _, v in verdicts:
            print('VERDICT ' + v)
    else:
        print('VERDICT DRAFT')

    print('\nSESSIONS')
    if others:
        for sid, kind, stamp in others:
            print(f'- {sid} {kind} ({stamp} UTC). Read what it produced before drafting.')
    else:
        print(f'- no other session touched this message in the last {LOOKBACK_HOURS} hours')

    print(f'\nTHREAD ({len(msgs)} messages, oldest first)')
    for m in msgs:
        who = 'the owner' if m.get('user') == me else nm.get(m.get('user'), m.get('user') or m.get('bot_id') or '?')
        mark = '  <- this message' if m.get('ts') == ts else ''
        print(f'**{who}** · {when(m.get("ts"))}{mark}')
        print('\n'.join('> ' + ln for ln in (render(m.get('text', ''), nm) or '(no text)').split('\n')))
        print()

    time.sleep(ml.API_PAUSE)
    hist = ml.slack('conversations.history', token, {'channel': channel, 'limit': 200})
    mine = [m for m in hist.get('messages', []) if m.get('user') == me][:a.brian_last] if hist.get('ok') else []
    print(f'OWNER (his last {len(mine)} messages in this channel, newest first)')
    for m in mine:
        print(f'**the owner** · {when(m.get("ts"))}')
        print('\n'.join('> ' + ln for ln in render(m.get('text', ''), nm).split('\n')))
        print()
    if not mine:
        print('- none in the last 200 channel messages')

    return max((c for c, _ in verdicts), default=0)

if __name__ == '__main__':
    sys.exit(main())
