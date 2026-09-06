#!/usr/bin/env python3
"""
Access-request watcher.

Why this exists: on 1 Sep 2026 the owner found Slack threads asking him for access
that no sweep had ever surfaced. Two separate holes:

  1. Slack. The mention ledger DID capture "we don't have access to the doc
     above" as an open item, but the report is one flat list sorted by age, so a
     blocking access ask reads exactly like small talk. Nothing marked it.
  2. Google Drive. A "Share request for <doc>" arrives by EMAIL, never touches
     Slack, and no sweep read Gmail at all. Five requests were sitting unanswered,
     the oldest 39 days.

This script covers both, and it verifies rather than trusts: a Drive request is
only reported as pending when the requester still has no permission on the file
right now. Read-only, never grants anything (that stays the owner's call), but it
prints the exact grant command for each one.

Usage:
  python3 .agent/skills/access-watch/scripts/access_watch.py report     # markdown, for briefings
  python3 .agent/skills/access-watch/scripts/access_watch.py report --json
  python3 .agent/skills/access-watch/scripts/access_watch.py report --days 120
"""
import argparse
import base64
import json
import os
import re
import sys
import time
import warnings
from datetime import datetime, timezone

warnings.filterwarnings("ignore")

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
GMAIL_DIR = os.path.join(BASE_DIR, '.agent', 'skills', 'gmail-connector')
DRIVE_DIR = os.path.join(BASE_DIR, '.agent', 'skills', 'work-drive-connector')
LEDGER = os.path.join(BASE_DIR, 'journal', 'state', 'slack_mention_ledger.json')
IGNORE = os.path.join(BASE_DIR, 'journal', 'state', 'access_watch_ignore.json')

BRIAN_ID = '<SLACK_ID>'

# An access ask, not a mention of the word "access". Deliberately narrow: a false
# positive costs a line in the briefing, a false negative costs a blocked vendor
# for a week, so the patterns are phrases people actually use when locked out.
ACCESS_PATTERNS = [
    r"\b(don'?t|do not|doesn'?t|dont) have access\b",
    r"\bno access\b",
    r"\b(can'?t|cannot|couldn'?t|unable to) (open|access|view|see|log ?in|login|ssh)\b",
    r"\brequest(ed|ing)? access\b",
    r"\b(give|grant|provide|share) (me |us |him |her |them )?(the )?access\b",
    r"\bcan you (provide|grant|give|share) access\b",
    r"\bplease share\b.*\b(doc|document|sheet|link|prd|access|recording)\b",
    r"\b(add|invite) (me|us|him|her|them)\b.*\b(channel|repo|board|drive|folder|workspace)\b",
    r"\baccess (request|denied|issue)\b",
    r"\bpermission denied\b",
    r"\b(belum|tidak|gak|nggak) (punya|ada) akses\b",
    r"\bminta akses\b",
]
ACCESS_RE = re.compile('|'.join(ACCESS_PATTERNS), re.I)

DOC_RE = re.compile(r"(?:document|spreadsheets|presentation|file|forms)/d/([A-Za-z0-9_-]{20,})")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
SKIP_SENDERS = ('noreply', 'no-reply', 'drive-shares', 'google.com')

def is_access_request(text):
    """True when the message is somebody telling the owner they are locked out."""
    return bool(ACCESS_RE.search(text or ''))

# --------------------------------------------------------------- Drive side --

def _ignored():
    """Requests deliberately not actioned (a recruitment form every candidate
    pings, a doc the owner will never share). Keyed file_id or file_id::email."""
    if not os.path.exists(IGNORE):
        return set()
    try:
        return set(json.load(open(IGNORE)).get('ignore', []))
    except Exception:
        return set()

def _add_ignore(keys, reason):
    data = {'ignore': [], 'notes': {}}
    if os.path.exists(IGNORE):
        try:
            data = json.load(open(IGNORE))
        except Exception:
            pass
    data.setdefault('ignore', [])
    data.setdefault('notes', {})
    for k in keys:
        if k not in data['ignore']:
            data['ignore'].append(k)
        data['notes'][k] = reason
    tmp = IGNORE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, IGNORE)

def _drive():
    sys.path.insert(0, DRIVE_DIR)
    cwd = os.getcwd()
    os.chdir(DRIVE_DIR)                     # token.json resolves relative to cwd
    try:
        from gdrive_manager import authenticate  # type: ignore
        from googleapiclient.discovery import build  # type: ignore
        return build('drive', 'v3', credentials=authenticate())
    finally:
        os.chdir(cwd)

def _gmail():
    sys.path.insert(0, GMAIL_DIR)
    cwd = os.getcwd()
    os.chdir(GMAIL_DIR)
    try:
        import gmail_manager  # type: ignore
        from googleapiclient.discovery import build  # type: ignore
        return build('gmail', 'v1', credentials=gmail_manager.authenticate())
    finally:
        os.chdir(cwd)

def _body_text(payload):
    out = []
    stack = [payload]
    while stack:
        p = stack.pop()
        data = (p.get('body') or {}).get('data')
        if data:
            try:
                out.append(base64.urlsafe_b64decode(data).decode('utf-8', 'replace'))
            except Exception:
                pass
        stack.extend(p.get('parts') or [])
    return '\n'.join(out)

def drive_requests(days):
    """Share-request emails whose requester STILL has no permission on the file."""
    svc = _gmail()
    q = (f'from:drive-shares-dm-noreply@google.com subject:"Share request" '
         f'newer_than:{days}d')
    msgs = svc.users().messages().list(userId='me', q=q, maxResults=100).execute().get('messages', [])
    drive = _drive() if msgs else None
    perm_cache = {}
    seen = set()
    ignore = _ignored()
    pending = []
    for m in msgs:
        full = svc.users().messages().get(userId='me', id=m['id'], format='full').execute()
        headers = {h['name'].lower(): h['value'] for h in full['payload'].get('headers', [])}
        subject = headers.get('subject', '')
        body = _body_text(full['payload'])
        fids = DOC_RE.findall(body)
        if not fids:
            continue
        fid = fids[0]
        who = [e for e in EMAIL_RE.findall(body)
               if not any(s in e.lower() for s in SKIP_SENDERS)]
        if not who:
            continue
        requester = who[0].lower()
        if (fid, requester) in seen:
            continue
        if fid in ignore or f'{fid}::{requester}' in ignore:
            continue
        seen.add((fid, requester))
        if fid not in perm_cache:
            try:
                meta = drive.files().get(fileId=fid, fields='name,driveId',
                                         supportsAllDrives=True).execute()
                perms = drive.permissions().list(
                    fileId=fid, supportsAllDrives=True,
                    fields='permissions(type,role,emailAddress,domain)'
                ).execute().get('permissions', [])
                perm_cache[fid] = (meta, perms, None)
            except Exception as e:                      # not ours / deleted
                perm_cache[fid] = (None, [], str(e)[:120])
        meta, perms, err = perm_cache[fid]
        if err:
            continue
        granted = {(p.get('emailAddress') or '').lower() for p in perms}
        anyone = any(p['type'] == 'anyone' for p in perms)
        domain = {p.get('domain') for p in perms if p['type'] == 'domain'}
        req_domain = requester.split('@')[-1]
        if anyone or requester in granted or req_domain in domain:
            continue
        ts = int(full.get('internalDate', '0')) / 1000
        pending.append({
            'source': 'drive',
            'requester': requester,
            'doc': meta.get('name'),
            'file_id': fid,
            'shared_drive': bool(meta.get('driveId')),
            'asked_at': ts,
            'subject': subject,
            'grant_cmd': (f"python3 .agent/skills/access-watch/scripts/access_watch.py "
                          f"grant --file {fid} --email {requester} --approved"),
        })
    pending.sort(key=lambda r: r['asked_at'])
    return pending

def grant(file_id, email, role='commenter', message=None):
    drive = _drive()
    body = {'type': 'user', 'role': role, 'emailAddress': email}
    kw = dict(fileId=file_id, supportsAllDrives=True, sendNotificationEmail=True, body=body)
    if message:
        kw['emailMessage'] = message
    return drive.permissions().create(**kw).execute()

# --------------------------------------------------------------- Slack side --

def slack_requests():
    """Open mention-ledger items that are somebody asking the owner for access."""
    if not os.path.exists(LEDGER):
        return []
    state = json.load(open(LEDGER))
    names = state.get('user_names', {})
    out = []
    for iid, it in state.get('items', {}).items():
        if it.get('status') != 'open':
            continue
        if not is_access_request(it.get('text')):
            continue
        out.append({
            'source': 'slack',
            'item_id': iid,
            'requester': names.get(it.get('author'), it.get('author')),
            'channel': it.get('channel_name'),
            'text': re.sub(r'\s+', ' ', it.get('text') or '')[:200],
            'permalink': it.get('permalink'),
            'asked_at': float(it['ts']),
        })
    out.sort(key=lambda r: r['asked_at'])
    return out

def age(ts):
    d = (time.time() - ts) / 86400
    if d < 1:
        return f'{int(d * 24)}h'
    return f'{d:.0f}d'

def cmd_report(args):
    slack = slack_requests()
    drive = [] if args.no_drive else drive_requests(args.days)
    if args.out:
        snap = {'generated_at': time.time(), 'slack': slack, 'drive': drive}
        path = args.out if os.path.isabs(args.out) else os.path.join(BASE_DIR, args.out)
        tmp = path + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(snap, f, indent=1)
        os.replace(tmp, path)                 # cron writes this, briefings read it
        print(f'wrote {path}: {len(drive)} drive + {len(slack)} slack pending')
        return 1 if (slack or drive) else 0
    if args.json:
        print(json.dumps({'slack': slack, 'drive': drive}, indent=1))
        return 1 if (slack or drive) else 0
    total = len(slack) + len(drive)
    if not total:
        print('No access requests waiting. Clean.')
        return 0
    print(f'## 🔑 Access requests waiting on you ({total})\n')
    if drive:
        print('**Google Drive share requests** (verified: requester still has no permission)\n')
        for r in drive:
            where = ' *(shared drive, external users blocked by policy)*' if r['shared_drive'] else ''
            print(f"- **{r['doc']}** · {r['requester']} · {age(r['asked_at'])} ago{where}")
            print(f"  `{r['grant_cmd']}`")
        print()
    if slack:
        print('**Slack**\n')
        for r in slack:
            link = f" [thread]({r['permalink']})" if r.get('permalink') else ''
            print(f"- **{r['channel']}** · {r['requester']} · {age(r['asked_at'])} ago{link} — {r['text']}")
            print(f"  `{r['item_id']}`")
    return 1

def cmd_dismiss(args):
    key = args.file if not args.email else f'{args.file}::{args.email}'
    _add_ignore([key], args.reason or 'dismissed by hand')
    print(f'ignoring {key} — {args.reason or "dismissed by hand"}')

def cmd_grant(args):
    if not args.approved:
        sys.exit('Refusing: granting access is an external write. Re-run with --approved '
                 'once the owner has signed off on this specific grant.')
    r = grant(args.file, args.email, args.role, args.message)
    print(f"granted {args.role} to {args.email} on {args.file} (permission {r.get('id')})")

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd')
    rp = sub.add_parser('report')
    rp.add_argument('--days', type=int, default=90)
    rp.add_argument('--json', action='store_true')
    rp.add_argument('--no-drive', action='store_true', help='skip Gmail/Drive, Slack only')
    rp.add_argument('--out', help='write a JSON snapshot here instead of printing '
                                  '(cron writes it, briefings read it without waiting on Gmail)')
    gp = sub.add_parser('grant')
    gp.add_argument('--file', required=True)
    gp.add_argument('--email', required=True)
    gp.add_argument('--role', default='commenter', choices=['reader', 'commenter', 'writer'])
    gp.add_argument('--message')
    gp.add_argument('--approved', action='store_true')
    dp = sub.add_parser('dismiss')
    dp.add_argument('--file', required=True)
    dp.add_argument('--email', help='dismiss this requester only (default: the whole file)')
    dp.add_argument('--reason')
    args = ap.parse_args()
    if args.cmd == 'grant':
        cmd_grant(args)
    elif args.cmd == 'dismiss':
        cmd_dismiss(args)
    else:
        sys.exit(cmd_report(args) and 0)

if __name__ == '__main__':
    main()
