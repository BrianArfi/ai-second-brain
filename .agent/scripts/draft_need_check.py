#!/usr/bin/env python3
"""draft_need_check.py - Should this reply draft exist at all?

41 of 150 corrections the owner gave on message drafts between 10 Aug and 27 Sep 2026 were
"drop this": he had already replied by hand, it was handled in another session, or a
thumbs-up was enough (journal/analysis/draft_feedback_baseline_2026-09-27.md, pattern 1).
This check runs before a reply is drafted, and again before an approved draft is sent
if time has passed.

Usage:
  python3 .agent/scripts/draft_need_check.py --permalink <slack message url> [--text "<incoming>"]
  python3 .agent/scripts/draft_need_check.py --text "<incoming>"          # ack test only

Prints one verdict line and exits with its code:
  0  DRAFT                   nothing found against drafting
  10 SKIP already-replied    the owner posted in the thread (or channel) after the message,
                             or reacted with an ack emoji
  11 REACT confirmation      the incoming message only confirms or acknowledges; a 👍
                             reaction is the reply, not text
Read-only. No Slack write calls.
"""
import argparse
import os
import re
import sys
import time

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(BASE_DIR, '.agent', 'skills', 'slack-tracker', 'scripts'))
import mention_ledger as ml  # noqa: E402  (load_token, slack, ACK_REACTIONS)

# A message that only confirms or acknowledges. the owner, 18 Sep 2026: "you don't need to
# respond and reply for confirmations ... a reaction with ok or thumbs up is already good".
ACK_RE = re.compile(
    r"^\s*(<@\w+(\|[^>]*)?>\s*)?"
    r"(ok(ay|e)?|noted|sure|done|got it|will do|on it|confirmed?|thanks?( you)?|"
    r"thx|ty|sounds good|perfect|great|cool|agreed|yes|yep|yup|siap|sip|oke|baik|"
    r"ok noted|noted with thanks|will check|will update|received)"
    r"([\s,.!]+(bro|owner|ya|thanks?|thank you|will do|noted|done|:\w+:))*[\s.!]*"
    r"(:\w+:\s*)*$",
    re.I,
)
QUESTION_RE = re.compile(r"\?|\b(can you|could you|please|would you|when|what|which|who|how)\b", re.I)
LINK_RE = re.compile(r'archives/([CDG][A-Z0-9]+)/p(\d{10})(\d{6})(?:\?[^\s]*thread_ts=(\d+\.\d+))?')

def is_ack(text):
    """True when the message only confirms. Short, matches an ack form, asks nothing."""
    t = (text or '').strip()
    if not t or len(t) > 120 or QUESTION_RE.search(t):
        return False
    return bool(ACK_RE.match(t))

def parse_permalink(url):
    m = LINK_RE.search(url or '')
    if not m:
        return None
    channel, sec, frac, thread_ts = m.groups()
    return channel, f'{sec}.{frac}', thread_ts

def brian_replied_after(token, channel, ts, thread_ts=None):
    """Return how the owner already answered the message at `ts`, or None."""
    me = ml.slack('auth.test', token)
    owner = me.get('user_id') or ml.BRIAN_ID_DEFAULT
    anchor = thread_ts or ts
    resp = ml.slack('conversations.replies', token, {'channel': channel, 'ts': anchor, 'limit': 200})
    if resp.get('ok'):
        for m in resp.get('messages', []):
            if m.get('ts') == ts:
                for r in m.get('reactions', []):
                    if r.get('name') in ml.ACK_REACTIONS and owner in r.get('users', []):
                        return f'reacted :{r["name"]}:'
            if m.get('user') == owner and float(m['ts']) > float(ts):
                return 'replied in the thread'
    time.sleep(ml.API_PAUSE)
    if not thread_ts:
        # Top-level message: a later the owner post in the same channel or DM counts.
        hist = ml.slack('conversations.history', token,
                        {'channel': channel, 'oldest': ts, 'limit': 200})
        if hist.get('ok') and any(m.get('user') == owner and float(m['ts']) > float(ts)
                                  for m in hist.get('messages', [])):
            return 'posted in the channel after it'
    return None

def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding='utf-8')
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--permalink')
    ap.add_argument('--text', help='the incoming message text')
    a = ap.parse_args()
    if not a.permalink and a.text is None:
        ap.error('give --permalink, --text, or both')

    if a.permalink:
        parsed = parse_permalink(a.permalink)
        if not parsed:
            print('DRAFT (could not parse the permalink, check skipped)')
            return 0
        how = brian_replied_after(ml.load_token(), *parsed)
        if how:
            print(f'SKIP already-replied: the owner {how}. No draft.')
            return 10
    if a.text is not None and is_ack(a.text):
        print('REACT confirmation: the message only confirms. React 👍, no text reply.')
        return 11
    print('DRAFT')
    return 0

if __name__ == '__main__':
    sys.exit(main())
