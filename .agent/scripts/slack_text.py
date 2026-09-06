#!/usr/bin/env python3
"""Slack's wire text, turned back into words.

Slack does not send `@Teammate`. It sends `<@<SLACK_ID>>`, and a channel reference, a link and an
email address each get their own bracket form. Passed through untouched, a reply draft reads:

    Original: <@<SLACK_ID>> We discussed the multi-currency doc internally ... schedule a call
    for tomorrow with me, <@UV64SPATC>, <@U8VMF9CPQ> and <@UEH6T8BDM>

which names nobody, and is what the owner was reading on 25 Aug 2026.

## Why this exists as a shared module

The same decoding already lives in the ASB app, in Rust
(`src-tauri/src/slackpush.rs::render_text`), because the app's banner had the identical bug. That
fixed ONE surface. Everything the harness writes -- reply drafts, chase drafts, the generated
follow-up tracker -- is written by Python and still carried the codes.

Two implementations is already one too many, and a third would be worse. This is the Python half,
kept behaviourally identical to the Rust one on purpose; the shared tests below are the same cases
that file asserts. If you change one, change both.

## The name cache

`journal/state/slack_user_names.json`, a flat `{user_id: display_name}` map maintained by the
slack-push listener. 742 entries as of writing. A cache miss degrades to the id rather than to
nothing: `@U0NOBODY` is poor, and an empty string is worse, because it silently removes a person
from a sentence about who was asked.
"""

import json
import os
import re

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
NAMES_PATH = os.path.join(REPO_ROOT, 'journal', 'state', 'slack_user_names.json')

_NAMES_CACHE = {'mtime': None, 'names': {}}

def user_names(path=NAMES_PATH):
    """The id -> name map, re-read only when the file changes."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    if _NAMES_CACHE['mtime'] == mtime:
        return _NAMES_CACHE['names']
    try:
        with open(path, encoding='utf-8') as fh:
            names = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return _NAMES_CACHE['names']
    if isinstance(names, dict):
        _NAMES_CACHE.update(mtime=mtime, names=names)
    return _NAMES_CACHE['names']

def _token(body, names):
    """One `<...>` token, already stripped of its brackets, as a person would say it."""
    # Slack puts an optional display label after a pipe. It is authoritative when present, because
    # it is what the sender saw as they typed.
    target, _, label = body.partition('|')
    label = label or None

    if target.startswith('@'):
        if label:
            return '@' + label
        uid = target[1:]
        return '@' + names.get(uid, uid)
    if target.startswith('#'):
        return '#' + (label or target[1:])
    if target.startswith('!'):
        # `<!subteam^S123|@group>` is the one broadcast form carrying its own name.
        if label:
            return '@' + label.lstrip('@')
        return '@' + target[1:].split('^')[0]
    # Everything else is a link. The label is the point of it; the bare URL is the fallback.
    if label:
        return label
    return target[len('mailto:'):] if target.startswith('mailto:') else target

def render(text, names=None, collapse=False):
    """Slack's wire text as words.

    `collapse` squeezes all whitespace to single spaces, for a one-line surface. Off by default:
    a reply draft quoting a bulleted message wants to keep its line breaks.
    """
    if not text:
        return ''
    names = user_names() if names is None else names

    out = []
    rest = text
    while True:
        open_at = rest.find('<')
        if open_at < 0:
            out.append(rest)
            break
        out.append(rest[:open_at])
        after = rest[open_at + 1:]
        close_at = after.find('>')
        if close_at < 0:
            # An unbalanced '<' is ordinary text, not the start of a token. Eating the rest of the
            # message because of one would be worse than leaving it visible.
            out.append('<')
            rest = after
            continue
        out.append(_token(after[:close_at], names))
        rest = after[close_at + 1:]

    # Only these three, and only after the tokens are gone: Slack escapes exactly these on the way
    # out, and '&amp;' has to be last or '&amp;lt;' decodes twice.
    joined = ''.join(out).replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
    return ' '.join(joined.split()) if collapse else joined

MENTION_RE = re.compile(r'<@([A-Z0-9]{6,})(?:\|([^>]*))?>')
BROADCAST_RE = re.compile(r'<!(here|channel|everyone)(?:\|[^>]*)?>')

def mentions_only(text, names=None):
    """Rewrite ONLY `<@U...>` and `<!here>`, leaving every other bracket alone.

    For markdown that is not a Slack message but quotes people out of one -- the generated
    follow-up tracker, briefings, notes. Those carry placeholders of their own (`<YYMMDD>`,
    `<SELLER_PREFIX>`, `<NNNN>` are all in the tracker today) and the full `render` above would
    strip the brackets off each of them, quietly turning a template into prose.

    So this is the conservative half: it touches the two forms that are unambiguously Slack and
    nothing else.
    """
    if not text:
        return ''
    names = user_names() if names is None else names

    def one(m):
        uid, label = m.group(1), m.group(2)
        return '@' + (label or names.get(uid, uid))

    return BROADCAST_RE.sub(lambda m: '@' + m.group(1), MENTION_RE.sub(one, text))

if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == '--selftest':
        N = {'<SLACK_ID>': 'the owner (You)', '<SLACK_ID>': 'Muhammad Bilal'}
        cases = [
            ('<@<SLACK_ID>> done, can u validate ? cc <@<SLACK_ID>>',
             '@Muhammad Bilal done, can u validate ? cc @yourhandle (You)'),
            ('ping <@UNOBODY>', 'ping @UNOBODY'),
            ('URL: <https://x.example.com/|x.example.com> and <mailto:a@b.com>',
             'URL: x.example.com and a@b.com'),
            ('<#C1|ecom-core> <!here>', '#ecom-core @here'),
            ('a &amp; b &lt;c&gt;', 'a & b <c>'),
            ('5 < 6 and that is that', '5 < 6 and that is that'),
            ('', ''),
        ]
        bad = 0
        for raw, want in cases:
            got = render(raw, names=N)
            if got != want:
                bad += 1
                print(f'FAIL\n  in:   {raw!r}\n  want: {want!r}\n  got:  {got!r}')
        multi = render('Attendees\n• one\n• two', names=N, collapse=True)
        if multi != 'Attendees • one • two':
            bad += 1
            print(f'FAIL collapse: {multi!r}')
        kept = render('one\ntwo', names=N)
        if kept != 'one\ntwo':
            bad += 1
            print(f'FAIL default keeps newlines: {kept!r}')
        # The conservative half. A template placeholder must survive it untouched, which is the
        # whole reason it exists separately from `render`.
        for raw, want in [
            ('ask <@<SLACK_ID>> about <YYMMDD>', 'ask @yourhandle (You) about <YYMMDD>'),
            ('<@U0NOBODY> and <SELLER_PREFIX>-<NNNN>', '@U0NOBODY and <SELLER_PREFIX>-<NNNN>'),
            ('<!here> ship it', '@here ship it'),
            ('see <https://x.example.com|x>', 'see <https://x.example.com|x>'),
        ]:
            got = mentions_only(raw, names=N)
            if got != want:
                bad += 1
                print(f'FAIL mentions_only\n  in:   {raw!r}\n  want: {want!r}\n  got:  {got!r}')
        print('all slack_text cases pass' if not bad else f'{bad} FAILED')
        raise SystemExit(1 if bad else 0)
    print(render(sys.stdin.read()), end='')
