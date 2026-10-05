#!/usr/bin/env python3
"""Stop hook: a message draft shown in chat passes the draft checks before the owner reads it.

Most drafts the owner corrects are never written to a file and never reach a send command:
they are `>` quotes in a chat reply, and his correction is his next message. The send
guard and the draft-file guard cannot see that moment. This hook can, because the Stop
event carries the transcript.

At the end of a turn that shows a draft, it runs `.agent/scripts/draft_lint.py` on the
quoted message text and checks one thing about process:

  EXTRA_LINE      an offer, walkthrough, promise of extra work, or history line
  WRONG_FACT      a person's name in lowercase
  AI_VOICE        a message to a person that reads like system internals
  NOT_NEEDED /    a reply draft to a Slack permalink, with no `draft_context.py` run for
  MISSED_CONTEXT  that permalink in this session. That script says whether the owner already
                  answered (by hand or in another session) and prints the thread plus
                  his last messages in the channel.

On a finding it blocks once with the fixes, so the corrected draft is what the owner answers.
`stop_hook_active` stops a loop: the second stop always passes. Every firing is logged to
`journal/draft_feedback/lint_events.jsonl`, which the weekly report counts.

Off switch: DRAFT_LINT_DISABLE=1. Contract: exit 0 always, fail open.
"""
import datetime
import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / '.agent' / 'scripts'))

PERMALINK = re.compile(r'https://[\w.-]*slack\.com/archives/[CDG][A-Z0-9]+/p(\d{16})[^\s)>\]]*')
ASKS_APPROVAL = re.compile(
    r'(?i)\b(kirim|send it|say "?send|say "?go|approve|not sent|belum (di)?kirim|waiting on your|'
    r'reply "?(ok|send|kirim|go)|send as is|siap dikirim|tinggal (bilang|balas))')
DRAFT_HINT = re.compile(r'(?i)(slack|whatsapp|\bDM\b|thread|draft|message|balasan|pesan|email|comment|reply)')
NOISE = ('<system-reminder>', '<task-notification>', '<command-name>', '<local-command', '[Request interrupted')
LOG = ROOT / 'journal' / 'draft_feedback' / 'lint_events.jsonl'

def _text(content):
    if isinstance(content, str):
        return content
    return '\n'.join(b.get('text', '') for b in content or []
                     if isinstance(b, dict) and b.get('type') == 'text')

def read_transcript(path):
    """(assistant text of the current turn, every Bash command in the session)."""
    turn, commands = [], []
    with open(path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get('isSidechain'):
                continue
            msg = d.get('message') or {}
            content = msg.get('content')
            if d.get('type') == 'assistant':
                for b in content if isinstance(content, list) else []:
                    if isinstance(b, dict) and b.get('type') == 'tool_use':
                        cmd = (b.get('input') or {}).get('command')
                        if cmd:
                            commands.append(str(cmd))
                t = _text(content).strip()
                if t:
                    turn.append(t)
            elif d.get('type') == 'user' and not d.get('isMeta'):
                if isinstance(content, list) and any(
                        isinstance(b, dict) and b.get('type') == 'tool_result' for b in content):
                    continue
                t = _text(content).strip()
                if t and not t.startswith(NOISE):
                    turn = []          # a real human message starts a new turn
    return '\n\n'.join(turn), commands

def findings(turn, commands, session):
    import draft_lint
    if not DRAFT_HINT.search(turn):
        return []
    segs = draft_lint.draft_segments(turn)
    if not segs:
        return []
    out = [f for s in segs for f in draft_lint.lint(s)]
    if ASKS_APPROVAL.search(turn):
        ran = '\n'.join(c for c in commands if 'draft_context.py' in c or 'draft_need_check.py' in c)
        urls = dict((m.group(1), m.group(0)) for m in PERMALINK.finditer(turn))
        for ts, url in list(urls.items())[:3]:
            if ts not in ran:
                out.append({'pattern': 'MISSED_CONTEXT', 'severity': 'block',
                            'msg': ('reply draft with no context check. Run: python3 '
                                    '.agent/scripts/draft_context.py --permalink "%s" --session %s . '
                                    'If it says SKIP, drop the draft and say why in one line. '
                                    'Otherwise re-read the thread and the owner\'s last messages it '
                                    'prints and fix the draft') % (url, session)})
    return out

def log(session, found):
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, 'a', encoding='utf-8') as f:
            for x in found:
                f.write(json.dumps({'ts': datetime.datetime.now(datetime.timezone.utc).isoformat()[:19],
                                    'session': session[:8], 'pattern': x['pattern'],
                                    'severity': x['severity'], 'msg': x['msg'][:160]},
                                   ensure_ascii=False) + '\n')
    except OSError:
        pass

def main():
    if os.environ.get('DRAFT_LINT_DISABLE') == '1':
        return
    try:
        d = json.loads(sys.stdin.read() or '{}')
    except ValueError:
        return
    if d.get('stop_hook_active'):
        return
    path = d.get('transcript_path')
    if not path or not os.path.exists(path):
        return
    session = str(d.get('session_id') or '')
    turn, commands = read_transcript(path)
    found = findings(turn, commands, session)
    blocks = [f for f in found if f['severity'] == 'block']
    if not blocks:
        return
    log(session, found)
    lines = ['- %s: %s' % (f['pattern'], f['msg']) for f in found]
    print(json.dumps({
        'decision': 'block',
        'reason': ('DRAFT CHECK. The draft you just showed has issues the owner keeps correcting '
                   '(journal/analysis/draft_feedback_weekly.md):\n' + '\n'.join(lines[:10]) +
                   '\nFix them and show the corrected draft once, with one line saying what '
                   'changed. A warn item is a judgment call: keep it only if it is right for '
                   'this reader. Fixing a draft is not sending it: the approval gate still holds.'),
    }, ensure_ascii=False))

if __name__ == '__main__':
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
