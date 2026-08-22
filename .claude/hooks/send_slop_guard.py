#!/usr/bin/env python3
"""PreToolUse hook on Bash: check the no-ai-slop rules on text about to LEAVE the machine.

The hole this closes. `emdash_guard.py` only sees Write/Edit into a repo .md/.txt
file, so it catches a draft on its way into `journal/`, and nothing on its way out
to a person. A Slack send built inline

    slack_client.py --action post --channel X --text "Hi Teammate - quick nudge ..." --approved

never touches a file, so until now the single machine-checked rule in the whole
no-ai-slop gate did not apply to the one moment that is irreversible. Short
messages, the most common kind, were the least protected.

Two severities, matching how the skill itself is written
(`.agent/skills/no-ai-slop/SKILL.md`):

  em-dash / en-dash  -> permissionDecision "deny". The repo override bans these
                        outright, no exceptions. "ask" was the first design and
                        was measured to be decorative here: this repo runs
                        defaultMode bypassPermissions, so an ask never reaches a
                        prompt and the send goes out anyway. Deny is the only
                        severity that actually stops a banned character from
                        reaching a client, and the fix costs one rewritten
                        sentence. A clean send is never touched.

                        Escape hatch for the real exception, quoting someone
                        verbatim: prefix the command with
                        SLOP_GUARD_ALLOW_EMDASH=1.

  slop words/phrases -> additionalContext only. These need judgment (a banned
                        word can be the right word inside a quote), so the hook
                        names what it found and leaves the call to the drafter.

Scope: `slack_client.py` in either connector (primary + secondary), both `--text`
and `--text-file`. Other outbound paths (Gmail, Jira/GDoc comments via MCP) still
have no machine check; they need their own matcher, not a wider Bash regex.

Contract: always exit 0. A crash here must never break a send.
"""
import json
import os
import re
import shlex
import sys

MAX_FILE_BYTES = 200_000

# Banned outright (SKILL.md "Words to cut"). `harness` is deliberately omitted:
# this repo calls the Claude setup "the harness" in almost every message, so
# matching it would cry wolf on every send.
BANNED_WORDS = [
    'delve', 'foster', 'leverage', 'utilize', 'facilitate', 'empower',
    'streamline', 'robust', 'cutting-edge', 'paradigm shift', 'game changer',
    'this is huge', 'this changes everything', 'tapestry', 'realm', 'beacon',
    'multifaceted', 'meticulous', 'intricate', 'paramount', 'transformative',
    'elevate', 'embark', 'supercharge', 'ever-evolving',
]

# Multi-word only, so a single ordinary word never trips these (SKILL.md
# "Often-empty phrases", "Slack-specific filler", and the pattern openers).
BANNED_PHRASES = [
    "it's worth noting", 'it is worth noting', "it's important to note",
    'at the end of the day', 'when it comes to', 'at its core',
    "in today's world", 'in the age of', 'the reality is', 'the truth is',
    'in terms of', 'with regard to', 'going forward', "let's dive in",
    'just following up on this', 'hope this finds you well',
    'hope this message finds you well', 'wanted to circle back',
    "here's the thing", 'let me be clear', "i'll be honest",
    'the uncomfortable truth is', 'what most people get wrong',
    "here's what nobody tells you", 'the part everyone misses',
    'stands as a testament', 'marks a pivotal moment', 'plays a vital role',
    'underscores its significance', 'the key point is', 'as you can see',
    'this distinction matters', 'in other words', 'per discussion',
    'as aligned', 'experts agree', 'what if i told you', 'think about it:',
    'plot twist:', 'in conclusion', 'please do not hesitate',
    "please don't hesitate", 'let me know if you have any questions',
]

SEND_MARKERS = ('slack_client.py',)

def read_payload():
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ''
    except Exception:
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None

def outbound_text(command):
    """The text this command would send: --text inline, or --text-file's contents.
    Returns (text, origin) or (None, None)."""
    try:
        tokens = shlex.split(command, comments=False, posix=True)
    except ValueError:
        # unbalanced quotes: fall back to a raw scan so a weird command still
        # gets checked rather than silently skipped
        return command, 'raw command'

    for i, tok in enumerate(tokens):
        if tok == '--text' and i + 1 < len(tokens):
            return tokens[i + 1], '--text'
        if tok.startswith('--text='):
            return tok.split('=', 1)[1], '--text'
        path = None
        if tok in ('--text-file', '--text_file') and i + 1 < len(tokens):
            path = tokens[i + 1]
        elif tok.startswith('--text-file='):
            path = tok.split('=', 1)[1]
        if path:
            try:
                if os.path.getsize(path) > MAX_FILE_BYTES:
                    return None, None
                with open(path, encoding='utf-8', errors='replace') as f:
                    return f.read(), f'--text-file {os.path.basename(path)}'
            except OSError:
                return None, None
    return None, None

def find_words(text):
    low = text.lower()
    hits = []
    for w in BANNED_WORDS:
        if re.search(r'(?<![\w-])' + re.escape(w) + r'(?![\w-])', low):
            hits.append(w)
    return hits

def find_phrases(text):
    low = ' '.join(text.lower().split())
    return [p for p in BANNED_PHRASES if p in low]

def main():
    d = read_payload()
    if not d:
        sys.exit(0)

    command = str((d.get('tool_input') or {}).get('command') or '')
    if not any(m in command for m in SEND_MARKERS):
        sys.exit(0)

    text, origin = outbound_text(command)
    if not text or not text.strip():
        sys.exit(0)

    dashes = [c for c in ('—', '–') if c in text]
    words = find_words(text)
    phrases = find_phrases(text)

    # verbatim-quote escape hatch, set on the command itself
    if dashes and re.search(r'(?<![\w-])SLOP_GUARD_ALLOW_EMDASH=1(?![\w-])', command):
        dashes = []

    if dashes:
        found = ' and '.join(f'"{c}"' for c in dashes)
        reason = (
            f'NO-AI-SLOP GATE - {found} in the text about to be sent ({origin}). '
            'The repo override bans em-dash and en-dash outright: reframe the '
            'sentence rather than swapping in a hyphen, then re-run the send. '
            'Quoting someone verbatim is the one exception: prefix the command '
            'with SLOP_GUARD_ALLOW_EMDASH=1.'
        )
        extra = []
        if words:
            extra.append('banned words: ' + ', '.join(words[:6]))
        if phrases:
            extra.append('slop phrases: ' + ', '.join(phrases[:4]))
        if extra:
            reason += ' Also present, fix in the same pass: ' + '; '.join(extra) + '.'
        print(json.dumps({
            'hookSpecificOutput': {
                'hookEventName': 'PreToolUse',
                'permissionDecision': 'deny',
                'permissionDecisionReason': reason,
            }
        }))
        sys.exit(0)

    if words or phrases:
        bits = []
        if words:
            bits.append('banned words: ' + ', '.join(words[:6]))
        if phrases:
            bits.append('slop phrases: ' + ', '.join(phrases[:4]))
        print(json.dumps({
            'hookSpecificOutput': {
                'hookEventName': 'PreToolUse',
                'additionalContext': (
                    f'no-ai-slop: outbound text ({origin}) still contains '
                    + '; '.join(bits)
                    + '. Cut them before this goes to a named human '
                      '(.agent/skills/no-ai-slop/SKILL.md).'
                ),
            }
        }))
    sys.exit(0)

if __name__ == '__main__':
    try:
        main()
    except Exception:
        sys.exit(0)   # never break a send on a guard bug
