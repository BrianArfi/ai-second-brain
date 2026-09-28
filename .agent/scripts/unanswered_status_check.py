#!/usr/bin/env python3
"""Stale-status guard: verify that an item reported as "unanswered" is actually unanswered.

Two checks, both born from the 28 Sep 2026 correction: the briefings kept surfacing
items the owner had already handled outside the harness (his approvals happen in his own
Slack/mail client, which the inbound-only sweeps cannot see).

  calendar  - list upcoming events where the owner's own responseStatus is accepted or
              declined, so an "unanswered invite" line is never written for one.
  thread    - given a Slack channel + thread ts, look for a message from the owner AFTER
              the referenced message. Exit code 0 + HANDLED if found, 1 + OPEN if not.

Usage:
  python3 .agent/scripts/unanswered_status_check.py calendar [--days 7] [--json OUT]
  python3 .agent/scripts/unanswered_status_check.py thread --channel C09... --ts 1789387556.011429 [--json OUT]
"""
import argparse
import json
import os
import subprocess
import sys

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
HANDLED = {'accepted', 'declined'}

GCAL = os.path.join(BASE_DIR, '.agent', 'skills', 'google-calendar-connector', 'gcal_manager.py')
SLACK_CLIENT = os.path.join(BASE_DIR, '.agent', 'skills', 'slack-connector', 'scripts', 'slack_client.py')

_CALENDAR_EMAIL = None
_SLACK_ID = None

def brian_calendar_email(profile='work'):
    """Resolve the account email from the live token (gcal_manager whoami), env override first."""
    global _CALENDAR_EMAIL
    if _CALENDAR_EMAIL:
        return _CALENDAR_EMAIL
    _CALENDAR_EMAIL = os.environ.get('BRIAN_CAL_EMAIL') or ''
    if not _CALENDAR_EMAIL:
        out = subprocess.run(
            [sys.executable, GCAL, 'whoami', '--profile', profile],
            capture_output=True, text=True, encoding='utf-8', errors='replace')
        _CALENDAR_EMAIL = (out.stdout or '').strip().lower()
    if not _CALENDAR_EMAIL or _CALENDAR_EMAIL == 'NO_TOKEN':
        sys.exit('FATAL: could not resolve the calendar account email '
                 '(set BRIAN_CAL_EMAIL or run gcal_manager.py auth --profile %s)' % profile)
    return _CALENDAR_EMAIL

def brian_slack_id():
    """Resolve the user id from the token itself (auth.test), env override first."""
    global _SLACK_ID
    if _SLACK_ID:
        return _SLACK_ID
    _SLACK_ID = os.environ.get('OWNER_SLACK_ID') or ''
    if not _SLACK_ID:
        _SLACK_ID = _slack('auth.test', {}).get('user_id', '')
    if not _SLACK_ID:
        sys.exit('FATAL: could not resolve the Slack user id '
                 '(set OWNER_SLACK_ID or check the SLACK_USER_TOKEN)')
    return _SLACK_ID

def _load_slack_token():
    tok = os.environ.get('SLACK_USER_TOKEN')
    if tok:
        return tok
    env = os.path.join(BASE_DIR, '.agent', 'skills', 'slack-connector', 'token.env')
    if os.path.exists(env):
        with open(env, encoding='utf-8') as f:
            for line in f:
                if line.startswith('SLACK_USER_TOKEN='):
                    return line.split('=', 1)[1].strip()
    sys.exit('FATAL: SLACK_USER_TOKEN not found (env or slack-connector/token.env)')

def _slack(method, params):
    token = _load_slack_token()
    import urllib.request
    url = 'https://slack.com/api/' + method
    data = json.dumps(params).encode()
    req = urllib.request.Request(url, data=data, headers={
        'Authorization': 'Bearer ' + token,
        'Content-Type': 'application/json; charset=utf-8',
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        payload = json.loads(r.read().decode('utf-8'))
    if not payload.get('ok'):
        raise RuntimeError('slack api error: %s' % payload.get('error'))
    return payload

def calendar_check(days):
    """Returns (report_lines, handled_events, open_events)."""
    out = subprocess.run(
        [sys.executable, GCAL, 'list', '--profile', 'work',
         '--days-back', '0', '--days-forward', str(days), '--json'],
        capture_output=True, text=True, encoding='utf-8', errors='replace')
    # gcal prints a preamble line before the JSON array
    raw = out.stdout
    payload = raw[raw.index('['):]
    events = json.loads(payload)
    handled, open_items = [], []
    for e in events:
        attendees = e.get('attendees') or []
        mine = [a for a in attendees if a.get('email', '').lower() == brian_calendar_email()]
        if not mine:
            continue  # no invite outstanding for the owner
        status = mine[0].get('responseStatus', 'needsAction')
        entry = {'date': str(e.get('start'))[:16], 'summary': e.get('summary', ''),
                 'status': status}
        (handled if status in HANDLED else open_items).append(entry)
    lines = ['## Stale-status check (calendar RSVP, next %d days)' % days]
    if handled:
        lines.append('### Already handled (never surface as unanswered):')
        for h in handled:
            lines.append('- %s %s' % (h['date'], h['summary']))
    if open_items:
        lines.append('### Genuinely unanswered (safe to surface):')
        for o in open_items:
            lines.append('- %s %s' % (o['date'], o['summary']))
    return lines, handled, open_items

def thread_check(channel, ts):
    """Look for a message from the owner in the thread after ts. Returns (ok, snippet)."""
    resp = _slack('conversations.history', {'channel': channel, 'oldest': str(ts),
                                            'limit': 50})
    for msg in resp.get('messages', []):
        if msg.get('user') == brian_slack_id() and float(msg.get('ts', 0)) > float(ts):
            text = (msg.get('text') or '')[:120].replace('\n', ' ')
            return True, text
    return False, None

def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('calendar')
    c.add_argument('--days', type=int, default=7)
    c.add_argument('--json', dest='json_out', default=None)
    t = sub.add_parser('thread')
    t.add_argument('--channel', required=True)
    t.add_argument('--ts', required=True)
    t.add_argument('--json', dest='json_out', default=None)
    a = p.parse_args()

    if a.cmd == 'calendar':
        lines, handled, open_items = calendar_check(a.days)
        report = '\n'.join(lines)
        if a.json_out:
            with open(a.json_out, 'w', encoding='utf-8') as f:
                json.dump({'generated_at': subprocess.run(['date', '-Iseconds'],
                            capture_output=True, text=True).stdout.strip(),
                           'handled': handled, 'open': open_items},
                          f, ensure_ascii=False, indent=1)
        print(report)
        return
    if a.cmd == 'thread':
        ok, snippet = thread_check(a.channel, a.ts)
        result = {'channel': a.channel, 'ts': a.ts,
                  'handled': ok, 'brian_reply': snippet}
        if a.json_out:
            with open(a.json_out, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=1)
        if ok:
            print('HANDLED (the owner replied after %s): %s' % (a.ts, snippet))
            return
        print('OPEN (no the owner message found after %s)' % a.ts)
        sys.exit(1)

if __name__ == '__main__':
    main()
