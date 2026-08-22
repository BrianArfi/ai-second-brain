#!/usr/bin/env python3
"""
mention_ledger.py - Stateful Slack sweep: track every mention of the owner (channels,
threads, DMs) until he has actually responded, plus a full-message watermark sweep
of ALL joined channels for the GLM classification digest.

Design (2026-07-09, per the owner):
  Layer 1 (this script, pure Python, cron */30): collect + mechanical reply-state.
  Layer 2 (GLM via agy-bridge --task harvest): classify digest + open items.
  Layer 3 (Claude, morning/evening update): surface "Waiting on your reply".

Why this exists: the old runner skimmed the last 5 msgs/channel through a name-keyword
filter. Replies to old threads never bump into channel history, mentions were never
searched, and nothing persisted across runs - so unreplied mentions silently vanished.

Subcommands:
  sweep              full collection + reply-state pass (default for cron)
  report [--all]     markdown report of OPEN items (for morning/evening updates)
  dismiss <item_id>  mark an item handled outside Slack
  classify           build a GLM prompt from the digest and run agy-bridge harvest

State: journal/state/slack_mention_ledger.json
Digest (for GLM): journal/state/slack_sweep_digest.jsonl (appended; classify consumes)

Auth: SLACK_USER_TOKEN from .agent/skills/slack-connector/token.env (xoxp, has
search:read). search.messages ONLY works with the user token, never the bot token.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
STATE_PATH = os.path.join(BASE_DIR, 'journal', 'state', 'slack_mention_ledger.json')
DIGEST_PATH = os.path.join(BASE_DIR, 'journal', 'state', 'slack_sweep_digest.jsonl')
TOKEN_ENV = os.path.join(BASE_DIR, '.agent', 'skills', 'slack-connector', 'token.env')
AGY_BRIDGE = os.path.join(BASE_DIR, '.agent', 'skills', 'agy-bridge', 'run.py')

BRIAN_ID_DEFAULT = '<SLACK_ID>'          # verified via auth.test 2026-07-09
FRED_ID = '<SLACK_ID>'                    # any YourManager message = high priority
PRIORITY_AUTHORS = {FRED_ID}

# the owner's decision: only these reactions count as "answered"; eyes does NOT.
ACK_REACTIONS = {'white_check_mark', 'heavy_check_mark', '+1', 'thumbsup', 'ok_hand'}

FIRST_RUN_MENTION_LOOKBACK_DAYS = 3        # don't flood the ledger on first run
FIRST_RUN_CHANNEL_LOOKBACK_HOURS = 24
ANSWERED_RETENTION_DAYS = 14               # prune answered/dismissed after this
API_PAUSE = 0.15                           # pacing between Slack calls

# Auto-dismiss noise so the queue only holds real "waiting on the owner" items.
# CONSERVATIVE by design: a false-dismiss (hiding a real ask) is worse than a
# little residual noise, so we only fire on bots or an unambiguous closer phrase.
ACK_PHRASES = [
    'thank you', 'thanks', 'thankyou', 'much appreciated', 'appreciate it',
    'no worries', 'no problem', 'sounds good', 'sound good', 'got it', 'noted',
    'will do', 'ok thanks', 'okay thanks', 'perfect', 'great', 'awesome',
    'welcome', 'you are welcome', "you're welcome", 'yes exactly', 'exactly',
    'agreed', 'confirming agreement', 'confirmed', 'sure no worries',
    'roger', 'copy that', 'done', 'cheers',
]

# App/bot accounts that post as a normal user (no bot_id) but are pure noise.
# Grow this as new notifier apps appear in the owner's DMs.
NOISE_AUTHORS = {
    'USLACKBOT',
    '<SLACK_ID>',   # Google Calendar app
    'system health grafana alert',   # Grafana alerts bot (posts w/o bot_id)
}

# Channels that are pure automated-alert firehoses: every message is noise,
# the owner never replies. Items from these are auto-dismissed on sweep.
NOISE_CHANNELS = {
    '<SLACK_ID>',   # system-health-grafana-alerts
}

def is_noise(text, is_bot, author=None, channel=None):
    """True only for bot/notifier messages or short pure-acknowledgment closers.
    Never fires on questions, links-only, or bare '^' pings (those may need
    attention). Keeps false-dismiss risk near zero."""
    if channel in NOISE_CHANNELS or is_bot or author in NOISE_AUTHORS:
        return True
    t = (text or '').lower()
    t = re.sub(r'<[^>]+>', ' ', t)          # drop <@U..> mentions and <http..> links
    t = re.sub(r'[^a-z\s]', ' ', t)          # letters only (strip emoji/punct/^)
    t = re.sub(r'\s+', ' ', t).strip()
    if not t:
        return False                          # link-only / bare ping -> keep, may matter
    if '?' in (text or ''):
        return False                          # any question -> keep
    words = t.split()
    if len(words) > 6:
        return False                          # long enough to carry a real ask
    return any(p in t for p in ACK_PHRASES)

# ---------------------------------------------------------------- Slack API --

def load_token():
    tok = os.environ.get('SLACK_USER_TOKEN')
    if not tok and os.path.exists(TOKEN_ENV):
        for line in open(TOKEN_ENV):
            line = line.strip()
            if line.startswith('SLACK_USER_TOKEN='):
                tok = line.split('=', 1)[1].strip().strip('"').strip("'")
    if not tok:
        sys.exit('FATAL: SLACK_USER_TOKEN not found (env or slack-connector/token.env)')
    return tok

def slack(method, token, params=None, retries=3):
    params = params or {}
    url = f'https://slack.com/api/{method}?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
    for attempt in range(retries):
        try:
            resp = json.load(urllib.request.urlopen(req, timeout=30))
        except Exception as e:                       # network blip: back off, retry
            if attempt == retries - 1:
                return {'ok': False, 'error': f'request_failed: {e}'}
            time.sleep(2 * (attempt + 1))
            continue
        if resp.get('ok'):
            return resp
        if resp.get('error') == 'ratelimited':
            time.sleep(int(resp.get('retry_after', 10)) if isinstance(resp.get('retry_after'), (int, str)) else 10)
            continue
        return resp
    return {'ok': False, 'error': 'retries_exhausted'}

# ------------------------------------------------------------------- state --

def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {'search_watermark': 0.0, 'watermarks': {}, 'threads': {},
            'items': {}, 'channel_names': {}, 'last_sweep': None}

def save_state(state):
    """Merge into whatever is on disk, then write. Never replace outright.

    A replace is only safe while one machine writes this file. Two do now: the
    macOS listener and the WSL sweep, and either may be the only one running for
    a whole day. The second write would otherwise be built on a picture that
    never contained the first machine's records, and would delete them with no
    conflict to notice. That is the 17 Aug 2026 incident in CLAUDE.md.

    Merging makes a write additive: it can add an item or advance one, never
    remove or revert. Deletion still happens, but only through the retention
    rule inside the merge, which both machines derive identically from the data
    rather than sending to each other.

    Returns the state that was actually written, which is not always the one
    passed in. A caller that reads counts back out of its own dict afterwards
    would otherwise report a number that predates the merge.
    """
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)

    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH) as f:
                on_disk = json.load(f)
        except (json.JSONDecodeError, OSError):
            on_disk = None      # unreadable: our copy is the better of the two
        if on_disk is not None:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from ledger_merge import merge_states
            state = merge_states(on_disk, state,
                                 retention_days=ANSWERED_RETENTION_DAYS)

    tmp = STATE_PATH + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f, indent=1, ensure_ascii=False)
    os.replace(tmp, STATE_PATH)
    return state

def digest_append(rows):
    if not rows:
        return
    os.makedirs(os.path.dirname(DIGEST_PATH), exist_ok=True)
    with open(DIGEST_PATH, 'a') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')

# ------------------------------------------------------------------- sweep --

def thread_ts_from_permalink(permalink):
    try:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(permalink).query)
        return q.get('thread_ts', [None])[0]
    except Exception:
        return None

def new_item(state, channel_id, channel_name, ts, author, text, permalink, kind,
             thread_ts=None, is_bot=False):
    item_id = f'{channel_id}:{ts}'
    if item_id in state['items']:
        return
    noise = is_noise(text, is_bot or str(author).startswith('B'), author, channel_id)
    state['items'][item_id] = {
        'channel': channel_id, 'channel_name': channel_name, 'ts': ts,
        'thread_ts': thread_ts, 'author': author, 'text': text[:600],
        'permalink': permalink, 'kind': kind,          # mention | dm | thread_followup
        'status': 'dismissed' if noise else 'open',
        'priority': author in PRIORITY_AUTHORS,
        'first_seen': time.time(),
        'answered_at': time.time() if noise else None,
        'answered_by': 'auto_noise' if noise else None,
    }
    if thread_ts:
        state['threads'].setdefault(f'{channel_id}:{thread_ts}',
                                    {'channel': channel_id, 'last_seen_reply': 0.0})

def sweep_mentions(token, state, brian_id):
    """search.messages catches mentions ANYWHERE: channels, thread replies, DMs."""
    wm = float(state.get('search_watermark') or 0)
    if wm == 0:
        wm = time.time() - FIRST_RUN_MENTION_LOOKBACK_DAYS * 86400
    newest = wm
    page, found = 1, 0
    while page <= 10:
        resp = slack('search.messages', token, {
            'query': f'<@{brian_id}>', 'sort': 'timestamp', 'sort_dir': 'desc',
            'count': 100, 'page': page})
        if not resp.get('ok'):
            print(f'  ! search.messages: {resp.get("error")}', file=sys.stderr)
            break
        matches = resp.get('messages', {}).get('matches', [])
        if not matches:
            break
        stop = False
        for m in matches:
            ts = float(m.get('ts', 0))
            if ts <= wm:
                stop = True
                break
            newest = max(newest, ts)
            author = m.get('user') or m.get('username') or '?'
            if author == brian_id:
                continue
            ch = m.get('channel', {})
            permalink = m.get('permalink', '')
            new_item(state, ch.get('id', '?'), ch.get('name', '?'), m.get('ts'),
                     author, m.get('text', ''), permalink, 'mention',
                     thread_ts_from_permalink(permalink))
            found += 1
        if stop or page >= resp.get('messages', {}).get('paging', {}).get('pages', 1):
            break
        page += 1
        time.sleep(API_PAUSE)
    state['search_watermark'] = newest
    return found

def list_joined(token):
    convos, cursor = [], None
    while True:
        params = {'types': 'public_channel,private_channel,mpim,im',
                  'exclude_archived': 'true', 'limit': 200}
        if cursor:
            params['cursor'] = cursor
        resp = slack('users.conversations', token, params)
        if not resp.get('ok'):
            print(f'  ! users.conversations: {resp.get("error")}', file=sys.stderr)
            break
        convos.extend(resp.get('channels', []))
        cursor = resp.get('response_metadata', {}).get('next_cursor')
        if not cursor:
            break
        time.sleep(API_PAUSE)
    return convos

def sweep_channels(token, state, brian_id):
    """Watermark sweep of ALL joined conversations. Feeds digest + DM items +
    thread registry. No message cap: everything since the watermark is seen."""
    digest_rows, dm_items, brian_acted = [], 0, []
    for ch in list_joined(token):
        cid = ch['id']
        name = ch.get('name') or ch.get('user') or cid   # ims have no name
        is_im = ch.get('is_im', False)
        state['channel_names'][cid] = name
        wm = float(state['watermarks'].get(cid) or 0)
        if wm == 0:
            wm = time.time() - FIRST_RUN_CHANNEL_LOOKBACK_HOURS * 3600
        resp = slack('conversations.history', token,
                     {'channel': cid, 'oldest': f'{wm:.6f}', 'limit': 200})
        time.sleep(API_PAUSE)
        if not resp.get('ok'):
            continue
        msgs = resp.get('messages', [])
        if msgs:
            state['watermarks'][cid] = max(float(m['ts']) for m in msgs)
        else:
            state['watermarks'][cid] = max(wm, state['watermarks'].get(cid, 0) or wm)
            continue
        for m in msgs:
            author = m.get('user', m.get('bot_id', '?'))
            text = m.get('text', '')
            is_bot = bool(m.get('bot_id')) or m.get('subtype') == 'bot_message'
            if author == brian_id:
                brian_acted.append((cid, float(m['ts'])))
                continue
            # every DM message not from the owner is a candidate "needs response"
            if is_im:
                new_item(state, cid, f'DM:{name}', m['ts'], author, text, '', 'dm',
                         is_bot=is_bot)
                dm_items += 1
            # register threads with new activity so old-thread replies are seen
            if m.get('reply_count', 0) > 0:
                state['threads'].setdefault(f'{cid}:{m["ts"]}',
                                            {'channel': cid, 'last_seen_reply': 0.0})
            digest_rows.append({'channel': cid, 'name': name, 'ts': m['ts'],
                                'user': author, 'text': text[:400],
                                'is_dm': is_im, 'swept_at': time.time()})
    digest_append(digest_rows)
    return len(digest_rows), dm_items, brian_acted

def resolve_open_items(token, state, brian_id):
    """Mechanical reply-state: an item is answered when the owner replied after it
    (same thread, or same channel for non-thread items) or ack-reacted on it.
    Also reopens a thread item if someone followed up after the owner's last reply."""
    answered = 0
    by_channel_open = {}
    for item_id, it in state['items'].items():
        if it['status'] != 'open':
            continue
        by_channel_open.setdefault(it['channel'], []).append((item_id, it))

    for cid, items in by_channel_open.items():
        for item_id, it in items:
            anchor = it['thread_ts'] or it['ts']
            resp = slack('conversations.replies', token,
                         {'channel': cid, 'ts': anchor, 'limit': 100})
            time.sleep(API_PAUSE)
            if not resp.get('ok'):
                continue
            msgs = resp.get('messages', [])
            root = msgs[0] if msgs else {}
            # 1) ack-reaction by the owner on the root/mention message
            for r in root.get('reactions', []):
                if r.get('name') in ACK_REACTIONS and brian_id in r.get('users', []):
                    it.update(status='answered', answered_by='reaction',
                              answered_at=time.time())
                    answered += 1
                    break
            if it['status'] != 'open':
                continue
            # 2) the owner message later in the same thread
            brian_after = [float(m['ts']) for m in msgs
                           if m.get('user') == brian_id and float(m['ts']) > float(it['ts'])]
            if brian_after:
                it.update(status='answered', answered_by='thread_reply',
                          answered_at=time.time())
                answered += 1
                continue
        # 3) non-thread items: any the owner message in the channel after the item
        plain = [(iid, it) for iid, it in items
                 if it['status'] == 'open' and not it['thread_ts']]
        if plain:
            oldest = min(float(it['ts']) for _, it in plain)
            resp = slack('conversations.history', token,
                         {'channel': cid, 'oldest': f'{oldest:.6f}', 'limit': 200})
            time.sleep(API_PAUSE)
            if resp.get('ok'):
                brian_ts = [float(m['ts']) for m in resp.get('messages', [])
                            if m.get('user') == brian_id]
                for iid, it in plain:
                    if any(bt > float(it['ts']) for bt in brian_ts):
                        it.update(status='answered', answered_by='channel_reply',
                                  answered_at=time.time())
                        answered += 1
    return answered

SLACK_LINK_RE = re.compile(r'archives/(C[A-Z0-9]+)/p(\d{16})')

def needs_context(text):
    """A pointer/link-only message carries no standalone ask - the substance is
    in the message it points to (^ , :point_up:, a shared permalink, a bare link)."""
    t = re.sub(r'<@[^>]+>', '', text or '')          # drop mentions
    t = re.sub(r'<https?://[^>]+>', '', t)            # drop links
    t = re.sub(r':[a-z0-9_+\-]+:', '', t)             # drop emoji
    t = re.sub(r'[\^\s⬆️]', '', t)          # drop ^ / up-arrow / space
    return t.strip() == ''

def _permalink_target(text):
    """If the message is (or contains) a slack permalink, return (cid, ts)."""
    m = SLACK_LINK_RE.search(text or '')
    if not m:
        return None
    ts = m.group(2)
    return m.group(1), f'{ts[:10]}.{ts[10:]}'

def _substantive_context(token, cid, ts, hops=2):
    """Return [[user, text], ...] of the 1-2 most recent SUBSTANTIVE messages at
    or before ts in cid. Follows a permalink/pointer chain up to `hops` levels so
    'link -> point_up -> real ask' still resolves to the real ask."""
    resp = slack('conversations.history', token,
                 {'channel': cid, 'latest': ts, 'inclusive': 'true', 'limit': 8})
    time.sleep(API_PAUSE)
    if not resp.get('ok'):
        return []
    ctx = []
    for m in resp.get('messages', []):               # newest-first
        if m.get('subtype') in ('channel_join', 'group_join'):
            continue
        txt = m.get('text', '')
        if needs_context(txt):                        # itself a pointer
            tgt = _permalink_target(txt)
            if tgt and hops > 0:                      # chase the link it points to
                deep = _substantive_context(token, tgt[0], tgt[1], hops - 1)
                if deep:
                    return deep
            continue
        ctx.append([m.get('user', '?'), re.sub(r'\s+', ' ', txt)[:220]])
        if len(ctx) >= 2:
            break
    return list(reversed(ctx))

def enrich_pointers(token, state):
    """Attach preceding-message context to open pointer/link-only items so the
    surfaced queue shows the real ask, never a bare '^'."""
    n = 0
    for it in state['items'].values():
        if it['status'] != 'open' or it.get('context'):
            continue
        if not needs_context(it['text']):
            continue
        tgt = _permalink_target(it['text'])
        if tgt:
            ctx = _substantive_context(token, tgt[0], tgt[1])
        else:                                          # ^ / point_up -> look back in-place
            ctx = _substantive_context(token, it['channel'], it['ts'])
        if ctx:
            it['context'] = ctx
            n += 1
    return n

def prune(state):
    cutoff = time.time() - ANSWERED_RETENTION_DAYS * 86400
    dead = [iid for iid, it in state['items'].items()
            if it['status'] in ('answered', 'dismissed')
            and (it.get('answered_at') or it['first_seen']) < cutoff]
    for iid in dead:
        del state['items'][iid]
    # drop thread registry entries with no open item and no activity for 14d
    live_threads = {f"{it['channel']}:{it['thread_ts']}" for it in state['items'].values()
                    if it['thread_ts'] and it['status'] == 'open'}
    for tid in [t for t in state['threads'] if t not in live_threads]:
        state['threads'].pop(tid, None)
    return len(dead)

def sweep_noise_backlog(state):
    """Re-check already-open items against is_noise so items that predate the
    heuristic (or newly qualify) get auto-dismissed too."""
    n = 0
    for it in state['items'].values():
        if it['status'] != 'open':
            continue
        if is_noise(it['text'], str(it['author']).startswith('B'), it['author'], it.get('channel')):
            it.update(status='dismissed', answered_by='auto_noise',
                      answered_at=time.time())
            n += 1
    return n

def cmd_sweep(args):
    # The sweep used to be the only writer of this file, so it needed no lock.
    # Push ingestion is a second writer now (slack-push/scripts/ingest.py), and
    # both rewrite the whole document, which is a lost update whenever they
    # overlap. Same lock, same name, on both sides.
    sys.path.insert(0, os.path.join(BASE_DIR, '.agent', 'scripts'))
    from ledger_lock import ledger_lock
    with ledger_lock('slack_mention_ledger'):
        return _sweep(args)

def _sweep(args):
    token = load_token()
    auth = slack('auth.test', token)
    brian_id = auth.get('user_id') or BRIAN_ID_DEFAULT
    state = load_state()
    t0 = time.time()
    n_mentions = sweep_mentions(token, state, brian_id)
    n_digest, n_dm, _ = sweep_channels(token, state, brian_id)
    n_answered = resolve_open_items(token, state, brian_id)
    n_noise = sweep_noise_backlog(state)
    n_ctx = enrich_pointers(token, state)
    n_pruned = prune(state)
    state['last_sweep'] = time.time()
    save_state(state)
    open_items = [i for i in state['items'].values() if i['status'] == 'open']
    print(f'sweep done in {time.time()-t0:.0f}s: +{n_mentions} mentions, '
          f'+{n_dm} DM items, {n_digest} digest msgs, {n_answered} auto-answered, '
          f'{n_noise} auto-noise, {n_ctx} enriched, {n_pruned} pruned -> {len(open_items)} OPEN '
          f'({sum(1 for i in open_items if i["priority"])} priority)')

# ------------------------------------------------------------------ report --

def age_str(ts):
    h = (time.time() - float(ts)) / 3600
    return f'{h/24:.1f}d' if h >= 24 else f'{h:.0f}h'

def resolve_names(state, user_ids, token):
    """users.info with a persistent cache in state['user_names']. Per
    feedback_no_guessing_names: resolve via lookup, fall back to the raw ID."""
    cache = state.setdefault('user_names', {})
    missing = [u for u in user_ids if u and u not in cache and u.startswith('U')]
    for uid in missing:
        resp = slack('users.info', token, {'user': uid})
        prof = resp.get('user', {}) if resp.get('ok') else {}
        cache[uid] = prof.get('real_name') or prof.get('name') or uid
        time.sleep(API_PAUSE)
    if missing:
        save_state(state)
    return cache

def cmd_report(args):
    state = load_state()
    items = [(iid, it) for iid, it in state['items'].items()
             if args.all or it['status'] == 'open']
    if not items:
        print('No open items. Ledger is clean.')
        return
    token = load_token()
    ids = {it['author'] for _, it in items}
    ids |= {it['channel_name'].split(':', 1)[1] for _, it in items
            if it['channel_name'].startswith('DM:')}
    # DMs found via search carry the counterpart's raw U-ID as channel_name
    ids |= {it['channel_name'] for _, it in items
            if re.fullmatch(r'U[A-Z0-9]+', it['channel_name'])}
    ids |= {uid for _, it in items for uid, _ in it.get('context', [])}
    names = resolve_names(state, ids, token)
    items.sort(key=lambda x: (not x[1]['priority'], -float(x[1]['ts'])))
    print(f'## 🔴 Waiting on your reply ({len(items)})\n')
    for iid, it in items:
        flag = '🔥 ' if it['priority'] else ''
        link = f' [thread]({it["permalink"]})' if it['permalink'] else ''
        author = names.get(it['author'], it['author'])
        chan = it['channel_name']
        if chan.startswith('DM:'):
            chan = 'DM ' + names.get(chan[3:], chan[3:])
        elif re.fullmatch(r'U[A-Z0-9]+', chan):
            chan = 'DM ' + names.get(chan, chan)
        text = re.sub(r'\s+', ' ', it['text'])[:160]
        print(f'- {flag}**{chan}** · {author} · {age_str(it["ts"])} ago'
              f'{link} — {text}  `{iid}` [{it["status"]}]')
        for uid, ctx in it.get('context', []):        # the real ask behind a pointer
            ctx1 = re.sub(r'\s+', ' ', ctx)[:180]
            print(f'    ↳ _re:_ {names.get(uid, uid)}: {ctx1}')

def cmd_dismiss(args):
    state = load_state()
    it = state['items'].get(args.item_id)
    if not it:
        sys.exit(f'item not found: {args.item_id}')
    it.update(status='dismissed', answered_at=time.time(), answered_by='manual')
    save_state(state)
    print(f'dismissed: {args.item_id}')

# ---------------------------------------------------------------- classify --

def cmd_classify(args):
    """Batch the digest through GLM (agy-bridge harvest chain). GLM only enriches;
    it never decides answered/open - that is mechanical (resolve_open_items)."""
    if not os.path.exists(DIGEST_PATH):
        print('digest empty, nothing to classify')
        return
    rows = [json.loads(l) for l in open(DIGEST_PATH) if l.strip()]
    if not rows:
        print('digest empty, nothing to classify')
        return
    state = load_state()
    open_items = [it for it in state['items'].values() if it['status'] == 'open']
    prompt = (
        'You are a mechanical Slack triage classifier for the owner (Work PM, Slack id '
        f'{BRIAN_ID_DEFAULT}). For EACH message below output one JSON line: '
        '{"ts":..., "channel":..., "class":"needs_reply|action_item|meeting_input|fyi|noise", '
        '"urgency":"high|normal|low", "summary":"<max 15 words>"}. '
        'needs_reply = a human is waiting on the owner specifically. YourManager messages are always high. '
        'Output ONLY JSON lines, no prose.\n\n'
        '== OPEN LEDGER ITEMS (already known, classify urgency only) ==\n'
        + '\n'.join(json.dumps({'ts': i['ts'], 'channel': i['channel_name'],
                                'author': i['author'], 'text': i['text'][:200]},
                               ensure_ascii=False) for i in open_items[:50])
        + '\n\n== NEW CHANNEL MESSAGES (classify fully) ==\n'
        + '\n'.join(json.dumps({'ts': r['ts'], 'channel': r['name'], 'user': r['user'],
                                'text': r['text'][:200]}, ensure_ascii=False)
                    for r in rows[:400]))
    tmp = os.path.join(os.path.dirname(DIGEST_PATH), 'slack_classify_prompt.txt')
    with open(tmp, 'w') as f:
        f.write(prompt)
    out = subprocess.run([sys.executable, AGY_BRIDGE, '--task', 'harvest',
                          '--prompt-file', tmp, '--timeout', '180'],
                         capture_output=True, text=True)
    result_path = os.path.join(os.path.dirname(DIGEST_PATH), 'slack_classify_result.txt')
    with open(result_path, 'w') as f:
        f.write(out.stdout)
    if out.returncode == 0 and out.stdout.strip():
        os.replace(DIGEST_PATH, DIGEST_PATH + '.classified')   # consume digest
        print(f'classified {len(rows)} msgs -> {result_path}')
    else:
        print(f'agy-bridge failed (rc={out.returncode}); digest kept for retry.\n'
              f'{out.stderr[:500]}', file=sys.stderr)

# -------------------------------------------------------------------- main --

def main():
    p = argparse.ArgumentParser(description='Stateful Slack mention ledger')
    sub = p.add_subparsers(dest='cmd')
    sub.add_parser('sweep')
    rp = sub.add_parser('report')
    rp.add_argument('--all', action='store_true')
    dp = sub.add_parser('dismiss')
    dp.add_argument('item_id')
    sub.add_parser('classify')
    args = p.parse_args()
    {'sweep': cmd_sweep, 'report': cmd_report,
     'dismiss': cmd_dismiss, 'classify': cmd_classify}.get(args.cmd or 'sweep', cmd_sweep)(args)

if __name__ == '__main__':
    main()
