"""Text gates for anything leaving the machine to a human: dashes and Fathom links.

Extracted from `.claude/hooks/send_slop_guard.py` so the hook and the sending scripts
(Slack post and update, Gmail send, gdoc_comment, WhatsApp helpers) refuse the same
text for the same reason, with the same wording.

Both checks here are hard blocks, as in the hook:

  em-dash / en-dash   The repo override bans them outright. Override for a verbatim
                      quote: SLOP_GUARD_ALLOW_EMDASH=1 on the command.
  fathom.video/calls  The internal Fathom link needs an account and a manual approval,
                      so the recipient cannot open it. Override when the recipient is
                      on the owner's Fathom team: SLOP_GUARD_ALLOW_FATHOM_CALLS=1.

The hook cannot see the environment of the command it inspects, so it passes
`allowed=True` after matching the `NAME=1` prefix in the command text. A script sees
the same prefix as a real environment variable, so `env_allows()` reads that. One
override, two readers.

The slop-word, budget, voice and Slack-handle checks stay in the hook: they warn
rather than block, or need the Slack index, and a warning has no business stopping a
script that may run unattended.
"""
import json
import os
import re
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))

# A fathom.video/calls/<id> link is the INTERNAL one: it needs a Fathom account and a
# manual approval from the owner before anybody can watch. On 31 Aug 2026 one went into
# a Slack thread with ExampleVendor, and the next morning Teammate Meer was asking the owner for
# access instead of watching the demo. Fathom already gives every recording a public
# share_url that opens with no account. So this is a link that is simply the wrong one,
# always, in any message leaving the machine, which makes it a deny rather than a warning.
FATHOM_CALL_RE = re.compile(r'https?://(?:www\.)?fathom\.video/calls/([A-Za-z0-9_-]+)')
FATHOM_CACHE = os.path.join(REPO_ROOT, 'journal', 'state', 'fathom_share_links.json')

DASHES = ('\u2014', '\u2013')          # em-dash, en-dash

ENV_ALLOW_EMDASH = 'SLOP_GUARD_ALLOW_EMDASH'
ENV_ALLOW_FATHOM = 'SLOP_GUARD_ALLOW_FATHOM_CALLS'

# Every override a send script must refuse to read from a credentials file. token.env
# supplies credentials only; if it could set one of these, a file would un-gate every send.
# SLOP_GUARD_ALLOW_PLAIN_NAMES belongs to gates/handles.py (it cannot be imported here
# without a cycle), but it is listed so token.env cannot set it either.
OVERRIDE_ENV_NAMES = (ENV_ALLOW_EMDASH, ENV_ALLOW_FATHOM, 'CLAIM_GUARD_DISABLE',
                      'SLOP_GUARD_ALLOW_PLAIN_NAMES')

def env_allows(name, env=None):
    """True when the override `NAME=1` is set in the environment of this process."""
    env = os.environ if env is None else env
    return str(env.get(name, '')).strip() == '1'

def command_allows(name, command):
    """True when the override `NAME=1` is written on the command line (hook side)."""
    return bool(re.search(r'(?<![\w-])' + re.escape(name) + r'=1(?![\w-])', command))

def fathom_share_hint(call_ids, cache_path=None):
    """The share link for each call id, when share-link has already cached it.

    Read-only and local by design: a gate must not make a network call on the way to a
    send, so an uncached id gets the command to run instead of a silent stall.
    """
    try:
        with open(os.path.abspath(cache_path or FATHOM_CACHE)) as f:
            cache = json.load(f)
    except Exception:
        cache = {}
    lines = []
    for cid in call_ids:
        hit = (cache.get(cid) or {}).get('share_url')
        if hit:
            lines.append(f'{cid} -> {hit}')
        else:
            lines.append(
                f'{cid} -> not cached, run: python3 .agent/skills/fathom-connector/'
                f'scripts/fathom_client.py --action share-link --id {cid}')
    return lines

def fathom_refusal(text, origin, allowed=False):
    """Reason string when `text` carries an internal Fathom link, else None."""
    if allowed or not text:
        return None
    calls = list(dict.fromkeys(FATHOM_CALL_RE.findall(text)))
    if not calls:
        return None
    return (
        'FATHOM LINK GATE - the text about to be sent ({}) carries an '
        'internal fathom.video/calls/ link. That link needs a Fathom '
        'account and a manual approval from the owner, so the recipient '
        'cannot open it and will ask for access instead. Send the '
        'public share link: {}. Then re-run the send. Override with '
        'SLOP_GUARD_ALLOW_FATHOM_CALLS=1 only when the recipient is '
        'already on the owner\'s Fathom team.'
    ).format(origin, '; '.join(fathom_share_hint(calls)))

def find_dashes(text):
    return [c for c in DASHES if c in (text or '')]

def dash_refusal(text, origin, allowed=False, extra=None):
    """Reason string when `text` carries an em-dash or en-dash, else None.

    `extra` is the hook's "also present" list (banned words, budget...), appended so
    one refusal carries every fix. Scripts pass nothing.
    """
    if allowed:
        return None
    dashes = find_dashes(text)
    if not dashes:
        return None
    found = ' and '.join(f'"{c}"' for c in dashes)
    reason = (
        f'NO-AI-SLOP GATE - {found} in the text about to be sent ({origin}). '
        'The repo override bans em-dash and en-dash outright: reframe the '
        'sentence rather than swapping in a hyphen, then re-run the send. '
        'Quoting someone verbatim is the one exception: prefix the command '
        'with SLOP_GUARD_ALLOW_EMDASH=1.'
    )
    if extra:
        reason += ' Also present, fix in the same pass: ' + '; '.join(extra) + '.'
    return reason

def refusals(text, origin, env=None):
    """Every hard refusal for `text`, using environment overrides. Order matches the hook."""
    out = []
    r = fathom_refusal(text, origin, env_allows(ENV_ALLOW_FATHOM, env))
    if r:
        out.append(r)
    r = dash_refusal(text, origin, env_allows(ENV_ALLOW_EMDASH, env))
    if r:
        out.append(r)
    return out

def eprint(message):
    """Print to stderr without ever raising on a narrow console encoding.

    The refusal text quotes the banned character itself, and a cp1252 or ascii console
    cannot always encode it. A gate that dies on its own error message still stops the
    send, but with a traceback instead of the reason.
    """
    try:
        print(message, file=sys.stderr)
    except UnicodeEncodeError:
        enc = getattr(sys.stderr, 'encoding', None) or 'ascii'
        print(message.encode(enc, 'replace').decode(enc), file=sys.stderr)

def enforce(text, origin, env=None):
    """Script-side gate. Refuse (stderr, exit 1) before any network call, else return.

    A gate bug lets the send continue, because the approval gate still stands behind
    it. A clear violation always stops.
    """
    try:
        reasons = refusals(text, origin, env)
    except Exception:
        return
    if not reasons:
        return
    for r in reasons:
        eprint('[BLOCKED] ' + r)
    eprint('Nothing sent.')
    sys.exit(1)

def enforce_dashes(text, origin, env=None):
    """Script-side em-dash gate alone, for ticket text (Linear and Jira writes).

    Same refusal, wording and override (SLOP_GUARD_ALLOW_EMDASH=1) as `enforce`, but no
    Fathom link check: that one exists for a recipient who cannot open the link, and the
    ticket writers were only ever asked for the dash ban. A gate bug lets the write
    continue, because `--approved` still stands behind it.
    """
    try:
        reason = dash_refusal(text, origin, env_allows(ENV_ALLOW_EMDASH, env))
    except Exception:
        return
    if not reason:
        return
    eprint('[BLOCKED] ' + reason)
    eprint('Nothing sent.')
    sys.exit(1)

def _main(argv=None):
    """CLI for senders that are not Python or live outside this repo (WhatsApp helpers).

      python3 .agent/scripts/gates/outbound.py check --origin whatsapp --text-file draft.txt
      python3 .agent/scripts/gates/outbound.py check --origin whatsapp --text "hello"

    Exit 0 clean, exit 1 refused (reason on stderr). Same overrides as the hook:
    SLOP_GUARD_ALLOW_EMDASH=1 / SLOP_GUARD_ALLOW_FATHOM_CALLS=1 in the environment.
    """
    import argparse
    ap = argparse.ArgumentParser(description='Outbound text gate (em-dash, internal Fathom link)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('check')
    c.add_argument('--origin', default='outbound message')
    g = c.add_mutually_exclusive_group(required=True)
    g.add_argument('--text')
    g.add_argument('--text-file')
    args = ap.parse_args(argv)
    text = args.text
    if args.text_file:
        with open(args.text_file, encoding='utf-8-sig') as f:
            text = f.read()
    enforce(text, args.origin)
    print('outbound gate: clean')
    return 0

if __name__ == '__main__':
    sys.exit(_main())
