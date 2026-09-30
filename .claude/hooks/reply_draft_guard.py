#!/usr/bin/env python3
"""PostToolUse hook on Write|Edit: a reply draft must quote what it is replying to.

the owner, 29 Sep 2026: "the thread history and the original message, can you include it
in the summary of the proposed draft for reply drafts? often i am trying to guess the
original message." The rule already existed in memory
(feedback-slack-sending-and-drafts, "Reply Drafts Show Original And Pointers") and was
still skipped: the Lamya SAIB reply draft went to him with a one-line "why" and no
quote. A rule that lives only in recall fails exactly when the drafter is busy, so
this checks the file.

Scope: `.md` files under `journal/drafts/` that are replies, meaning the file name
contains "reply" or the text names a thread (`thread_ts`, `--thread-ts`,
"thread reply"). A fresh message with no original is out of scope.

What must be in the file:
  - an "Original message" label followed by a `>` blockquote, always;
  - a "Thread so far" label followed by a `>` blockquote, when the draft goes into a
    thread. The parent alone is not enough: the reply answers the whole exchange.

On a miss the hook returns decision "block", which hands the reason back to the
drafter to fix the file before the owner sees it. It never touches the send path.
Escape hatch for a genuine exception: a line `<!-- no-original: <why> -->` in the file.
"""
import json
import os
import re
import sys

LABEL_ORIG = re.compile(r'^\s*(?:#+\s*|-\s*)?(?:\*\*)?\s*original\b', re.I)  # '## Original message', '**Original**, Name'
LABEL_THREAD = re.compile(r'thread so far', re.I)
THREAD_HINT = re.compile(r'thread_ts|--thread-ts|thread reply', re.I)

def has_quote_after(text, label):
    """True when a line matching `label` is followed, within 6 lines, by a `>` line."""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if label.search(ln):
            for nxt in lines[i + 1:i + 7]:
                if nxt.lstrip().startswith('>'):
                    return True
    return False

CODE_LANGS = {'bash', 'sh', 'shell', 'json', 'python', 'py', 'yaml', 'yml', 'sql', 'mermaid',
              'gherkin', 'js', 'ts', 'javascript', 'typescript', 'diff', 'powershell', 'ps1'}

def message_fences(text):
    """Count ``` fences that hold prose, not code. A fence never wraps, so a draft in one
    runs off the right edge (the owner, 24 Sep and again 29 Sep 2026). Tagged code fences
    (```bash, ```json ...) are fine; untagged or text-tagged ones are drafts."""
    n, inside = 0, False
    for ln in text.splitlines():
        s = ln.strip()
        if not s.startswith('```'):
            continue
        if not inside and s[3:].strip().lower() not in CODE_LANGS:
            n += 1
        inside = not inside
    return n

def check(path, text):
    """Return a list of what is missing, empty when the draft is fine."""
    name = os.path.basename(path).lower()
    is_thread = bool(THREAD_HINT.search(text))
    missing = []
    fences = message_fences(text)
    if fences:
        missing.append(f'the message text as > blockquotes: {fences} code fence(s) hold draft text, '
                       'and a fence does not wrap, so the owner has to scroll sideways. Put each '
                       'message in > lines; keep ``` only for tagged code (```bash, ```json)')
    if 'reply' not in name and not is_thread:
        return missing
    if '<!-- no-original:' in text:
        return missing
    if not has_quote_after(text, LABEL_ORIG):
        missing.append('an "Original message" section: who sent it, when, the permalink, '
                       'and the full text as a > blockquote')
    if is_thread and not has_quote_after(text, LABEL_THREAD):
        missing.append('a "Thread so far" section: every earlier message in the thread, '
                       'each as "> **Name**, time" then the text as a > blockquote')
    return missing

def main():
    try:
        d = json.loads(sys.stdin.read() or '{}')
    except json.JSONDecodeError:
        return
    path = str((d.get('tool_input') or {}).get('file_path') or '')
    norm = path.replace('\\', '/')
    if not norm.endswith('.md') or '/journal/drafts/' not in norm:
        return
    try:
        with open(path, encoding='utf-8') as fh:
            text = fh.read()
    except OSError:
        return
    missing = check(path, text)
    if not missing:
        return
    print(json.dumps({
        'decision': 'block',
        'reason': (f'Draft {os.path.basename(path)} is missing ' + '; and '.join(missing)
                   + '. the owner asked for this on 29 Sep 2026 so he never has to guess what a '
                   'reply answers or scroll to read it. For a missing original, read the thread '
                   '(slack_read_thread) and add it above the draft, or add '
                   '<!-- no-original: <why> --> if there is truly no original.'),
    }))

if __name__ == '__main__':
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
