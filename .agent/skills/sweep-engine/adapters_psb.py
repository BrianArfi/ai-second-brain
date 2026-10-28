#!/usr/bin/env python3
"""
adapters_psb.py - PSB source adapters for sweep_core. One adapter per connected tool,
each writing normalized items (see sweep_core.py docstring) to <out>/<name>.jsonl and
one health row to <out>/<name>.health.json.

  python3 adapters_psb.py <adapter> --out <dir> [--days N]

Adapters: slack_ledger, slack_threads, gmail, jira, calendar, linear, github.
run_sweep.py runs them as separate processes with a timeout each, so one hung API
cannot take the whole sweep down, and a failure is always named in the report.

Read-only. Nothing here posts, reacts, archives, marks read or RSVPs.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8')
    except (AttributeError, ValueError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
SKILLS = os.path.join(BASE, '.agent', 'skills')
LEDGER = os.path.join(BASE, 'journal', 'state', 'slack_mention_ledger.json')
SWEEP_STATE = os.path.join(BASE, 'journal', 'sweep', 'state.json')
SLACK_ENV = os.path.join(SKILLS, 'slack-connector', 'token.env')
# Who "me" is in each tool. config.json (held back from the public sync; copy
# config.example.json) pins them; anything unset is looked up at run time.
CONFIG = {}
try:
    CONFIG = json.load(open(os.path.join(HERE, 'config.json'), encoding='utf-8'))
except (OSError, ValueError):
    pass
ME_SLACK = CONFIG.get('slack_user_id', '')              # empty: Slack auth.test fills it
SLACK_HOST = CONFIG.get('slack_host', '')               # e.g. https://acme.slack.com
GDRIVE_SLACK_APP = CONFIG.get('gdrive_slack_app', '')   # empty: matched by bot name
CALENDAR_PROFILE = CONFIG.get('calendar_profile', 'default')

class Out:
    def __init__(self, out_dir, name):
        self.name = name
        self.dir = out_dir
        self.items = []
        self.notes = []
        self.source = name.split('_')[0]
        self.coverage_days = 0          # how far back an absent item would have been seen
        self.complete = True            # false when a budget or page cap cut the read short

    def add(self, **it):
        it.setdefault('source', self.name.split('_')[0])
        self.items.append(it)

    def write(self, status='ok'):
        os.makedirs(self.dir, exist_ok=True)
        with open(os.path.join(self.dir, f'{self.name}.jsonl'), 'w', encoding='utf-8') as fh:
            for it in self.items:
                fh.write(json.dumps(it, ensure_ascii=False) + '\n')
        with open(os.path.join(self.dir, f'{self.name}.health.json'), 'w', encoding='utf-8') as fh:
            json.dump([{'adapter': self.name, 'source': self.source, 'status': status,
                        'items': len(self.items), 'note': '; '.join(self.notes),
                        'coverage_days': self.coverage_days, 'complete': self.complete}], fh)
        print(f'{self.name}: {status}, {len(self.items)} items' +
              (f" ({'; '.join(self.notes)})" if self.notes else ''))

def title_key(prefix, title):
    sys.path.insert(0, HERE)
    from sweep_core import norm_title
    return f'{prefix}:{norm_title(title)}'

DOC_ID_RE = re.compile(r'docs\.google\.com/(?:document|spreadsheets|presentation|forms)/d/([A-Za-z0-9_-]{20,})')

def doc_keys(title, text):
    """(conversation_key, aliases) for a Google doc: the id when a link names it, the
    normalized title otherwise. Both are returned so the other source can join."""
    m = DOC_ID_RE.search(text or '')
    tkey = title_key('gdoc', title)
    if m:
        return f'gdoc:id:{m.group(1)}', [tkey]
    return tkey, []

# ------------------------------------------------------------------- Slack --

def _env(path, key):
    if os.environ.get(key):
        return os.environ[key]
    for line in open(path, encoding='utf-8'):
        if line.startswith(key + '='):
            return line.split('=', 1)[1].strip().strip('"').strip("'")
    raise SystemExit(f'{key} missing in {path}')

class Slack:
    """Slack Web API with real rate-limit handling: honours Retry-After on HTTP 429
    and `ratelimited` bodies, and never raises on a transient network error."""

    def __init__(self):
        self.token = _env(SLACK_ENV, 'SLACK_USER_TOKEN')
        self.calls = 0
        self.rate_waits = 0
        global ME_SLACK, SLACK_HOST
        if not ME_SLACK or not SLACK_HOST:
            who = self.api('auth.test')
            ME_SLACK = ME_SLACK or who.get('user_id', '')
            SLACK_HOST = SLACK_HOST or (who.get('url') or '').rstrip('/')

    def api(self, method, **params):
        for attempt in range(8):
            req = urllib.request.Request(
                f'https://slack.com/api/{method}?' + urllib.parse.urlencode(params),
                headers={'Authorization': f'Bearer {self.token}'})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    body = json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    self.rate_waits += 1
                    time.sleep(int(e.headers.get('Retry-After') or 10) + 1)
                    continue
                if attempt == 7:
                    return {'ok': False, 'error': f'http_{e.code}'}
                time.sleep(2 * (attempt + 1))
                continue
            except Exception as e:                       # noqa: BLE001 - network blip
                if attempt == 7:
                    return {'ok': False, 'error': f'request_failed: {e}'}
                time.sleep(2 * (attempt + 1))
                continue
            self.calls += 1
            if body.get('error') == 'ratelimited':
                self.rate_waits += 1
                time.sleep(int(body.get('retry_after') or 10) + 1)
                continue
            return body
        return {'ok': False, 'error': 'retries_exhausted'}

def _names():
    try:
        return json.load(open(LEDGER, encoding='utf-8')).get('user_names', {})
    except Exception:                                    # noqa: BLE001
        return {}

def _slack_key(channel, ts, thread_ts):
    if thread_ts and thread_ts != ts:
        return f'slack:{channel}:{thread_ts}'
    if channel.startswith('D'):
        return f'slack:{channel}'                       # a 1:1 DM is one conversation
    return f'slack:{channel}:{thread_ts or ts}'

def _ledger_title(ch, cname, names):
    cname = (cname or ch).lstrip('#')
    if ch.startswith('D'):
        uid = cname.split(':')[-1]
        return f'DM with {names.get(uid, uid)}'
    if cname.startswith('mpdm-'):
        return 'group DM'
    return f'#{cname}'

GDRIVE_NOTICE_RE = re.compile(
    r'^(?P<who>.+?) (?P<what>commented on|mentioned you in|assigned an action item to you in|'
    r'replied to a comment in|resolved a comment in|shared) (the file |an item with you)?(?P<title>.*?)\.?$',
    re.S)

def adapter_slack_ledger(out, days):
    """Open items in the mention ledger (mentions, DMs, thread follow-ups). The ledger's
    own cron keeps it current and closes answered items; this only reads it."""
    st = json.load(open(LEDGER, encoding='utf-8'))
    out.coverage_days = 30
    names = st.get('user_names', {})
    cnames = st.get('channel_names', {})
    last = st.get('last_sweep')
    if last and time.time() - float(last) > 3 * 3600:
        out.notes.append(f'ledger last swept {int((time.time() - float(last)) / 3600)}h ago')
    cut = time.time() - 30 * 86400
    for it in st.get('items', {}).values():
        ch = it.get('channel', '')
        if it.get('status') != 'open':
            # The ledger saw the owner answer (or dismissed it): tell the core, so the same
            # conversation reported open by another adapter closes too.
            at = float(it.get('answered_at') or 0)
            if at > cut:
                out.add(source='slack', conversation_key=_slack_key(ch, it.get('ts'), it.get('thread_ts')),
                        actor='you', actor_id=ME_SLACK, ts=at, is_self=True,
                        text=f"(ledger: {it.get('answered_by') or it.get('status')})")
            continue
        author = it.get('author', '')
        text = it.get('text', '')
        row = dict(source='slack', actor=names.get(author, author), actor_id=author,
                   ts=it.get('ts'), text=text, link=it.get('permalink', ''),
                   why_me='dm' if it.get('kind') == 'dm' else
                   ('thread_participant' if it.get('kind') == 'thread_followup' else 'mention'),
                   conversation_key=_slack_key(ch, it.get('ts'), it.get('thread_ts')),
                   title=_ledger_title(ch, it.get('channel_name') or cnames.get(ch, ch), names),
                   meta={'ledger_status': it.get('status'), 'priority': it.get('priority')})
        plain = re.sub(r'<(?:[^>|]*\|)?([^>]*)>', r'\1', text).strip()
        gdrive = (author == GDRIVE_SLACK_APP if GDRIVE_SLACK_APP
                  else names.get(author, '').lower() == 'google drive')
        m = GDRIVE_NOTICE_RE.match(plain) if gdrive else None
        if m:
            # Google Drive app notice in Slack: a real doc signal, collapse into the doc
            key, aliases = doc_keys(m.group('title'), text)
            row.update(actor=m.group('who').strip(), is_bot=False, source='gdocs',
                       collapse_into=key, aliases=aliases,
                       title=m.group('title').strip(),
                       why_me='action_item' if 'action item' in m.group('what') else 'doc_comment')
        out.add(**row)

sys.path.insert(0, HERE)
from sweep_core import DEFER_RE  # noqa: E402

def adapter_slack_threads(out, days, budget_s=300):
    """Every thread the user posted in over `days`, where someone else replied after
    the user's last message. Emits every message after the user's last one, plus the
    user's last message as a self event, so the core can close it once answered."""
    sl = Slack()
    names = _names()
    out.coverage_days = days
    after = time.strftime('%Y-%m-%d', time.gmtime(time.time() - days * 86400))
    threads, page = {}, 1
    toplevel = {}       # channel -> (name, ts of the user's latest top-level message)
    while page <= 10:
        r = sl.api('search.messages', query=f'from:<@{ME_SLACK}> after:{after}', count=100,
                   page=page, sort='timestamp')
        if not r.get('ok'):
            raise RuntimeError(f"search.messages: {r.get('error')}")
        m = r.get('messages', {})
        for x in m.get('matches', []):
            ch = x['channel']['id']
            q = urllib.parse.parse_qs(urllib.parse.urlparse(x.get('permalink', '')).query)
            tts = q.get('thread_ts', [x['ts']])[0]
            key = (ch, tts)
            prev = threads.get(key)
            if not prev or float(x['ts']) > prev[1]:
                threads[key] = (x['channel'].get('name', ch), float(x['ts']))
            if 'thread_ts' not in q and float(x['ts']) > toplevel.get(ch, ('', 0))[1]:
                toplevel[ch] = (x['channel'].get('name', ch), float(x['ts']))
        if page >= m.get('paging', {}).get('pages', 1):
            break
        page += 1

    def uname(u):
        if u not in names:
            info = sl.api('users.info', user=u)
            names[u] = (info.get('user') or {}).get('real_name', u)
        return names[u]

    started = time.time()
    done = 0
    # Threads already open in the sweep state go first: they hold the asks most at risk
    # of being dropped. Then most recently active, so a budget cut skips the quietest.
    known = set()
    try:
        st = json.load(open(SWEEP_STATE, encoding='utf-8'))
        known = {k for k, v in st.get('conversations', {}).items() if v.get('status') in ('open', 'aging', 'promised')}
    except (OSError, ValueError):
        pass
    order = sorted(threads.items(), key=lambda kv: (f'slack:{kv[0][0]}:{kv[0][1]}' not in known, -kv[1][1]))
    for (ch, tts), (cname, _) in order:
        if time.time() - started > budget_s:
            out.complete = False
            out.notes.append(f'time budget hit: {done}/{len(threads)} threads read, quietest skipped')
            break
        r = sl.api('conversations.replies', channel=ch, ts=tts, limit=200)
        done += 1
        msgs = r.get('messages', [])
        if len(msgs) < 2 and not ch.startswith('D'):
            continue
        mine = [x for x in msgs if x.get('user') == ME_SLACK]
        my_last = max([float(x['ts']) for x in mine] or [0])
        after_me = [x for x in msgs if float(x['ts']) > my_last and x.get('user') != ME_SLACK]
        if not after_me:
            last_mine = max(mine, key=lambda y: float(y['ts'])) if mine else None
            if not (last_mine and DEFER_RE.search(last_mine.get('text', ''))):
                continue
            # the promise itself, plus the message it answered, so the report can show both
            before = [x for x in msgs if float(x['ts']) < my_last and x.get('user') != ME_SLACK]
            after_me = before[-1:]
        key = f'slack:{ch}:{tts}'
        is_dm = ch.startswith('D') or ch.startswith('G') or cname.startswith('mpdm-')
        if ch.startswith('D'):
            title = f'DM with {uname(cname)}' if re.match(r'^U[A-Z0-9]+$', cname) else f'DM {cname}'
        elif cname.startswith('mpdm-'):
            title = 'group DM'
        else:
            title = f'#{cname}'
        for x in after_me + ([max(mine, key=lambda y: float(y['ts']))] if mine else []):
            mine_msg = x.get('user') == ME_SLACK
            text = x.get('text', '')
            out.add(source='slack', conversation_key=key, title=title,
                    actor=uname(x['user']) if x.get('user') else (x.get('username') or 'bot'),
                    actor_id=x.get('user') or x.get('bot_id', ''), ts=x['ts'], text=text,
                    link=f"{SLACK_HOST}/archives/{ch}/p{x['ts'].replace('.', '')}?thread_ts={tts}",
                    why_me='mention' if f'<@{ME_SLACK}>' in text else ('dm' if is_dm else 'thread_participant'),
                    is_self=mine_msg, is_bot=bool(x.get('bot_id')) and not x.get('user'))

    # Replies posted to the channel instead of the thread. Someone answers the user's
    # top-level post with another top-level post and no @-mention: conversations.replies
    # never sees it and the mention ledger never sees it. Read the channel after the
    # user's latest top-level post. In a DM everything after it is addressed to the user;
    # in a channel only the next 24h count, and the core's addressed-to-other rule and the
    # label pass sort a reply from unrelated chatter.
    side = 0
    for ch, (cname, my_ts) in sorted(toplevel.items(), key=lambda kv: -kv[1][1]):
        if time.time() - started > budget_s:
            out.complete = False
            out.notes.append('time budget hit before every channel was read for out-of-thread replies')
            break
        is_dm = ch.startswith('D') or ch.startswith('G') or cname.startswith('mpdm-')
        latest = None if is_dm else f'{my_ts + 86400:.6f}'
        r = sl.api('conversations.history', channel=ch, oldest=f'{my_ts:.6f}', inclusive='false',
                   limit=50, **({'latest': latest} if latest else {}))
        msgs = [x for x in r.get('messages', [])
                if x.get('user') != ME_SLACK and not x.get('subtype')
                and (not x.get('thread_ts') or x.get('thread_ts') == x.get('ts'))]
        if not msgs:
            continue
        key = _slack_key(ch, f'{my_ts:.6f}', None) if ch.startswith('D') else f'slack:{ch}:{my_ts:.6f}'
        if ch.startswith('D'):
            title = f'DM with {uname(cname)}' if re.match(r'^U[A-Z0-9]+$', cname) else f'DM {cname}'
        else:
            title = 'group DM' if cname.startswith('mpdm-') else f'#{cname}'
        for x in sorted(msgs, key=lambda y: float(y['ts']))[:20]:
            text = x.get('text', '')
            out.add(source='slack', conversation_key=key, title=title,
                    actor=uname(x['user']) if x.get('user') else (x.get('username') or 'bot'),
                    actor_id=x.get('user') or x.get('bot_id', ''), ts=x['ts'], text=text,
                    link=f"{SLACK_HOST}/archives/{ch}/p{x['ts'].replace('.', '')}",
                    why_me='mention' if f'<@{ME_SLACK}>' in text else ('dm' if is_dm else 'thread_participant'),
                    is_self=False, is_bot=bool(x.get('bot_id')) and not x.get('user'))
            side += 1
        out.add(source='slack', conversation_key=key, title=title, actor='you', actor_id=ME_SLACK,
                ts=f'{my_ts:.6f}', text='(your top-level post)', is_self=True)
    out.notes.append(f'{len(threads)} threads, {side} out-of-thread replies, '
                     f'{sl.calls} API calls, {sl.rate_waits} rate-limit waits')

# ------------------------------------------------------------------- Gmail --

JIRA_KEY_RE = re.compile(r'\b([A-Z][A-Z0-9]{1,9}-\d{1,6})\b')
CAL_SUBJ_RE = re.compile(r'^(invitation|updated invitation|accepted|declined|tentatively accepted|'
                         r'canceled event|cancelled event|invitation from google calendar)\b', re.I)

def _gmail_service():
    sys.path.insert(0, os.path.join(SKILLS, 'gmail-connector'))
    cwd = os.getcwd()
    os.chdir(os.path.join(SKILLS, 'gmail-connector'))
    try:
        import gmail_manager                                   # type: ignore
        from googleapiclient.discovery import build            # type: ignore
        return build('gmail', 'v1', credentials=gmail_manager.authenticate(), cache_discovery=False)
    finally:
        os.chdir(cwd)

ESIGN_RE = r'docusign\.net|echosign|adobesign|hellosign|dropboxsign|pandadoc'

def _body(svc, mid):
    """Plain text of one message, for the doc link a comment notification carries."""
    import base64
    try:
        full = svc.users().messages().get(userId='me', id=mid, format='full').execute()
    except Exception:                                          # noqa: BLE001
        return ''
    out = []

    def walk(part):
        data = (part.get('body') or {}).get('data')
        if data:
            out.append(base64.urlsafe_b64decode(data + '===').decode('utf-8', 'ignore'))
        for p in part.get('parts') or []:
            walk(p)
    walk(full.get('payload') or {})
    return '\n'.join(out)

def _hdr(msg, name):
    return next((h['value'] for h in msg.get('payload', {}).get('headers', [])
                 if h['name'].lower() == name.lower()), '')

def adapter_gmail(out, days):
    svc = _gmail_service()
    out.coverage_days = days
    me = svc.users().getProfile(userId='me').execute()['emailAddress'].lower()
    tids, token = [], None
    while len(tids) < 200:
        res = svc.users().threads().list(userId='me', q=f'newer_than:{days}d in:inbox -from:me',
                                         maxResults=100, pageToken=token).execute()
        tids += [t['id'] for t in res.get('threads', [])]
        token = res.get('nextPageToken')
        if not token:
            break
    if token:
        out.complete = False
        out.notes.append('more than 200 threads; oldest not read')
    for tid in tids:
        th = svc.users().threads().get(userId='me', id=tid, format='metadata',
                                       metadataHeaders=['From', 'Subject', 'List-Unsubscribe', 'To', 'Cc']).execute()
        for msg in th.get('messages', []):
            frm = _hdr(msg, 'From')
            email = (re.search(r'<([^>]+)>', frm) or re.search(r'(\S+@\S+)', frm) or [None, frm])[1].lower()
            name = re.sub(r'\s*<[^>]+>', '', frm).strip().strip('"') or email
            subj = _hdr(msg, 'Subject')
            snippet = msg.get('snippet', '')
            labels = msg.get('labelIds', [])
            cat = next((l.split('_', 1)[1].lower() for l in labels if l.startswith('CATEGORY_')
                        and l != 'CATEGORY_PERSONAL'), '')
            row = dict(source='gmail', conversation_key=f'gmail:{tid}', actor=name, actor_id=email,
                       ts=int(msg.get('internalDate', 0)) / 1000, title=subj, text=snippet,
                       link=f'https://mail.google.com/mail/u/0/#all/{tid}',
                       why_me='dm' if me in _hdr(msg, 'To').lower() else 'cc', is_self=email == me,
                       meta={'category': cat, 'list_unsubscribe': bool(_hdr(msg, 'List-Unsubscribe'))})
            low = email
            if 'atlassian.net' in low or low.startswith('jira@'):
                keym = JIRA_KEY_RE.search(subj + ' ' + snippet)
                who = re.match(r'^(.+?) (mentioned you|commented|assigned|changed|updated|created)', snippet or subj)
                if 'confluence' in low or '/wiki/' in snippet or 'mentioned you in a page' in subj.lower() \
                        or 'confluence' in name.lower():
                    pm = re.search(r'mentioned you (?:in|on) "([^"]+)"', snippet)
                    ptitle = pm.group(1) if pm else re.sub(r'^.*?:\s*', '', subj)
                    row.update(collapse_into=title_key('confluence', ptitle), source='confluence', title=ptitle)
                elif keym:
                    row.update(collapse_into=f'jira:{keym.group(1)}', source='jira')
                row.update(actor=who.group(1) if who else name,
                           why_me='mention' if 'mentioned you' in (subj + snippet).lower() else 'watcher',
                           meta={**row['meta'], 'notification': True, 'list_unsubscribe': False})
            elif 'comments-noreply@docs.google.com' in low:
                t = re.sub(r'^(document|spreadsheet|presentation)\s*:\s*', '', subj, flags=re.I)
                who = re.match(r'^(.+?) (mentioned you|assigned|replied|commented|added)', snippet)
                key, aliases = doc_keys(t, _body(svc, msg['id']))
                row.update(source='gdocs', collapse_into=key, aliases=aliases, title=t,
                           actor=who.group(1) if who else name,
                           why_me='action_item' if 'assigned' in snippet.lower() else 'doc_comment',
                           meta={**row['meta'], 'notification': True, 'list_unsubscribe': False})
            elif re.search(ESIGN_RE, low):
                # a signature request is an ask with a deadline, whatever tab Gmail filed it in
                row.update(why_me='signature_request',
                           meta={**row['meta'], 'notification': True, 'list_unsubscribe': False})
            elif 'drive-shares' in low or re.search(r'share request|requesting access|request(ed)? access', subj, re.I):
                row.update(source='drive', why_me='share_request',
                           meta={**row['meta'], 'notification': True, 'list_unsubscribe': False})
            elif CAL_SUBJ_RE.search(subj):
                # covered by the calendar adapter, which reads the RSVP state itself
                row.update(meta={**row['meta'], 'kind': 'calendar_mail'}, why_me='notification')
            out.add(**row)
    out.notes.append(f'{len(tids)} inbox threads, {days}d')
    return

# -------------------------------------------------------------------- Jira --

def _jira():
    sys.path.insert(0, os.path.join(SKILLS, 'jira-connector', 'scripts'))
    import jira_client                                         # type: ignore
    return jira_client

def _adf_mentions(node, acc):
    if isinstance(node, dict):
        if node.get('type') == 'mention':
            acc.add(node.get('attrs', {}).get('id'))
        for v in node.values():
            _adf_mentions(v, acc)
    elif isinstance(node, list):
        for v in node:
            _adf_mentions(v, acc)
    return acc

def adapter_jira(out, days, extra_keys=()):
    jc = _jira()
    import requests                                            # type: ignore
    sites = sorted(set(jc.KEY_DOMAINS.values()))
    out.coverage_days = days
    cut = time.time() - days * 86400
    for site in sites:
        try:
            me = requests.get(f'https://{site}/rest/api/3/myself', headers=jc.HEADERS,
                              auth=jc.AUTH, timeout=20).json()['accountId']
            keys = [k for k in extra_keys if jc.KEY_DOMAINS.get(k.split('-')[0]) == site]
            jql = (f'(assignee = currentUser() OR reporter = currentUser() OR watcher = currentUser()'
                   + (f" OR key in ({','.join(keys)})" if keys else '')
                   + f') AND updated >= -{days}d ORDER BY updated DESC')
            _, issues = jc.search_issues(jql, domain=site, limit=80,
                                         fields=['summary', 'status', 'assignee', 'reporter',
                                                 'created', 'comment', 'watches'])
        except Exception as e:                                 # noqa: BLE001
            out.notes.append(f'{site}: {str(e)[:160]}')
            out.complete = False
            continue
        for iss in issues:
            f = iss['fields']
            key = iss['key']
            link = f'https://{site}/browse/{key}'
            role = ('assigned' if (f.get('assignee') or {}).get('accountId') == me else
                    'reporter' if (f.get('reporter') or {}).get('accountId') == me else 'watcher')
            base = dict(source='jira', conversation_key=f'jira:{key}', title=f"{key} {f.get('summary', '')}",
                        link=link)
            if role == 'assigned' and jc_ts(f.get('created')) > cut:
                rep = f.get('reporter') or {}
                out.add(**base, actor=rep.get('displayName', '?'), actor_id=rep.get('accountId'),
                        ts=f.get('created'), text=f"New ticket assigned to you: {f.get('summary', '')}",
                        why_me='assigned', is_self=rep.get('accountId') == me)
            for c in (f.get('comment') or {}).get('comments', []):
                if jc_ts(c.get('created')) < cut and c.get('author', {}).get('accountId') != me:
                    continue
                aid = c.get('author', {}).get('accountId')
                mentioned = me in _adf_mentions(c.get('body'), set())
                out.add(**{**base, 'link': f"{link}?focusedCommentId={c.get('id')}"},
                        actor=c.get('author', {}).get('displayName', '?'), actor_id=aid,
                        ts=c.get('created'), text=jc._adf_to_text(c.get('body'))[:1500],
                        why_me='mention' if mentioned else role, is_self=aid == me)
        out.notes.append(f'{site}: {len(issues)} issues')

def jc_ts(s):
    import datetime as dt
    if not s:
        return 0
    try:
        return dt.datetime.strptime(s[:19] + s[23:].replace(':', ''), '%Y-%m-%dT%H:%M:%S%z').timestamp()
    except ValueError:
        return 0

# ---------------------------------------------------------------- Calendar --

def adapter_calendar(out, days):
    sys.path.insert(0, os.path.join(SKILLS, 'google-calendar-connector'))
    import gcal_manager                                        # type: ignore
    from googleapiclient.discovery import build                # type: ignore
    import datetime as dt
    svc = build('calendar', 'v3', credentials=gcal_manager.authenticate(CALENDAR_PROFILE), cache_discovery=False)
    out.coverage_days = 36500      # every invite still waiting is reported, however old
    now = dt.datetime.now(dt.timezone.utc)
    ev = svc.events().list(calendarId='primary', timeMin=now.isoformat(),
                           timeMax=(now + dt.timedelta(days=14)).isoformat(), singleEvents=True,
                           orderBy='startTime', maxResults=250).execute().get('items', [])
    n = 0
    for e in ev:
        me = next((a for a in e.get('attendees', []) if a.get('self')), None)
        if not me or me.get('organizer') or (e.get('organizer') or {}).get('self'):
            continue
        if me.get('responseStatus') != 'needsAction':
            continue
        n += 1
        start = (e.get('start') or {}).get('dateTime') or (e.get('start') or {}).get('date')
        org = e.get('organizer') or {}
        series = e.get('recurringEventId')
        out.add(source='calendar', conversation_key=f"cal:{series or e['id']}",
                title=e.get('summary', '(no title)') + (' (recurring)' if series else ''),
                actor=org.get('displayName') or org.get('email', '?'), actor_id=org.get('email'),
                ts=e.get('updated'), text=f"Invite without your RSVP: {e.get('summary', '')} at {start}",
                link=e.get('htmlLink', ''), why_me='rsvp')
    out.notes.append(f'{len(ev)} events next 14d, {n} need an RSVP')

# ------------------------------------------------------------------ Linear --

def adapter_linear(out, days):
    """Notifications (read or unread: reading one in the app is not answering it) plus
    every issue the owner is assigned, created or subscribed to that moved in the window,
    with its comments, so a reply after his own comment surfaces like a Slack thread."""
    sys.path.insert(0, os.path.join(SKILLS, 'linear-connector', 'scripts'))
    import linear_client                                       # type: ignore
    q = '''{ viewer { id displayName } notifications(first: 100) { nodes {
      type createdAt readAt actor { id name }
      ... on IssueNotification { issue { identifier title url }
        comment { body user { id name } externalUser { name } } } } } }'''
    data = linear_client.gql(q)
    me = data['viewer']['id']
    at_me = '@' + (data['viewer'].get('displayName') or '(unnamed)').lower()
    out.coverage_days = days
    cut = time.time() - days * 86400
    for n in data['notifications']['nodes']:
        iss = n.get('issue')
        if not iss:
            continue
        ts = jc_ts(n['createdAt'].replace('Z', '+0000')) if 'Z' in n['createdAt'] else jc_ts(n['createdAt'])
        if ts and ts < cut:
            continue
        typ = n.get('type', '')
        cm = n.get('comment') or {}
        who = n.get('actor') or cm.get('user') or cm.get('externalUser')
        if not who:
            continue                  # Linear's own bots (Jira sync errors, welcome): no person waiting
        out.add(source='linear', conversation_key=f"linear:{iss['identifier']}",
                title=f"{iss['identifier']} {iss['title']}", actor=who.get('name', '?'),
                actor_id=who.get('id'), ts=n['createdAt'],
                text=((n.get('comment') or {}).get('body') or typ)[:1500], link=iss['url'],
                why_me='mention' if 'Mention' in typ else 'assigned' if 'Assigned' in typ else 'watcher',
                is_self=who.get('id') == me)
    q = '''query($since: DateTimeOrDuration!) { issues(first: 80, orderBy: updatedAt, filter: {
      updatedAt: { gt: $since }, or: [ { assignee: { isMe: { eq: true } } },
        { creator: { isMe: { eq: true } } }, { subscribers: { isMe: { eq: true } } } ] }) { nodes {
      identifier title url createdAt assignee { id } creator { id name }
      comments(first: 30) { nodes { body createdAt url user { id name } externalUser { name } } } } } }'''
    issues = linear_client.gql(q, {'since': f'-P{days}D'})['issues']['nodes']
    for iss in issues:
        key = iss['identifier']
        role = ('assigned' if (iss.get('assignee') or {}).get('id') == me else
                'reporter' if (iss.get('creator') or {}).get('id') == me else 'watcher')
        base = dict(source='linear', conversation_key=f'linear:{key}', title=f"{key} {iss['title']}")
        cr = iss.get('creator') or {}
        if role == 'assigned' and jc_ts(iss['createdAt']) > cut and cr.get('id') != me:
            out.add(**base, link=iss['url'], actor=cr.get('name', '?'), actor_id=cr.get('id'),
                    ts=iss['createdAt'], text=f"New ticket assigned to you: {iss['title']}", why_me='assigned')
        for c in (iss.get('comments') or {}).get('nodes', []):
            u = c.get('user') or c.get('externalUser')
            if not u:
                continue              # bot comment (Jira sync error and the like)
            if jc_ts(c['createdAt']) < cut and u.get('id') != me:
                continue
            body = c.get('body') or ''
            out.add(**base, link=c.get('url') or iss['url'], actor=u.get('name', '?'), actor_id=u.get('id'),
                    ts=c['createdAt'], text=body[:1500], is_self=u.get('id') == me,
                    why_me='mention' if at_me in body.lower() else role)
    out.notes.append(f"{len(data['notifications']['nodes'])} notifications, {len(issues)} issues")

# ------------------------------------------------------------------ GitHub --

def adapter_github(out, days):
    r = subprocess.run(['gh', 'search', 'prs', '--review-requested=@me', '--state=open',
                        '--json', 'url,title,author,updatedAt,repository', '--limit', '30'],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:200] or 'gh failed')
    out.coverage_days = 36500      # every open review request is reported
    for pr in json.loads(r.stdout or '[]'):
        out.add(source='github', conversation_key=f"gh:{pr['url']}", title=pr['title'],
                actor=(pr.get('author') or {}).get('login', '?'), ts=pr['updatedAt'],
                text=f"Review requested: {pr['title']} ({pr['repository']['nameWithOwner']})",
                link=pr['url'], why_me='review_request')

ADAPTERS = {
    'slack_ledger': adapter_slack_ledger, 'slack_threads': adapter_slack_threads,
    'gmail': adapter_gmail, 'jira': adapter_jira, 'calendar': adapter_calendar,
    'linear': adapter_linear, 'github': adapter_github,
}

def main():
    p = argparse.ArgumentParser()
    p.add_argument('adapter', choices=sorted(ADAPTERS))
    p.add_argument('--out', required=True)
    p.add_argument('--days', type=int, default=None)
    p.add_argument('--extra-keys', default='', help='jira: comma list of keys seen in notification mail')
    a = p.parse_args()
    out = Out(a.out, a.adapter)
    days = a.days or {'slack_threads': 10, 'gmail': 3, 'jira': 3, 'linear': 7,
                      'calendar': 14, 'github': 30, 'slack_ledger': 30}[a.adapter]
    if a.adapter.startswith('slack') and not (ME_SLACK and SLACK_HOST):
        try:
            Slack()                   # fills ME_SLACK and SLACK_HOST from auth.test
        except SystemExit:
            pass
    try:
        if a.adapter == 'jira':
            adapter_jira(out, days, [k for k in a.extra_keys.split(',') if k])
        else:
            ADAPTERS[a.adapter](out, days)
        out.write('ok')
    except SystemExit as e:
        out.notes.append(f"not configured: {str(e).splitlines()[0] if str(e) else 'missing credentials'}")
        out.write('skipped')
    except Exception as e:                                     # noqa: BLE001
        out.notes.append(f'{type(e).__name__}: {str(e)[:200]}')
        out.write('failed')
        sys.exit(2)

if __name__ == '__main__':
    main()
