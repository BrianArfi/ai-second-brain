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

  over budget /       -> additionalContext only. `answer_budget.md` says an outbound
  unasked rationale      message carries what the reader needs to act and stops. The
                         guard counts words (quotes, code blocks and tables excluded)
                         and names a rationale section the reader did not ask for.
                         A warning rather than a block, because a long message is
                         sometimes right and no regex can tell which one this is.

Scope: `slack_client.py` in either connector, `gmail_manager.py`, and
`gdoc_comment.py`. Flags read: `--text`, `--text-file`, `--body`, `--body-file`,
and the `text` fields inside `--items` JSON. Paths that never touch Bash (a
comment posted through an MCP tool) still have no machine check; they need their
own matcher, not a wider Bash regex.

A chat reply to the owner is out of scope by construction. Hooks fire on tool
calls, and no hook sees assistant text on its way to a human, so that half of
`answer_budget.md` runs on the rule alone.

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

# Marker -> (channel name, word ceiling from answer_budget.md). A ceiling triggers a
# cut, not a justification. Quotes, code blocks and tables do not count toward it.
SEND_TARGETS = {
    'slack_client.py': ('Slack', 80),
    'gmail_manager.py': ('email', 150),
    'gdoc_comment.py': ('a document comment', 60),
}
SEND_MARKERS = tuple(SEND_TARGETS)

# Lead-ins that introduce reasoning the reader did not ask for. Multi-word only, so
# an ordinary sentence never trips them.
RATIONALE_LEADINS = [
    'for context', 'some context', 'a bit of context', 'to give you context',
    'for background', 'background:', 'context:', 'rationale:', 'reasoning:',
    'why this matters', 'here is why', "here's why", 'let me explain',
    'to explain why', 'just to explain', 'for your awareness',
    'to walk you through', 'the reason for this is', 'by way of background',
]


def payload_words(text):
    """Words the reader has to read, with the payload excluded.

    A quoted message, a code block and a table of parallel data are the point of
    the message rather than padding, so they do not count. Counting them would
    make the guard fire on exactly the messages that are correct.
    """
    out, in_fence = [], False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('```'):
            in_fence = not in_fence
            continue
        if in_fence or stripped.startswith('>') or stripped.startswith('|'):
            continue
        out.append(line)
    return len(' '.join(out).split())


def find_rationale(text):
    low = ' '.join(text.lower().split())
    return [p for p in RATIONALE_LEADINS if p in low]

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

# Flags that carry a message to a human. Inline flags hold the text itself; file
# flags hold a path to it. `--items` is gdoc_comment's JSON of [{anchor, text}].
INLINE_FLAGS = ('--text', '--body')
FILE_FLAGS = ('--text-file', '--text_file', '--body-file', '--body_file')
JSON_FLAGS = ('--items',)


def read_send_file(path, label):
    try:
        if os.path.getsize(path) > MAX_FILE_BYTES:
            return None, None
        with open(path, encoding='utf-8', errors='replace') as f:
            return f.read(), f'{label} {os.path.basename(path)}'
    except OSError:
        return None, None


def comment_text(path):
    """Every `text` field in a gdoc_comment items file, joined.

    One malformed comment must not hide the other nine, so a file that does not
    parse is skipped rather than guessed at.
    """
    if path == '-':
        return None, None            # stdin: the hook cannot read it without stealing it
    raw, _ = read_send_file(path, '--items')
    if raw is None:
        return None, None
    try:
        items = json.loads(raw)
    except Exception:
        return None, None
    if not isinstance(items, list):
        return None, None
    texts = [str(i.get('text') or '') for i in items if isinstance(i, dict)]
    joined = '\n\n'.join(t for t in texts if t.strip())
    if not joined:
        return None, None
    return joined, f'--items {os.path.basename(path)} ({len(texts)} comment(s))'


def outbound_text(command):
    """The text this command would send. Returns (text, origin) or (None, None)."""
    try:
        tokens = shlex.split(command, comments=False, posix=True)
    except ValueError:
        # unbalanced quotes: fall back to a raw scan so a weird command still
        # gets checked rather than silently skipped
        return command, 'raw command'

    for i, tok in enumerate(tokens):
        for flag in INLINE_FLAGS:
            if tok == flag and i + 1 < len(tokens):
                return tokens[i + 1], flag
            if tok.startswith(flag + '='):
                return tok.split('=', 1)[1], flag
        for flag in FILE_FLAGS:
            if tok == flag and i + 1 < len(tokens):
                return read_send_file(tokens[i + 1], flag)
            if tok.startswith(flag + '='):
                return read_send_file(tok.split('=', 1)[1], flag)
        for flag in JSON_FLAGS:
            if tok == flag and i + 1 < len(tokens):
                return comment_text(tokens[i + 1])
            if tok.startswith(flag + '='):
                return comment_text(tok.split('=', 1)[1])
    return None, None


def send_target(command):
    """(channel, word ceiling) for the first marker this command matches."""
    for marker, target in SEND_TARGETS.items():
        if marker in command:
            return target
    return ('this channel', 0)

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

    channel, ceiling = send_target(command)
    dashes = [c for c in ('—', '–') if c in text]
    words = find_words(text)
    phrases = find_phrases(text)
    rationale = find_rationale(text)
    count = payload_words(text)
    over = ceiling and count > ceiling

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
        if over:
            extra.append(f'{count} words against a {ceiling}-word budget for {channel}')
        if rationale:
            extra.append('unrequested rationale: ' + ', '.join(rationale[:3]))
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

    if words or phrases or over or rationale:
        bits = []
        if words:
            bits.append('banned words: ' + ', '.join(words[:6]))
        if phrases:
            bits.append('slop phrases: ' + ', '.join(phrases[:4]))
        if over:
            bits.append(
                f'{count} words against the {ceiling}-word budget for {channel}, '
                'excluding quotes, code blocks and tables'
            )
        if rationale:
            bits.append('unrequested rationale: ' + ', '.join(rationale[:3]))
        advice = '.agent/skills/no-ai-slop/SKILL.md'
        if over or rationale:
            advice = '.agent/skills/no-ai-slop/answer_budget.md'
        print(json.dumps({
            'hookSpecificOutput': {
                'hookEventName': 'PreToolUse',
                'additionalContext': (
                    f'no-ai-slop: outbound text ({origin}) still contains '
                    + '; '.join(bits)
                    + f'. Cut before this goes to a named human ({advice}).'
                    + (' The budget is a ceiling that triggers a cut, not a length to '
                       'justify. It lifts only when the reader asked for depth.'
                       if (over or rationale) else '')
                ),
            }
        }))
    sys.exit(0)

if __name__ == '__main__':
    try:
        main()
    except Exception:
        sys.exit(0)   # never break a send on a guard bug
