#!/usr/bin/env python3
"""
sweep_core.py - source-agnostic sweep engine: collapse, auto-close, noise filter, rank, render.

Standard library only, one file, so the same code runs in the PSB harness (fed by the
script adapters in adapters_psb.py) and in an ASB member workspace (fed by items the
agent collects through its connected tools and writes as JSONL).

Pipeline
  adapters -> items.jsonl -> [collapse by conversation] -> [auto-close: the user acted
  after the last inbound message] -> [deterministic noise rules] -> survivors.json
  -> (optional model pass writes labels.json) -> ranked report.md + state

Item contract (one JSON object per line; unknown fields are kept in `meta`):
  source            slack | gmail | jira | confluence | linear | gdocs | drive | calendar |
                    meetings | github | <any adapter name>
  conversation_key  one id per conversation: "slack:<channel>:<thread_ts>", "jira:<KEY>",
                    "gmail:<threadId>", "gdoc:id:<docId>", "cal:<eventId>". Everything with
                    the same key is ONE item in the output.
  aliases           optional list of other keys that name the same conversation (a doc by
                    title and by id). Keys joined by an alias collapse into one item.
  actor             display name of whoever wrote this message
  actor_id          optional stable id (Slack user id, email address, account id)
  ts                epoch seconds (number or numeric string) or ISO 8601
  text              message text (plain)
  link              URL back to the original
  why_me            mention | dm | assigned | reporter | watcher | thread_participant |
                    doc_comment | share_request | signature_request | rsvp |
                    review_request | action_item | cc | notification
  is_self           true when the USER wrote it (a reply, a comment, an RSVP). Self
                    events never surface; they close the conversation.
  is_bot            true for bots and notifier apps
  title             optional conversation title (email subject, ticket summary)
  collapse_into     optional conversation_key this item belongs to instead (a Jira
                    notification email collapses into jira:<KEY>)
  meta              optional dict: category, list_unsubscribe, notification, kind, ...

Health contract (one row per adapter, --health): adapter, source, status (ok | skipped |
failed), items, note, coverage_days (how far back the adapter reads), complete (false
when it hit a budget or page cap). An open item that an ok, complete adapter no longer
reports inside its coverage window was resolved at the source and closes. Anything
else is carried forward, so an item never vanishes just because a window moved on.

Commands
  run     --items F [F ...] --state S --filter C --out DIR [--health H]
  label   --state S --labels L --out DIR          apply a model pass, re-render
  dismiss --state S --key K [--reason R]          user says: not for me
  teach   --filter C (--allow X | --deny X | --vip X | --deny-conversation X)
  eval    --labelled L --filter C                  score the rule layer on a labelled set
"""

import argparse
import contextlib
import datetime as _dt
import html
import json
import os
import re
import sys
import tempfile
import time

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8')
    except (AttributeError, ValueError):
        pass

VERSION = 2

DEFAULT_FILTER = {
    # Who the user is, per source. Anything listed here is treated as is_self.
    'me': {'ids': [], 'emails': [], 'names': []},
    # Senders never filtered (substring match on actor, actor_id, or email).
    'allow_senders': [],
    # Senders always filtered (substring match). Only automated senders by default;
    # `teach --deny` adds to it.
    'deny_senders': ['noreply', 'no-reply', 'do-not-reply', 'donotreply', 'mailer-daemon'],
    # Senders filtered ONLY when the mail also carries a bulk signal (List-Unsubscribe or a
    # non-primary Gmail tab). A real person at marketing@client.com writing to you directly
    # stays; the newsletter from the same address goes.
    'bulk_senders': ['newsletter', 'marketing@', 'digest@', 'news@', 'hello@', 'community@',
                     'info@', 'updates@', 'team@', 'notifications@'],
    # Notifier senders that carry a REAL signal (a mention, a comment, a share request, a
    # signature). These are exempt from the sender and bulk rules.
    'signal_senders': [
        'jira@', '@atlassian.net', 'confluence@', 'comments-noreply@docs.google.com',
        'drive-shares-dm-noreply@google.com', 'drive-shares-noreply@google.com',
        'calendar-notification@google.com', 'notifications@linear.app',
        'notifications@github.com', 'docusign.net', 'echosign', 'adobesign',
        'hellosign', 'dropboxsign', 'pandadoc',
    ],
    # Subject or text patterns that mark marketing / cold outreach / automated mail.
    'deny_patterns': [
        r'\bprompts? made for you\b', r'\bwebinar\b', r'\bnewsletter\b',
        r'\bexport (is )?ready\b', r'\breacted to your (message|email)\b',
        r'\byour (weekly|daily|monthly) (digest|summary|report)\b',
        r'\bexclusive (offer|invite)\b', r'\bfree trial\b', r'\bremember to respond\b',
    ],
    # People whose messages rank first (name or id substring).
    'vip': [],
    # A thread/watcher item with no mention and no question goes stale after this many days.
    'stale_days': 5,
    # A surfacing item older than this moves to the collapsed "Older" list (VIPs exempt).
    'aging_days': 7,
    # Anything unanswered this long expires, with one line saying so.
    'hard_stale_days': 21,
    # The top of the report shows at most this many items; the rest sit under "More".
    'top_n': 7,
    # Propose a mute after this many dismissals, or this many expiries, with no reply ever.
    'mute_after_dismissals': 3,
    'mute_after_expiries': 5,
    # Channels / conversation-key prefixes that are pure firehoses.
    'deny_conversations': [],
}

ACK_PHRASES = [
    'thank you', 'thanks', 'thankyou', 'thx', 'ty', 'tq', 'makasih', 'terima kasih',
    'much appreciated', 'appreciate it', 'no worries', 'no problem', 'np',
    'sounds good', 'sound good', 'got it', 'noted', 'will do', 'ok', 'okay', 'oke', 'okey',
    'k', 'kk', 'sure', 'perfect', 'great', 'awesome', 'nice', 'cool', 'welcome',
    "you're welcome", 'you are welcome', 'exactly', 'agreed', 'confirmed', 'roger',
    'copy that', 'done', 'cheers', 'siap', 'sip', 'mantap', 'baik', 'yes', 'yep', 'yup',
    'alright', 'on it', 'lgtm', 'will check', 'checking', 'sure thing', 'thanks a lot',
    'thank u', 'jazakallah khair', 'jazakallah',
    'shukran', 'wow', 'haha', 'hahaha', 'lol', 'noted with thanks', 'amazing',
]
_ACK_SET = set(ACK_PHRASES)

CAL_REPLY_RE = re.compile(r'^\s*(accepted|declined|tentatively accepted|tentative)\s*:', re.I)
QUESTION_RE = re.compile(r'\?|\b(can you|could you|would you|please|pls|plz|kindly|need your|'
                         r'waiting (on|for) you|any update|thoughts|approve|review|confirm|'
                         r'let me know|lmk|bisa|tolong|mohon|gimana|kapan)\b', re.I)
# The user's own reply that defers rather than answers: the loop is still open, on them.
DEFER_RE = re.compile(r"\b(will (check|revert|get back|look|update|share|send|confirm)|let me (check|see|look)|"
                      r"i'?ll (check|get back|revert|look|update|share|send|confirm)|get back to you|"
                      r"tomorrow|later today|by (eod|end of day|monday|tuesday|wednesday|thursday|friday)|"
                      r"nanti|besok|aku cek|saya cek|ku cek|coba cek)\b", re.I)
EMOJI_ONLY_RE = re.compile(r'^(\s|:[a-z0-9_+\-]+:|[\U0001f000-\U0001faff☀-➿️‍])*$')
MENTION_TOKEN_RE = re.compile(r'<@([A-Z0-9]+)>')
THANKS_RE = re.compile(r"\b(thanks?|thank you|thx|makasih|terima kasih|appreciated?|shukran|jazakallah)\b")

WHY_WEIGHT = {
    'review_request': 30, 'share_request': 30, 'signature_request': 30, 'dm': 28, 'mention': 26,
    'assigned': 22, 'doc_comment': 22, 'action_item': 20, 'rsvp': 16, 'thread_participant': 14,
    'reporter': 12, 'cc': 10, 'watcher': 8, 'notification': 6,
}
DIRECT_WHY = {'dm', 'mention', 'assigned', 'review_request', 'share_request', 'signature_request',
              'rsvp', 'doc_comment', 'action_item'}
INDIRECT_WHY = {'cc', 'watcher', 'reporter', 'notification'}
LABEL_WEIGHT = {'blocking_someone': 40, 'needs_decision': 32, 'needs_reply': 24,
                'fyi': 0, 'noise': -100}
ACTIONABLE_LABELS = {'blocking_someone', 'needs_decision', 'needs_reply'}
ACTIVE = ('open', 'aging', 'promised')

# ------------------------------------------------------------------ helpers --

def now():
    return time.time()

def parse_ts(v):
    if v is None or v == '':
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    try:
        return float(s)
    except ValueError:
        pass
    try:
        return _dt.datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()
    except ValueError:
        return 0.0

def age_str(ts):
    d = max(0, now() - ts)
    if d < 3600:
        return f'{int(d // 60)}m'
    if d < 86400:
        return f'{int(d // 3600)}h'
    return f'{int(d // 86400)}d'

def load_json(path, default):
    if path and os.path.exists(path):
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    return default

def save_json(path, data):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix='.tmp-', suffix='.json')
    with os.fdopen(fd, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)

@contextlib.contextmanager
def state_lock(path, wait_s=90, stale_s=900):
    """One writer at a time: a cron sweep and a hand-run sweep on the same state file
    would otherwise each overwrite the other's result."""
    lock = os.path.abspath(path) + '.lock'
    os.makedirs(os.path.dirname(lock), exist_ok=True)
    deadline = now() + wait_s
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except FileExistsError:
            try:
                if now() - os.path.getmtime(lock) > stale_s:
                    os.remove(lock)           # a crashed run left it behind
                    continue
            except OSError:
                continue
            if now() > deadline:
                sys.exit(f'state is locked by another sweep ({lock}); try again in a minute')
            time.sleep(1)
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            os.remove(lock)

def load_filter(path):
    cfg = json.loads(json.dumps(DEFAULT_FILTER))
    user = load_json(path, {})
    for k, v in user.items():
        if isinstance(v, list) and isinstance(cfg.get(k), list):
            cfg[k] = list(dict.fromkeys(cfg[k] + v))     # extend defaults, keep order
        elif isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k] = {**cfg[k], **v}
        else:
            cfg[k] = v
    cfg.setdefault('_healthy', None)                     # None = unknown, trust the rules
    return cfg

def read_items(paths):
    items = []
    for p in paths:
        with open(p, encoding='utf-8') as fh:
            for n, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError as e:
                    print(f'warn: {p}:{n} skipped, bad JSON ({e})', file=sys.stderr)
    return items

def _hay(it):
    return ' '.join(str(it.get(k) or '') for k in ('actor', 'actor_id', 'email')).lower()

def _match_any(hay, needles):
    return next((n for n in needles if n and n.lower() in hay), None)

def _clean(text):
    t = re.sub(r'<[^>]+>', ' ', text or '')
    t = re.sub(r'https?://\S+', ' ', t)
    return re.sub(r'\s+', ' ', t).strip()

def norm_title(t):
    """One spelling per doc title: Slack sends '&amp;' and curly quotes, Gmail does not."""
    t = html.unescape(t or '').replace('’', "'").replace('‘', "'")
    t = t.replace('“', '"').replace('”', '"').replace('–', '-').replace('—', '-')
    t = re.sub(r'^(document|spreadsheet|presentation|file)\s*:\s*', '', t.strip(), flags=re.I)
    return re.sub(r'\s+', ' ', t).strip(' ."').lower()[:120]

def is_self(it, cfg):
    if it.get('is_self'):
        return True
    me = cfg.get('me', {})
    aid = str(it.get('actor_id') or '').lower()
    ids = {str(x).lower() for x in me.get('ids', []) + me.get('emails', [])}
    return bool(aid and aid in ids)

def names_me(text, cfg):
    me = cfg.get('me', {})
    tagged = set(MENTION_TOKEN_RE.findall(text or ''))
    if tagged & {str(x) for x in me.get('ids', [])}:
        return True
    return any(n and re.search(rf'\b{re.escape(n)}\b', text or '', re.I) for n in me.get('names', []))

def healthy(cfg, source):
    h = cfg.get('_healthy')
    return True if h is None else source in h

# ------------------------------------------------------------- noise rules --

def is_ack(text, names=()):
    t = _clean(text).lower()
    squeezed = re.sub(r'([a-z])\1{2,}', r'\1', t)        # "thannnnk yoooou" -> "thank you"
    if squeezed != t:
        return is_ack(squeezed, names)
    t = re.sub(r':[a-z0-9_+\-]+:', ' ', t).strip()          # emoji codes carry no ask
    if not t:
        return False
    if '?' in t:
        return False
    letters = re.sub(r'[^a-z\s\']', ' ', t)
    letters = re.sub(r'\s+', ' ', letters).strip()
    if not letters:
        return False
    if len(letters) <= 40 and letters in _ACK_SET:
        return True
    words = letters.split()
    # A short thank-you that carries no ask ("all works now bro, thanks!",
    # "Thanks @Dita for clarification") closes a loop; it does not open one.
    if len(words) <= 8 and THANKS_RE.search(letters) and not QUESTION_RE.search(t):
        return True
    if len(words) > 6:
        return False
    # every word belongs to some ack phrase ("ok thanks", "noted thank you")
    ack_words = {w for p in _ACK_SET for w in p.split()} | {'bro', 'all', 'so', 'much', 'a', 'lot',
                                                            'very', 'guys', 'team'} | set(names)
    return all(w in ack_words for w in words)

def message_noise(it, cfg):
    """Rule name when this single inbound message is noise, else None. Order matters:
    the allow list beats everything, a signal sender beats the sender rules."""
    hay = _hay(it)
    text = it.get('text') or ''
    title = it.get('title') or ''
    meta = it.get('meta') or {}
    if _match_any(hay, cfg['allow_senders']):
        return None
    signal = _match_any(hay, cfg['signal_senders'])
    cat = str(meta.get('category') or '').lower()
    bulk = bool(meta.get('list_unsubscribe')) or cat in ('promotions', 'social', 'forums', 'updates')
    if it.get('is_bot') and not signal:
        return 'bot'
    if not signal:
        hit = _match_any(hay, cfg['deny_senders'])
        if hit:
            return f'deny_sender:{hit}'
        hit = _match_any(hay, cfg['bulk_senders'])
        if hit and bulk:
            return f'bulk_sender:{hit}'
    # "Covered by another source" only holds when that source actually ran this time.
    if meta.get('kind') == 'calendar_mail' and healthy(cfg, 'calendar'):
        return 'calendar_mail_covered_by_calendar_source'
    if meta.get('notification') and it.get('why_me') == 'watcher' and healthy(cfg, it.get('source')):
        return 'watch_notification_covered_by_source'
    if meta.get('kind') == 'reaction' or re.search(r'\breacted\b.*\bto your\b', title + ' ' + text, re.I):
        return 'reaction'
    if CAL_REPLY_RE.search(title) or CAL_REPLY_RE.search(text[:40]):
        return 'calendar_reply'
    if cat in ('promotions', 'social', 'forums') and not signal:
        return f'gmail_category:{cat}'
    for pat in cfg['deny_patterns']:
        if re.search(pat, title + '\n' + text, re.I) and not signal:
            return f'deny_pattern:{pat}'
    if meta.get('list_unsubscribe') and not signal:
        return 'bulk_mail'
    if text and EMOJI_ONLY_RE.match(text):
        return 'emoji_only'
    if is_ack(text, [n.lower() for n in cfg.get('me', {}).get('names', [])]):
        return 'ack'
    # A thread reply that tags someone else and not the user is addressed to them.
    if it.get('why_me') in ('thread_participant', 'watcher'):
        if MENTION_TOKEN_RE.search(text) and not names_me(text, cfg):
            return 'addressed_to_other'
    return None

# ----------------------------------------------------------------- collapse --

def _canonical_keys(items):
    """Union every key with its aliases so one conversation reported under two names (a
    doc by title from Slack, by id from Gmail) becomes one item. Id-style keys win."""
    parent = {}

    def find(k):
        parent.setdefault(k, k)
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        # prefer the id-style key as the root
        if ':id:' in rb and ':id:' not in ra:
            ra, rb = rb, ra
        parent[rb] = ra

    for it in items:
        k = it.get('collapse_into') or it.get('conversation_key')
        if not k:
            continue
        find(k)
        for al in it.get('aliases') or []:
            union(k, al)
    return find

def collapse(items, cfg):
    find = _canonical_keys(items)
    convs = {}
    for it in items:
        key = it.get('collapse_into') or it.get('conversation_key')
        key = find(key) if key else f"{it.get('source', '?')}:{it.get('link') or it.get('ts')}"
        it['_ts'] = parse_ts(it.get('ts'))
        it['_self'] = is_self(it, cfg)
        c = convs.setdefault(key, {'key': key, 'msgs': [], 'sources': set(), 'links': [],
                                   'why': set(), 'title': ''})
        c['msgs'].append(it)
        c['sources'].add(it.get('source', '?'))
        if it.get('link') and it['link'] not in c['links']:
            # the conversation's own link first; a notification email's link after it
            if it.get('collapse_into'):
                c['links'].append(it['link'])
            else:
                c['links'].insert(0, it['link'])
        if it.get('why_me') and not it['_self']:
            c['why'].add(it['why_me'])
        if it.get('title') and (not c['title'] or not it.get('collapse_into')):
            c['title'] = it['title']
    for c in convs.values():
        # identical message delivered by two adapters (ledger + thread pass) counts once
        seen, uniq = set(), []
        for m in sorted(c['msgs'], key=lambda m: m['_ts']):
            sig = (m.get('actor_id') or m.get('actor'), round(m['_ts'], 3), (m.get('text') or '')[:80])
            if sig in seen:
                continue
            seen.add(sig)
            uniq.append(m)
        c['msgs'] = uniq
    return convs

def _pending(conv, cfg):
    """Inbound messages still waiting on the user, and the user's last message.

    Per person, not per thread: when the user's last reply tags specific people, a
    question from someone else in the same stretch is still open. A reply that tags
    nobody is taken to answer the whole thread."""
    msgs = conv['msgs']
    selfs = [m for m in msgs if m['_self']]
    last_self = selfs[-1] if selfs else None
    t_last = last_self['_ts'] if last_self else 0.0
    after = [m for m in msgs if not m['_self'] and m['_ts'] > t_last]
    left_behind = []
    if last_self:
        tagged = set(MENTION_TOKEN_RE.findall(last_self.get('text') or ''))
        if tagged:
            t_prev = selfs[-2]['_ts'] if len(selfs) > 1 else 0.0
            before = [m for m in msgs if not m['_self'] and t_prev < m['_ts'] < t_last]
            for m in before:
                if str(m.get('actor_id') or '') in tagged:
                    continue
                if QUESTION_RE.search(m.get('text') or ''):
                    left_behind.append(m)
    return left_behind + after, last_self, bool(left_behind)

def evaluate(conv, cfg, state_row):
    """Decide one conversation. Returns (verdict, detail); verdict is one of
    closed | promised | dismissed | noise | aging | candidate."""
    inbound, last_self, partial = _pending(conv, cfg)
    conv['last_self'] = last_self
    if not inbound:
        if last_self and DEFER_RE.search(last_self.get('text') or ''):
            return 'promised', 'you said you would come back to it'
        return 'closed', 'you replied or acted after the last message'
    if state_row and state_row.get('status') == 'dismissed' and \
            max(m['_ts'] for m in inbound) <= state_row.get('dismissed_at_ts', 0):
        return 'dismissed', state_row.get('dismiss_reason', 'dismissed by you')
    key = conv['key']
    hit = _match_any(key.lower(), [d.lower() for d in cfg['deny_conversations']])
    if hit:
        return 'noise', f'deny_conversation:{hit}'
    latest = max(m['_ts'] for m in inbound)
    direct = conv['why'] & DIRECT_WHY
    age_days = (now() - latest) / 86400
    if latest and age_days > cfg['hard_stale_days']:
        return 'noise', f'expired:{int(age_days)}d_unanswered'
    asked = any(QUESTION_RE.search(m.get('text') or '') for m in inbound)
    if latest and not direct and not asked and age_days > cfg['stale_days']:
        return 'noise', f'stale:{int(age_days)}d_no_mention_no_question'
    reasons = [message_noise(m, cfg) for m in inbound]
    if all(reasons):
        return 'noise', sorted(set(reasons), key=reasons.index)[0]
    conv['inbound'] = [m for m, r in zip(inbound, reasons) if not r]
    vip = _match_any(' '.join(_hay(m) for m in conv['inbound']), cfg['vip'])
    if latest and age_days > cfg['aging_days'] and not vip:
        return 'aging', f'{int(age_days)}d old'
    return 'candidate', 'partial: your last reply tagged someone else' if partial else None

# --------------------------------------------------------------------- rank --

def score(conv, cfg, label=None):
    s = max([WHY_WEIGHT.get(w, 5) for w in conv['why']] or [5])
    if label:
        s += LABEL_WEIGHT.get(label, 0)
    actors = ' '.join(_hay(m) for m in conv.get('inbound', []))
    if _match_any(actors, cfg['vip']):
        s += 25
    latest = max([m['_ts'] for m in conv.get('inbound', [])] or [now()])
    s += min(6, (now() - latest) / 86400 * 2)           # waiting longer ranks a little higher
    return round(s, 1)

def summarize(conv):
    inbound = conv.get('inbound') or []
    last = inbound[-1] if inbound else conv['msgs'][-1]
    actors = list(dict.fromkeys(m.get('actor') or m.get('actor_id') or '?' for m in inbound))
    actor_ids = list(dict.fromkeys(str(m.get('actor_id') or m.get('actor') or '?') for m in inbound))
    root = conv['msgs'][0]
    you = conv.get('last_self')
    return {
        'key': conv['key'],
        'sources': sorted(conv['sources']),
        'why_me': sorted(conv['why']),
        'title': conv['title'],
        'actors': actors,
        'actor_ids': actor_ids,
        'latest_ts': last['_ts'],
        'age': age_str(last['_ts']) if last['_ts'] else '?',
        'link': conv['links'][0] if conv['links'] else '',
        'extra_links': conv['links'][1:4],
        'text': (last.get('text') or '')[:600],
        'root': '' if root in inbound else f"{root.get('actor')}: {(root.get('text') or '')[:300]}",
        'your_last': (you.get('text') or '')[:300] if you else '',
        'messages': [{'actor': m.get('actor'), 'age': age_str(m['_ts']) if m['_ts'] else '?',
                      'text': (m.get('text') or '')[:400]} for m in inbound[-5:]],
    }

# -------------------------------------------------------------------- state --

def new_state():
    return {'version': VERSION, 'conversations': {}, 'runs': [], 'senders': {}}

def remember(state, key, **kw):
    row = state['conversations'].setdefault(key, {'first_seen': now()})
    row.update(kw)
    row['last_seen'] = now()
    return row

def tally(state, row, field):
    """Per-sender and per-channel counters: how often the user acted on, dismissed, or let
    expire what they sent. The basis for mute suggestions and the acted-on rate."""
    s = state.setdefault('senders', {})
    ids = (row.get('summary') or {}).get('actor_ids') or []
    parts = row.get('key', '').split(':')
    chan = ':'.join(parts[:2]) if parts and parts[0] == 'slack' and len(parts) > 2 else None
    for k in ids[:1] + ([f'channel {chan}'] if chan else []):
        c = s.setdefault(k, {'name': (row.get('summary') or {}).get('actors', [k])[0] if not k.startswith('channel ')
                             else (row.get('summary') or {}).get('title', k), 'acted': 0, 'dismissed': 0, 'expired': 0})
        c[field] = c.get(field, 0) + 1

def prune(state, days=30):
    cut = now() - days * 86400
    convs = state['conversations']
    for k in [k for k, v in convs.items() if v.get('last_seen', 0) < cut]:
        del convs[k]
    state['runs'] = state['runs'][-50:]

def health_view(health):
    """Which sources ran cleanly, and how far back each one can vouch for."""
    by_src = {}
    for h in health:
        src = h.get('source') or str(h.get('adapter', '')).split('_')[0]
        by_src.setdefault(src, []).append(h)
    healthy_src, coverage = set(), {}
    for src, rows in by_src.items():
        if all(r.get('status') == 'ok' for r in rows):
            healthy_src.add(src)
            if all(r.get('complete', False) and r.get('coverage_days') for r in rows):
                coverage[src] = min(float(r['coverage_days']) for r in rows)
    return healthy_src, coverage

# ------------------------------------------------------------------- render --

def _line(v, compact=False):
    s = v['summary']
    who = ', '.join(s['actors'][:3]) or 'you'
    text = re.sub(r'\s+', ' ', re.sub(r'<@?[^>|]*\|?([^>]*)>', r'\1', s['text'])).strip()
    if s['sources'] == ['slack'] or not s['title']:
        head = (f"{s['title']}: " if s['title'] else '') + text[:140]
    else:
        head = s['title'][:100] + (f' - "{text[:90]}"' if text and text[:40] not in s['title'] else '')
    link = f" [open]({s['link']})" if s['link'] else ''
    if compact:
        return f"- {who} ({s['age']}): {head[:110]}{link}"
    why = '/'.join(s['why_me'])
    lab = f" **{v['label']}**" if v.get('label') else ''
    reason = f" - {v['label_reason']}" if v.get('label_reason') else ''
    carried = ' _(not seen this run)_' if v.get('carried') else ''
    return f"- [{'/'.join(s['sources'])}]{lab} {who} ({s['age']}, {why}): {head}{reason}{link}{carried}"

def person_key(name):
    """One key per person across tools: 'Lamya Al Tahrawe' and 'Lamya Tahrawe' match."""
    n = re.sub(r'\(.*?\)|\bvia\b.*$', ' ', (name or '').lower())
    words = [w for w in re.findall(r'[a-z]+', n) if w not in ('al', 'el', 'bin', 'binti', 'ext')]
    return f'{words[0]} {words[-1]}' if len(words) > 1 else (words[0] if words else '?')

def _grouped(rows):
    """Several items from one person become one line with every link, in rank order."""
    groups, names = {}, {}
    for v in rows:
        who = (v['summary']['actors'] or ['?'])[0]
        k = person_key(who)
        groups.setdefault(k, []).append(v)
        names.setdefault(k, who)
    out = []
    for k, vs in groups.items():
        if len(vs) == 1:
            out.append(_line(vs[0]))
            continue
        parts = []
        for v in vs:
            s = v['summary']
            text = re.sub(r'\s+', ' ', re.sub(r'<@?[^>|]*\|?([^>]*)>', r'\1', s['text'])).strip()
            where = s['title'] if s['sources'] == ['slack'] else ''
            what = text if s['sources'] == ['slack'] or not s['title'] else s['title']
            lab = f"**{v['label']}** " if v.get('label') else ''
            parts.append(f"{lab}{where + ': ' if where else ''}{what[:70]}"
                         + (f" [open]({s['link']})" if s['link'] else ''))
        out.append(f"- **{names[k]}** ({len(vs)}):\n" + '\n'.join(f'  - {p}' for p in parts))
    return out

def acted_rate(state):
    """Of the items shown as needing the user 2 to 14 days ago, how many did they act on."""
    lo, hi = now() - 14 * 86400, now() - 2 * 86400
    shown = [v for v in state['conversations'].values()
             if v.get('first_actionable_ts') and lo <= v['first_actionable_ts'] <= hi]
    if len(shown) < 5:
        return None
    acted = sum(1 for v in shown if v.get('status') in ('closed', 'promised'))
    return acted, len(shown)

def render(out_dir, state, cfg, health):
    convs = state['conversations']
    run_id = state['runs'][-1]['id']
    act, fyi, unl, dropped, closed, aging, promised = [], [], [], [], [], [], []
    for v in convs.values():
        if v.get('last_run') != run_id:
            continue
        st = v.get('status')
        if st == 'open':
            lab = v.get('label')
            (act if lab in ACTIONABLE_LABELS else fyi if lab == 'fyi' else dropped if lab == 'noise'
             else unl).append(v)
        elif st in ('noise', 'dismissed', 'expired'):
            dropped.append(v)
        elif st == 'closed' and v.get('closed_now'):
            closed.append(v)
        elif st == 'aging':
            aging.append(v)
        elif st == 'promised':
            promised.append(v)

    # Before the label pass everything ranks by rules. After it, an item the labeller
    # skipped still ranks with the actionable ones: a missing label is not a verdict, and
    # hiding it once anything else was labelled is how a real ask dropped out of a report.
    top_src = act + unl
    top_src.sort(key=lambda v: (bool(v.get('label')) is False and bool(act), -v.get('score', 0)))
    new = [v for v in top_src if v.get('changed', True)]
    same = [v for v in top_src if not v.get('changed', True)]
    top, more = new[:cfg['top_n']], new[cfg['top_n']:]
    for v in top + more:
        v.setdefault('first_actionable_ts', now())

    run = state['runs'][-1]
    L = [f"# Sweep {run['at_wib']}", '',
         f"**{len(new)} new or changed need you** | {len(same)} still open, nothing new | "
         f"FYI {len(fyi)} | promised by you {len(promised)} | older than {cfg['aging_days']}d {len(aging)} | "
         f"filtered {len(dropped)} | closed since last run {len(closed)}"]
    rate = acted_rate(state)
    if rate:
        pct = round(100 * rate[0] / rate[1])
        L.append(f"Signal check: you acted on {rate[0]} of {rate[1]} items shown as needing you in the "
                 f"last 2 to 14 days ({pct}%)." + (' Under 70%: the filter is letting junk through.'
                                                    if pct < 70 else ''))
    L.append('')
    bad = [h for h in health if h.get('status') != 'ok']
    L.append('## Sources' + (f' ({len(bad)} not ok)' if bad else ''))
    for h in health:
        mark = {'ok': 'ok', 'skipped': 'skipped', 'failed': 'FAILED'}.get(h.get('status'), h.get('status'))
        partial = '' if h.get('complete', True) else ', partial'
        L.append(f"- {h.get('adapter')}: {mark}{partial}, {h.get('items', 0)} items"
                 + (f" ({h['note']})" if h.get('note') else ''))
    if not health:
        L.append('- (no adapter health supplied)')
    if state['runs'][-1].get('no_self'):
        L.append('- WARNING: none of your own replies were found, so nothing can close. '
                 'Check the `me` block in the filter file.')

    head = '## Needs you' if act else '## Needs you (rule ranking, not yet labelled)'
    L += ['', head]
    if act and unl:
        L.append(f'- WARNING: the label pass skipped {len(unl)} item(s). They are listed below '
                 'with no label, after the labelled ones. Label them and run `label` again.')
    L += _grouped(top) if top else ['- Nothing new.']
    if more:
        L += ['', f'<details><summary>More ({len(more)})</summary>', ''] + _grouped(more) + ['', '</details>']
    if same:
        L += ['', f'<details><summary>Still open, nothing new since the last sweep ({len(same)})</summary>', '']
        L += [_line(v, compact=True) for v in same] + ['', '</details>']
    if promised:
        L += ['', f'<details><summary>You said you would come back to these ({len(promised)})</summary>', '']
        L += [_line(v, compact=True) + (f" - you: \"{v['summary']['your_last'][:80]}\""
                                         if v['summary'].get('your_last') else '') for v in promised]
        L += ['', '</details>']
    if fyi:
        by_src = {}
        for v in fyi:
            by_src.setdefault('/'.join(v['summary']['sources']), []).append(v)
        counts = ', '.join(f'{k} {len(vs)}' for k, vs in by_src.items())
        L += ['', f'<details><summary>FYI ({len(fyi)}: {counts})</summary>', '']
        for k, vs in by_src.items():
            L += [f'**{k}**'] + [_line(v, compact=True) for v in vs] + ['']
        L += ['</details>']
    if aging:
        aging.sort(key=lambda v: -v['summary']['latest_ts'])
        L += ['', f'<details><summary>Older than {cfg["aging_days"]} days, still unanswered ({len(aging)})</summary>', '']
        L += [_line(v, compact=True) for v in aging] + ['', '</details>']
    if dropped:
        cats = {}
        for v in dropped:
            r = v.get('drop_reason') or 'model: noise'
            cats[r.split(':')[0]] = cats.get(r.split(':')[0], 0) + 1
        breakdown = ', '.join(f'{n} {c}' for c, n in sorted(cats.items(), key=lambda x: -x[1]))
        L += ['', f'<details><summary>Filtered out ({len(dropped)}: {breakdown})</summary>', '']
        for v in sorted(dropped, key=lambda v: v.get('drop_reason') or ''):
            s = v['summary']
            why = v.get('drop_reason') or f"model: {v.get('label_reason', '')}"
            L.append(f"- `{why}` {', '.join(s['actors'][:2]) or '?'}: "
                     + f"{(s['title'] or s['text'])[:90]}".replace('\n', ' ')
                     + (f" [open]({s['link']})" if s['link'] else ''))
        L += ['', '</details>']
    mutes = suggest_mutes(state, cfg)
    if mutes:
        L += ['', '## Suggested mutes (you decide)']
        L += [f"- {m['name']}: {m['why']}. Mute: `sweep_core.py teach --filter <filter> {m['flag']} \"{m['value']}\"`"
              for m in mutes]
    L += ['', 'Teach the filter: `--allow <sender>` for a false drop, `--deny <sender>` for noise that '
              'got through, `--vip <name>` to rank someone first.']
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, 'report.md'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(L) + '\n')
    surv = [{'key': v['summary']['key'], 'sources': v['summary']['sources'],
             'why_me': v['summary']['why_me'], 'title': v['summary']['title'],
             'actors': v['summary']['actors'], 'age': v['summary']['age'],
             'link': v['summary']['link'], 'root': v['summary'].get('root', ''),
             'your_last': v['summary'].get('your_last', ''), 'messages': v['summary']['messages']}
            for v in unl + act + fyi if v.get('changed', True) or not v.get('label')]
    save_json(os.path.join(out_dir, 'survivors.json'), surv)
    return {'new_or_changed': len(new), 'still_open': len(same), 'actionable': len(act),
            'unlabelled': len(unl), 'fyi': len(fyi), 'promised': len(promised), 'aging': len(aging),
            'filtered': len(dropped), 'closed_now': len(closed)}

def suggest_mutes(state, cfg):
    out = []
    muted = {x.lower() for x in cfg['deny_senders'] + cfg['deny_conversations']}
    for k, c in (state.get('senders') or {}).items():
        if c.get('acted'):
            continue
        is_chan = k.startswith('channel ')
        value = k[len('channel '):] if is_chan else k
        if value.lower() in muted:
            continue
        if c.get('dismissed', 0) >= cfg['mute_after_dismissals']:
            why = f"dismissed {c['dismissed']} times, never answered"
        elif c.get('expired', 0) >= cfg['mute_after_expiries']:
            why = f"{c['expired']} items expired unanswered"
        else:
            continue
        out.append({'name': c.get('name') or value, 'why': why, 'value': value,
                    'flag': '--deny-conversation' if is_chan else '--deny'})
    return out[:5]

# ----------------------------------------------------------------- commands --

def cmd_run(a):
    cfg = load_filter(a.filter)
    health = []
    for h in a.health or []:
        health += load_json(h, [])
    healthy_src, coverage = health_view(health)
    cfg['_healthy'] = healthy_src if health else None
    items = read_items(a.items)
    with state_lock(a.state):
        state = load_json(a.state, new_state())
        state.setdefault('senders', {})
        run_id = f"r{int(now())}"
        wib = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=7))).strftime('%Y-%m-%d %H:%M WIB')
        convs = collapse(items, cfg)
        no_self = not any(m['_self'] for c in convs.values() for m in c['msgs']) and len(convs) > 5
        state['runs'].append({'id': run_id, 'at': now(), 'at_wib': wib, 'items': len(items),
                              'health': health, 'no_self': no_self})
        counts = {}
        for key, conv in convs.items():
            row = state['conversations'].get(key)
            prev = (row or {}).get('status')
            verdict, detail = evaluate(conv, cfg, row)
            counts[verdict] = counts.get(verdict, 0) + 1
            if verdict in ('candidate', 'aging'):
                summ = summarize(conv)
                prev_latest = (row or {}).get('summary', {}).get('latest_ts', 0)
                keep_label = bool(row and row.get('label') and summ['latest_ts'] <= prev_latest)
                changed = summ['latest_ts'] > (row or {}).get('shown_latest', 0)
                remember(state, key, status='open' if verdict == 'candidate' else 'aging',
                         last_run=run_id, summary=summ, carried=False, changed=changed,
                         shown_latest=max(summ['latest_ts'], (row or {}).get('shown_latest', 0)),
                         score=score(conv, cfg, row.get('label') if keep_label else None),
                         label=row.get('label') if keep_label else None,
                         label_reason=row.get('label_reason') if keep_label else None,
                         drop_reason=None, note=detail)
            elif verdict in ('closed', 'promised'):
                if not row and verdict == 'closed':
                    continue                  # answered before it was ever shown: nothing to say
                conv.setdefault('inbound', [m for m in conv['msgs'] if not m['_self']])
                r = remember(state, key, status=verdict, last_run=run_id, summary=summarize(conv),
                             closed_now=prev in ACTIVE and verdict == 'closed', carried=False,
                             drop_reason=None, changed=False)
                if prev in ('open', 'aging') and verdict == 'closed':
                    tally(state, r, 'acted')
            else:
                conv.setdefault('inbound', [m for m in conv['msgs'] if not m['_self']])
                status = 'dismissed' if verdict == 'dismissed' else (
                    'expired' if str(detail).startswith('expired') else 'noise')
                r = remember(state, key, status=status, last_run=run_id, summary=summarize(conv),
                             drop_reason=detail, carried=False, changed=False)
                if status == 'expired' and prev in ACTIVE:
                    tally(state, r, 'expired')

        # Carry forward what the adapters did not report this time. An item leaves only
        # when a source that ran cleanly and read far enough back no longer sees it.
        for key, row in state['conversations'].items():
            if key in convs or row.get('status') not in ACTIVE:
                continue
            s = row.get('summary') or {}
            srcs = s.get('sources') or ['?']
            cov = min(coverage.get(x, 0) for x in srcs)
            age_d = (now() - s.get('latest_ts', now())) / 86400
            if cov and age_d < cov:
                row.update(status='closed', closed_now=True, last_run=run_id, carried=False,
                           drop_reason='no longer reported by its source')
                tally(state, row, 'acted')
            elif age_d > cfg['hard_stale_days']:
                row.update(status='expired', last_run=run_id, carried=False,
                           drop_reason=f'expired:{int(age_d)}d_unanswered')
                tally(state, row, 'expired')
            else:
                if row['status'] == 'open' and age_d > cfg['aging_days']:
                    row['status'] = 'aging'
                row.update(last_run=run_id, carried=True, changed=False)
                counts['carried'] = counts.get('carried', 0) + 1
        prune(state)
        res = render(a.out, state, cfg, health)
        save_json(a.state, state)
    print(json.dumps({'run': run_id, 'items_in': len(items), 'conversations': len(convs),
                      'verdicts': counts, **res,
                      'report': os.path.join(a.out, 'report.md'),
                      'survivors': os.path.join(a.out, 'survivors.json')}, indent=1))

def cmd_label(a):
    cfg = load_filter(a.filter)
    with state_lock(a.state):
        state = load_json(a.state, None)
        if not state or not state['runs']:
            sys.exit('no state yet: run first')
        health = state['runs'][-1].get('health', [])
        if health:
            cfg['_healthy'] = health_view(health)[0]
        labels = load_json(a.labels, {})
        if isinstance(labels, list):
            labels = {x['key']: x for x in labels}
        n, adjusted = 0, 0
        for key, lab in labels.items():
            row = state['conversations'].get(key)
            if not row or row.get('status') != 'open':
                continue
            label, reason = lab.get('label'), lab.get('reason', '')
            s = row.get('summary') or {}
            why = set(s.get('why_me') or [])
            texts = ' '.join(m.get('text') or '' for m in s.get('messages', []))
            # Guardrail 1: the model never drops what was addressed to the user.
            if label == 'noise' and (why & DIRECT_WHY or 'thread_participant' in why and names_me(texts, cfg)):
                label, reason = 'fyi', f'kept (model said noise, but it was addressed to you): {reason}'
                adjusted += 1
            # Guardrail 2: cc, watch and notification items only need the user when they
            # name the user. Otherwise they are information.
            if label in ACTIONABLE_LABELS and why and why <= INDIRECT_WHY and not names_me(texts, cfg):
                label, reason = 'fyi', f'cc/watch only, does not name you: {reason}'
                adjusted += 1
            row['label'], row['label_reason'] = label, reason
            row['score'] = round(row.get('score', 0) + LABEL_WEIGHT.get(label, 0), 1)
            n += 1
        res = render(a.out, state, cfg, health)
        save_json(a.state, state)
    print(json.dumps({'labelled': n, 'guardrail_adjusted': adjusted, **res,
                      'report': os.path.join(a.out, 'report.md')}, indent=1))

def cmd_dismiss(a):
    with state_lock(a.state):
        state = load_json(a.state, new_state())
        row = state['conversations'].get(a.key)
        if not row:
            sys.exit(f'unknown conversation {a.key}')
        row.update(status='dismissed', dismissed_at_ts=row.get('summary', {}).get('latest_ts', now()),
                   dismiss_reason=a.reason or 'dismissed by you', drop_reason=a.reason or 'dismissed by you')
        tally(state, row, 'dismissed')
        save_json(a.state, state)
    print(f'dismissed {a.key}; it comes back only if something new arrives in it')

def cmd_teach(a):
    cfg = load_json(a.filter, {})
    for field, val in (('allow_senders', a.allow), ('deny_senders', a.deny), ('vip', a.vip),
                       ('deny_conversations', a.deny_conversation)):
        if val:
            cfg.setdefault(field, [])
            if val not in cfg[field]:
                cfg[field].append(val)
            print(f'{field} += {val}')
    save_json(a.filter, cfg)

def cmd_eval(a):
    """Score the deterministic layer on a labelled set: every labelled row is one
    conversation. A rule layer is good when it drops noise and never drops a real ask."""
    cfg = load_filter(a.filter)
    rows = read_items([a.labelled])
    tally_ = {}
    misses, leaks = [], []
    for r in rows:
        if r.get('label') == 'notification':
            continue          # a signal the engine collapses into its doc/ticket, not scored
        actor_id = r.get('actor_id') or r.get('channel_or_key') or r.get('actor')
        it = {'source': r.get('source'), 'conversation_key': r.get('channel_or_key') or r.get('id'),
              'actor': r.get('actor'), 'actor_id': actor_id,
              'ts': now() - 3600 * float(r.get('age_h', 1)), 'text': r.get('text'),
              'why_me': r.get('why_me'),
              # what an adapter knows and a labelled row does not: Slack bot ids start with B
              'is_bot': bool(r.get('is_bot')) or bool(re.match(r'^B[0-9A-Z]{8,}$', str(r.get('actor') or ''))),
              'title': r.get('title', ''), 'meta': r.get('meta', {})}
        convs = collapse([it], cfg)
        verdict, detail = evaluate(next(iter(convs.values())), cfg, None)
        dropped = verdict == 'noise'
        lab = r.get('label')
        t = tally_.setdefault(lab, {'n': 0, 'dropped': 0})
        t['n'] += 1
        t['dropped'] += dropped
        if dropped and (lab == 'actionable' or r.get('must_surface')):
            misses.append((r.get('id'), detail, (r.get('text') or '')[:80]))
        if not dropped and lab == 'noise':
            leaks.append((r.get('id'), r.get('noise_reason'), (r.get('text') or '')[:80]))
    act = tally_.get('actionable', {'n': 0, 'dropped': 0})
    noi = tally_.get('noise', {'n': 0, 'dropped': 0})
    fy = tally_.get('fyi', {'n': 0, 'dropped': 0})
    kept_noise = noi['n'] - noi['dropped']
    kept_act = act['n'] - act['dropped']
    kept_fyi = fy['n'] - fy['dropped']
    out = {
        'actionable_recall': round(kept_act / act['n'], 3) if act['n'] else None,
        'noise_dropped_rate': round(noi['dropped'] / noi['n'], 3) if noi['n'] else None,
        'noise_share_of_kept_before_model': round(kept_noise / max(1, kept_act + kept_fyi + kept_noise), 3),
        'fyi_kept': f"{kept_fyi}/{fy['n']}",
        'tally': tally_, 'false_drops': misses, 'noise_leaks': leaks,
    }
    print(json.dumps(out, indent=1, ensure_ascii=False))
    if misses:
        sys.exit(1)

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('run')
    r.add_argument('--items', nargs='+', required=True)
    r.add_argument('--state', required=True)
    r.add_argument('--filter')
    r.add_argument('--out', required=True)
    r.add_argument('--health', nargs='*')
    r.set_defaults(fn=cmd_run)
    lb = sub.add_parser('label')
    lb.add_argument('--state', required=True)
    lb.add_argument('--labels', required=True)
    lb.add_argument('--filter')
    lb.add_argument('--out', required=True)
    lb.set_defaults(fn=cmd_label)
    d = sub.add_parser('dismiss')
    d.add_argument('--state', required=True)
    d.add_argument('--key', required=True)
    d.add_argument('--reason')
    d.set_defaults(fn=cmd_dismiss)
    t = sub.add_parser('teach')
    t.add_argument('--filter', required=True)
    t.add_argument('--allow')
    t.add_argument('--deny')
    t.add_argument('--vip')
    t.add_argument('--deny-conversation')
    t.set_defaults(fn=cmd_teach)
    e = sub.add_parser('eval')
    e.add_argument('--labelled', required=True)
    e.add_argument('--filter')
    e.set_defaults(fn=cmd_eval)
    a = p.parse_args()
    a.fn(a)

if __name__ == '__main__':
    main()
