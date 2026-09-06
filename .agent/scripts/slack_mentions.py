#!/usr/bin/env python3
"""Names in an outbound Slack message, turned into handles that actually ping.

`slack_text.py` does the inbound half: `<@<SLACK_ID>>` becomes `@Teammate Dev Singh` so a
reply draft reads like words. This file does the outbound half, which was missing.

A draft written here says "Teammate, quick one on the Apple Store date". Slack posts that
literally. Teammate gets no ping, no badge, and no notification, so the message sits in the
channel until somebody happens to scroll past it. `slack_client.py` already converts a
typed `@Teammate` into `<@U...>`, but a draft that writes the bare name never reaches that
code, and a bare name is what most drafts carry.

## Where the names come from

Two sources, in priority order:

1. `Clients/Work/People/*.md`, each carrying `**Slack:** <@<SLACK_ID>>` under an H1 with
   the person's full name. Curated by hand, so it wins on a conflict.
2. `journal/state/slack_user_names.json`, the `{user_id: display_name}` cache the
   slack-push listener maintains. Wide coverage, no curation.

## What it will and will not match

A first name is matched only when exactly one person in the workspace carries it. Two
Rahuls means "Teammate" resolves to nobody and the check reports the candidates instead of
guessing, because pinging the wrong Teammate is worse than pinging no one.

Matching is capital-sensitive and skips anything already inside a `<...>` token, a URL, or
a code span, so an existing mention is never double-wrapped.
"""

import argparse
import json
import os
import re
import sys
import time

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
NAMES_PATH = os.path.join(REPO_ROOT, 'journal', 'state', 'slack_user_names.json')
PERSON_PATH = os.path.join(REPO_ROOT, 'journal', 'state', 'slack_person_index.json')
PEOPLE_DIR = os.path.join(REPO_ROOT, 'Clients', 'Work', 'People')
TOKEN_ENV = os.path.join(REPO_ROOT, '.agent', 'skills', 'slack-connector', 'token.env')

# First names that are also ordinary words in English or Indonesian. Matching one of these
# on its own would rewrite a sentence that never named a person.
STOPNAMES = {
    'add', 'all', 'and', 'api', 'app', 'ada', 'bank', 'best', 'beta', 'blue', 'card',
    'case', 'cash', 'core', 'data', 'dev', 'secondary', 'free', 'gift', 'hold', 'info', 'jira',
    'lead', 'live', 'mail', 'work', 'mile', 'miles', 'need', 'note', 'ops', 'page',
    'plan', 'post', 'prod', 'sale', 'shop', 'site', 'star', 'stock', 'store', 'team',
    'test', 'user', 'web', 'will', 'work',
}

MENTION_RE = re.compile(r'<[^>]*>')
URL_RE = re.compile(r'https?://\S+')
CODE_RE = re.compile(r'`[^`]*`')

def _people_pages():
    """{full name: user id} from the curated People pages."""
    out = {}
    if not os.path.isdir(PEOPLE_DIR):
        return out
    for fn in sorted(os.listdir(PEOPLE_DIR)):
        if not fn.endswith('.md'):
            continue
        try:
            with open(os.path.join(PEOPLE_DIR, fn), encoding='utf-8') as fh:
                body = fh.read()
        except OSError:
            continue
        uid = re.search(r'\*\*Slack:\*\*\s*<@([A-Z0-9]+)>', body)
        title = re.search(r'^#\s+(.+)$', body, re.M)
        if uid and title:
            out[title.group(1).strip()] = uid.group(1)
    return out

def _cached_names():
    """{user id: display name} for live humans.

    `slack_person_index.json` is the good source: it is built by `refresh` below from
    users.list, and it holds only accounts where is_bot and deleted are both false. That
    matters, because the raw name cache carries every app and every deactivated account in
    the workspace, and those are what produced "Weekly", "Float" and "AWS" as people, plus
    a second dead Teammate that made the live one look ambiguous.

    Without the index it falls back to the raw cache, which over-reports rather than
    under-reports. A name flagged that should not have been is a line in a warning; a name
    missed is the bug this whole file exists to fix.
    """
    try:
        with open(PERSON_PATH, encoding='utf-8') as fh:
            people = json.load(fh)
        if isinstance(people, dict) and people.get('users'):
            return {uid: u['name'] for uid, u in people['users'].items()}
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        pass
    try:
        with open(NAMES_PATH, encoding='utf-8') as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}

def refresh_person_index():
    """Rebuild `slack_person_index.json` from users.list: live humans only.

    Reuses the slack-connector token, read-only (`users.list`), so it needs no approval
    gate. Returns the number of people written.
    """
    import urllib.parse
    import urllib.request

    # the owner's user token first, the bot token only as a fallback: the bot token in
    # token.env currently fails auth.test, and users.list is read-only either way.
    env = {}
    if os.path.exists(TOKEN_ENV):
        with open(TOKEN_ENV, encoding='utf-8') as fh:
            for line in fh:
                k, _, v = line.strip().partition('=')
                if k:
                    env[k.strip()] = v.strip().strip('"').strip("'")
    token = (os.environ.get('SLACK_USER_TOKEN') or env.get('SLACK_USER_TOKEN')
             or os.environ.get('SLACK_BOT_TOKEN') or env.get('SLACK_BOT_TOKEN'))
    if not token:
        raise SystemExit('no Slack token: set SLACK_USER_TOKEN or fill .agent/skills/slack-connector/token.env')

    users, cursor = {}, None
    while True:
        params = {'limit': 200}
        if cursor:
            params['cursor'] = cursor
        req = urllib.request.Request(
            'https://slack.com/api/users.list?' + urllib.parse.urlencode(params),
            headers={'Authorization': 'Bearer ' + token})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.load(resp)
        if not data.get('ok'):
            raise SystemExit('users.list failed: %s' % data.get('error'))
        for u in data.get('members', []):
            if u.get('is_bot') or u.get('deleted') or u.get('id') == 'USLACKBOT':
                continue
            p = u.get('profile') or {}
            name = p.get('real_name') or p.get('display_name') or u.get('name')
            if not name:
                continue
            users[u['id']] = {
                'name': name,
                'display_name': p.get('display_name') or '',
                'handle': u.get('name') or '',
                'title': p.get('title') or '',
            }
        cursor = (data.get('response_metadata') or {}).get('next_cursor')
        if not cursor:
            break

    tmp = PERSON_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump({'users': users}, fh, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, PERSON_PATH)
    return len(users)

def build_index():
    """{lowercased name key: sorted list of user ids}.

    A key is either a full display name or a first name. A first name that more than one
    person answers to keeps both ids, and the caller refuses to resolve it.
    """
    index = {}

    def put(key, uid):
        key = key.strip().lower()
        if len(key) < 3 or key in STOPNAMES:
            return
        index.setdefault(key, set()).add(uid)

    for uid, label in _cached_names().items():
        label = (label or '').strip()
        if not label or label.startswith('U') and ' ' not in label and label.isupper():
            continue
        clean = label.replace('.', ' ').replace('_', ' ')
        clean = re.sub(r'\s+', ' ', clean).strip()
        if not clean:
            continue
        put(clean, uid)
        put(clean.split()[0], uid)
        parts = clean.split()
        if len(parts) > 2:
            put(' '.join(parts[:2]), uid)   # "Teammate Dev" for "Teammate Dev Singh"

    # People pages are curated, so they overwrite whatever the cache guessed.
    for name, uid in _people_pages().items():
        for key in (name, name.split()[0]):
            key = key.strip().lower()
            if len(key) < 3 or key in STOPNAMES:
                continue
            index[key] = {uid}

    return {k: sorted(v) for k, v in index.items()}

def _masked(text):
    """The text with mentions, URLs and code spans blanked out, so a scan skips them."""
    out = list(text)
    for rx in (MENTION_RE, URL_RE, CODE_RE):
        for m in rx.finditer(text):
            for i in range(m.start(), m.end()):
                out[i] = '\x00'
    return ''.join(out)

def find_names(text, index=None):
    """Every named person in the text who carries no handle.

    Returns a list of dicts: {name, start, end, ids}. `ids` holds one id when the name
    resolves, several when it is ambiguous.
    """
    index = index if index is not None else build_index()
    scan = _masked(text)
    hits = []
    taken = []

    # Longest first, so "Teammate Dev Singh" wins over "Teammate".
    for key in sorted(index, key=len, reverse=True):
        pattern = r'(?<![\w@])' + r'\s+'.join(re.escape(w) for w in key.split()) + r'(?![\w])'
        for m in re.finditer(pattern, scan, re.IGNORECASE):
            surface = text[m.start():m.end()]
            if not surface[0].isupper():
                continue   # a lowercase "will" is the verb, not the person
            if any(m.start() < e and s < m.end() for s, e in taken):
                continue
            taken.append((m.start(), m.end()))
            hits.append({'name': surface, 'start': m.start(), 'end': m.end(),
                         'ids': index[key]})
    hits.sort(key=lambda h: h['start'])
    return hits

def unmentioned(text, index=None):
    """One entry per person named without a handle, not one per occurrence.

    A draft that says "Amr" nine times is one missing handle to fix, so the caller and the
    send guard both read the deduplicated list.
    """
    seen = {}
    for h in find_names(text, index):
        key = tuple(h['ids'])
        if key in seen:
            seen[key]['count'] += 1
        else:
            seen[key] = {'name': h['name'], 'ids': h['ids'], 'count': 1}
    return list(seen.values())

def apply_mentions(text, index=None, all_occurrences=False):
    """Replace resolvable names with `<@ID>`.

    By default only the FIRST occurrence of each person is replaced, which is how a person
    writes: ping once at the top, then use the plain name in the rest of the message.

    Returns (new_text, applied, unresolved).
    """
    hits = find_names(text, index)
    chosen, unresolved, seen = [], [], set()
    # Forward pass picks WHICH hits to rewrite, so "first occurrence" means the first one
    # in the message. The rewrite itself then runs backwards, to keep earlier offsets valid.
    for h in hits:
        if len(h['ids']) != 1:
            unresolved.append(h)
            continue
        uid = h['ids'][0]
        if uid in seen and not all_occurrences:
            continue
        seen.add(uid)
        chosen.append((h, uid))

    out = text
    for h, uid in reversed(chosen):
        out = out[:h['start']] + '<@%s>' % uid + out[h['end']:]
    return out, [{'name': h['name'], 'id': uid} for h, uid in chosen], unresolved

def read_text(args):
    if args.text is not None:
        return args.text
    if args.file:
        with open(args.file, encoding='utf-8') as fh:
            return fh.read()
    return sys.stdin.read()

def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('action', choices=['check', 'apply', 'lookup', 'refresh'])
    ap.add_argument('--text')
    ap.add_argument('--file')
    ap.add_argument('--name', help='name to look up (lookup action)')
    ap.add_argument('--if-stale', type=float, metavar='DAYS',
                    help='refresh: rebuild only when the index is older than DAYS (or missing)')
    ap.add_argument('--all', action='store_true',
                    help='apply: mention every occurrence, not only the first')
    ap.add_argument('--in-place', action='store_true', help='apply: write back to --file')
    a = ap.parse_args()

    if a.action == 'refresh':
        if a.if_stale is not None:
            try:
                age_days = (time.time() - os.path.getmtime(PERSON_PATH)) / 86400.0
            except OSError:
                age_days = None
            if age_days is not None and age_days < a.if_stale:
                print('index is %.1f days old, nothing to do' % age_days)
                return 0
        n = refresh_person_index()
        print('%d live humans written to %s' % (n, os.path.relpath(PERSON_PATH, REPO_ROOT)))
        return 0

    index = build_index()

    if a.action == 'lookup':
        if not a.name:
            print('lookup needs --name', file=sys.stderr)
            return 2
        ids = index.get(a.name.strip().lower(), [])
        if not ids:
            print('no match for %s' % a.name)
            return 1
        for uid in ids:
            print('<@%s>' % uid)
        return 0

    text = read_text(a)

    if a.action == 'check':
        people = unmentioned(text, index)
        if not people:
            print('OK: every person named in this message already carries a handle.')
            return 0
        print('%d person(s) written without a Slack handle:' % len(people))
        for p in people:
            times = '' if p['count'] == 1 else ' (x%d)' % p['count']
            if len(p['ids']) == 1:
                print('  %-22s -> <@%s>%s' % (p['name'], p['ids'][0], times))
            else:
                print('  %-22s -> ambiguous, pick one: %s%s'
                      % (p['name'], ', '.join('<@%s>' % i for i in p['ids']), times))
        return 1

    new, applied, unresolved = apply_mentions(text, index, a.all)
    if a.in_place and a.file:
        with open(a.file, 'w', encoding='utf-8') as fh:
            fh.write(new)
        print('wrote %s' % a.file)
    else:
        sys.stdout.write(new)
    for h in applied:
        print('  mentioned %s as <@%s>' % (h['name'], h['id']), file=sys.stderr)
    for h in unresolved:
        print('  left alone, ambiguous: %s (%s)' % (h['name'], ', '.join(h['ids'])), file=sys.stderr)
    return 0

if __name__ == '__main__':
    sys.exit(main())
