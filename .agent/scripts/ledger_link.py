#!/usr/bin/env python3
"""Turn a ledger ID into a link the owner can click.

`COM-0527` in a tracker, a briefing, or a chat reply is an opaque token: it says
a record exists without saying what it is. The dashboard already holds the whole
record (owner, SLA, source, timeline, notes) and since 12 Aug 2026 serves it at a
permalink, so the fix is mechanical: every ID that reaches the owner ships with that
permalink attached.

    COM-0527  ->  [`COM-0527`](http://localhost:3737/#find/COM-0527)

`localhost` is deliberate and machine-agnostic: whichever checkout the owner reads
the file on, its own dashboard serves the same git-synced ledgers, so the link
resolves there. Override with PSB_DASHBOARD_BASE when the port moves.

Use as a library from any renderer:

    from ledger_link import md, linkify
    md('COM-0527')            # one ID -> markdown link
    linkify(section_text)     # every ID in a block of markdown

or from the shell, as a filter:

    python3 .agent/scripts/ledger_link.py < in.md > out.md
    python3 .agent/scripts/ledger_link.py --url WAIT-0223
"""
import os
import re
import sys

BASE = os.environ.get('PSB_DASHBOARD_BASE', 'http://localhost:3737').rstrip('/')

# The three local ledgers. External tracker keys are NOT included: Jira keys
# (MP-, MPS-, MSP-, MBA-, STOR-) belong to an Atlassian instance and Linear
# identifiers (a team key followed by a number) belong to
# linear.app/yourcompany. Linkifying either here would send the owner to the
# wrong system. Their homes are jql_url()/the Linear issue URL in
# work_tree_link.py, not the local dashboard.
PREFIXES = ('COM', 'WAIT', 'DEC')

_ID = r'(?:' + '|'.join(PREFIXES) + r')-\d{3,5}'

# An ID, optionally wrapped in backticks. Guarded on both sides so it never
# fires inside a longer token (a filename, a slug, a URL path segment).
ID_RE = re.compile(r'(?<![\w/#=-])(`?)(' + _ID + r')\1(?![\w-])')

# Regions that must survive untouched: an existing markdown link (the ID may sit
# in its label or its target), a bare URL, and a fenced code block's payload.
PROTECT_RE = re.compile(
    r'\[[^\]\n]*\]\([^)\n]*\)'   # [label](target)
    r'|https?://\S+'             # bare URL
    r'|`[^`\n]*/#find/[^`\n]*`'  # an already-built permalink inside code ticks
)

def url(record_id):
    """Permalink to one ledger record on the local dashboard."""
    return f'{BASE}/#find/{record_id.strip().upper()}'

def md(record_id, code=True):
    """`COM-0527` -> [`COM-0527`](…/#find/COM-0527). code=False drops the ticks."""
    rid = record_id.strip().upper()
    label = f'`{rid}`' if code else rid
    return f'[{label}]({url(rid)})'

def linkify(text, code=True):
    """Every bare ledger ID in `text` becomes a markdown link. Existing links,
    URLs, and already-built permalinks are left exactly as they are, so running
    this twice is the same as running it once."""
    if not text:
        return text

    out = []
    pos = 0
    for m in PROTECT_RE.finditer(text):
        out.append(_linkify_span(text[pos:m.start()], code))
        out.append(m.group(0))
        pos = m.end()
    out.append(_linkify_span(text[pos:], code))
    return ''.join(out)

def _linkify_span(span, code):
    def repl(m):
        ticks, rid = m.group(1), m.group(2)
        return f'[{ticks}{rid}{ticks}]({url(rid)})' if ticks else md(rid, code=code)
    return ID_RE.sub(repl, span)

def main(argv):
    if len(argv) > 1 and argv[1] in ('--url', '-u'):
        if len(argv) < 3:
            print('usage: ledger_link.py --url COM-0527', file=sys.stderr)
            return 2
        print(url(argv[2]))
        return 0
    if len(argv) > 1 and argv[1] in ('--md', '-m'):
        if len(argv) < 3:
            print('usage: ledger_link.py --md COM-0527', file=sys.stderr)
            return 2
        print(md(argv[2]))
        return 0
    sys.stdout.write(linkify(sys.stdin.read()))
    return 0

if __name__ == '__main__':
    sys.exit(main(sys.argv))
