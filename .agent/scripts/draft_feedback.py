#!/usr/bin/env python3
"""draft_feedback.py - Track the owner's corrections on message drafts, week by week.

Every time a session shows the owner a Slack/WhatsApp/email/comment draft, his next message
is either an approval or a correction. This script mines the Claude Code transcripts for
those pairs, tags each correction with one pattern, and reports the correction rate per
week. Goal: the rate goes down, and any pattern that keeps coming back gets a machine
check instead of another prose rule.

Baseline and pattern definitions: journal/analysis/draft_feedback_baseline_2026-09-27.md

Subcommands:
  collect     scan transcripts, append new draft turns to journal/draft_feedback/turns.jsonl
  classify    tag unlabelled corrections via agy-bridge (--task harvest), fixed taxonomy
  report      write journal/analysis/draft_feedback_weekly.md, print the summary
  weekly      collect + classify + report (the Saturday cron runs this)
  seed-baseline <dir>   import the hand labels from the 27 Sep baseline run (one-off)

Read-only on transcripts. Writes only under journal/draft_feedback/ and journal/analysis/.
"""
import argparse
import collections
import datetime
import glob
import json
import os
import re
import subprocess
import sys

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
DATA_DIR = os.path.join(BASE_DIR, 'journal', 'draft_feedback')
TURNS = os.path.join(DATA_DIR, 'turns.jsonl')
LABELS = os.path.join(DATA_DIR, 'labels.jsonl')
REPORT = os.path.join(BASE_DIR, 'journal', 'analysis', 'draft_feedback_weekly.md')
LINT_LOG = os.path.join(DATA_DIR, 'lint_events.jsonl')
AGY_BRIDGE = os.path.join(BASE_DIR, '.agent', 'skills', 'agy-bridge', 'run.py')

# Transcript stores for this repo, per machine. Missing ones are skipped. The WSL cron
# reaches the Windows store through /mnt/c. macOS sessions are only counted when the
# script runs on the Mac.
HOME = os.path.expanduser('~')
ROOTS = [
    os.path.join(HOME, '.claude/projects/C--Users-the owner--gemini-antigravity-scratch-product-second-brain'),
    os.path.join(HOME, '.claude/projects/-home-you-antigravity-projects-product-second-brain'),
    os.path.join(HOME, '.claude/projects/-Users-you-product-second-brain'),
    '/mnt/c/Users/you/.claude/projects/C--Users-the owner--gemini-antigravity-scratch-product-second-brain',
    '//wsl.localhost/Ubuntu~/.claude/projects/-home-you-antigravity-projects-product-second-brain',
]

PATTERNS = {
    'NOT_NEEDED': 'Draft should not exist: already replied by hand, handled elsewhere, or a reaction was enough',
    'EXTRA_LINE': 'One added sentence had to be cut: offer, pressure citing an old item, history, extra scope',
    'WRONG_FACT': 'Wrong fact, owner, name, or an assumption not checked',
    'TOO_MUCH': 'Too long or too complicated; "just say X"',
    'MISSED_CONTEXT': 'Missed thread or offline context, answered the wrong thing',
    'VENUE_TAGS': 'Wrong venue (DM vs channel) or missing @handle',
    'AI_VOICE': 'Reads like AI, confusing, not down to earth',
    'ACT_NOT_TALK': 'Should have been an action (invite, ticket, reaction) not a message',
    'LINKS': 'Missing link or source',
    'LANGUAGE': 'Wrong language',
}

DRAFT_HINT = re.compile(
    r"(slack|whatsapp|\bDM\b|thread|reply draft|draft (reply|message|to)|"
    r"message to|balasan|pesan ke|email to|comment on|chase|--approved|venue)", re.I)
SHOWS_TEXT = re.compile(r"(^>\s|```|^\s*\*\*(to|draft|venue)|^draft\b)", re.I | re.M)
BARE_APPROVAL = re.compile(
    r"^\s*(ok(e|ay)?|oke+|sip|gas|go|send|kirim(in)?|approved?|yes|ya|y|lanjut|setuju|"
    r"mantap|good|lgtm|done|send it|kirim aja|ok kirim|gas kirim|approve|looks good,? send( it)?)"
    r"[\s.!,]*$", re.I)
NOISE = ('<system-reminder>', '<task-notification>', '<command-name>', '<command-message>',
         '<local-command', '[Request interrupted', 'Caveat:', '<bash-')
SECRET = re.compile(r'\b(cfat_|xox[bpas]-|sk-|ghp_|AIza)[A-Za-z0-9_\-]{8,}')

def _read_jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding='utf-8') as f:
        return [json.loads(l) for l in f if l.strip()]

def _append_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'a', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')

def _text(content):
    if isinstance(content, str):
        return content
    return '\n'.join(b.get('text', '') for b in content or []
                     if isinstance(b, dict) and b.get('type') == 'text')

def _human(d):
    if d.get('type') != 'user' or d.get('isSidechain') or d.get('isMeta'):
        return None
    c = d.get('message', {}).get('content')
    if isinstance(c, list) and any(isinstance(b, dict) and b.get('type') == 'tool_result' for b in c):
        return None
    t = _text(c).strip()
    if not t or t.startswith(NOISE):
        return None
    t = re.sub(r'<system-reminder>.*?</system-reminder>', '', t, flags=re.S).strip()
    return t or None

def scan_file(path):
    """Yield one record per draft-showing assistant turn followed by a human message."""
    last = []
    with open(path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get('isSidechain'):
                continue
            if d.get('type') == 'assistant':
                t = _text(d.get('message', {}).get('content')).strip()
                if t:
                    last.append(t)
                continue
            h = _human(d)
            if h is None:
                continue
            asst = '\n'.join(last[-3:])
            last = []
            if not (asst and DRAFT_HINT.search(asst) and SHOWS_TEXT.search(asst)):
                continue
            yield {
                'key': f"{os.path.basename(path)[:8]}:{d.get('timestamp', '')}",
                'ts': d.get('timestamp', ''),
                'approval': bool(BARE_APPROVAL.match(h)),
                'draft_turn': SECRET.sub('[REDACTED]', asst[-1500:]),
                'owner': SECRET.sub('[REDACTED]', h[:1500]),
            }

def cmd_collect(_=None):
    seen = {r['key'] for r in _read_jsonl(TURNS)}
    new = []
    files = sorted({os.path.realpath(f) for r in ROOTS if os.path.isdir(r)
                    for f in glob.glob(os.path.join(r, '*.jsonl'))})
    for f in files:
        for rec in scan_file(f):
            if rec['key'] not in seen:
                seen.add(rec['key'])
                new.append(rec)
    new.sort(key=lambda r: r['ts'])
    _append_jsonl(TURNS, new)
    print(f'collect: {len(files)} transcripts, {len(new)} new draft turns')
    return new

def _prompt(batch):
    cats = '\n'.join(f'{k}: {v}' for k, v in PATTERNS.items())
    # Numeric ids: a model echoes "7" faithfully, but cut the long session:timestamp
    # keys at the first colon-T (W40), so those labels never matched.
    items = '\n\n'.join(
        f"ID: {i}\nDRAFT SHOWN (tail): {r['draft_turn'][-900:]}\nBRIAN REPLIED: {r['owner'][:600]}"
        for i, r in enumerate(batch, 1))
    return (
        'An AI assistant showed the owner a draft of an outbound message (Slack, WhatsApp, email, '
        'comment). For each item, decide whether OWNER REPLIED is a correction of that draft. '
        'New unrelated tasks, questions about other topics, and feedback on documents or code '
        'are NONE. "drop this", "replied manually", "handled already" are NOT_NEEDED.\n\n'
        'Most replies are NOT corrections. Answer NONE unless the owner changes, rejects, or drops '
        'the outbound MESSAGE text itself. These are always NONE: comments left on a document, '
        'PRD, MoM, mockup, slide or file ("I left 4 comments on ...md"); UI or design feedback '
        '("kotak kuning ini nutupin"); instructions about the harness, app or code; approval '
        'with an unrelated extra task ("ok send, and also create a ticket"); the owner answering a '
        'question the assistant asked; the owner typing his own new message; a plain approval '
        '("send", "ok send it"). One line per ID, one pattern per ID. A sentence he wants '
        'added because the draft left out a fact he gives is MISSED_CONTEXT only if the fact '
        'was available in the thread; otherwise NONE.\n\n'
        f'Patterns (pick exactly one, or NONE):\n{cats}\n\n'
        'Output one line per item and nothing else:\nID<TAB>PATTERN<TAB>the owner\'s key words, max 120 chars\n\n'
        f'{items}\n')

def _claude_haiku(prompt_file):
    """Run the classify prompt on Haiku through the Claude CLI, or None if there is none."""
    import shutil
    exe = shutil.which('claude')
    if not exe:
        return None
    with open(prompt_file, encoding='utf-8') as f:
        prompt = f.read()
    try:
        return subprocess.run([exe, '-p', '--model', 'haiku'], input=prompt, capture_output=True,
                              text=True, encoding='utf-8', timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        return None

def unlabelled(week):
    """Corrections in `week` that no classifier has labelled yet."""
    labelled = {r['key'] for r in _read_jsonl(LABELS)}
    return sum(1 for r in _read_jsonl(TURNS)
               if r['ts'] and _week(r['ts']) == week and not r['approval'] and r['key'] not in labelled)

def cmd_classify(_=None):
    labelled = {r['key'] for r in _read_jsonl(LABELS)}
    todo = [r for r in _read_jsonl(TURNS) if not r['approval'] and r['key'] not in labelled]
    out = []
    for i in range(0, len(todo), 30):
        batch = todo[i:i + 30]
        tmp = os.path.join(DATA_DIR, '.classify_prompt.txt')
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(_prompt(batch))
        res = subprocess.run([sys.executable, AGY_BRIDGE, '--task', 'harvest', '--prompt-file', tmp,
                              '--timeout', '300'], capture_output=True, text=True, encoding='utf-8')
        by = 'agy'
        if res.returncode == 3:
            # The bridge's fallback_to_claude sentinel (W40: agy quota exhausted for four
            # days). Honour it with Haiku when the Claude CLI is on this machine.
            res, by = _claude_haiku(tmp), 'haiku'
        if res is None or res.returncode != 0:
            print(f'classify: no classifier available (agy-bridge and Claude CLI), '
                  f'{len(todo) - i} left unlabelled', file=sys.stderr)
            break
        os.remove(tmp)
        keys = {str(n): r['key'] for n, r in enumerate(batch, 1)}
        done = set()
        for line in res.stdout.splitlines():
            parts = line.strip().split('\t')
            if len(parts) >= 2 and parts[0].strip() in keys and parts[0].strip() not in done:
                done.add(parts[0].strip())
                pat = parts[1].strip().upper()
                pat = pat if pat in PATTERNS else 'NONE'
                out.append({'key': keys[parts[0].strip()], 'pattern': pat,
                            'quote': parts[2][:200] if len(parts) > 2 else '', 'by': by})
    _append_jsonl(LABELS, out)
    print(f'classify: {len(todo)} to label, {len(out)} labelled')

def _week(ts):
    y, w, _ = datetime.date.fromisoformat(ts[:10]).isocalendar()
    return f'{y}-W{w:02d}'

def report_week(today=None):
    """The week the report is for: the ISO week holding the most recent Saturday.

    On Saturday that is the current week; on Sunday or Monday (the catch-up runs) it is
    still the week that just ended, not the one or two days of the new week."""
    today = today or datetime.date.today()
    sat = today - datetime.timedelta(days=(today.weekday() - 5) % 7)
    y, w, _ = sat.isocalendar()
    return f'{y}-W{w:02d}'

def _reported_week():
    try:
        with open(REPORT, encoding='utf-8') as f:
            m = re.search(r'## Patterns in (\d{4}-W\d{2})', f.read())
        return m.group(1) if m else None
    except OSError:
        return None

def lint_counts(week):
    """How often the draft checks fired that week (draft_lint_stop.py log)."""
    c = collections.Counter()
    for r in _read_jsonl(LINT_LOG):
        if r.get('ts') and _week(r['ts']) == week and r.get('severity') == 'block':
            c[r.get('pattern')] += 1
    return c

def cmd_report(_=None, week=None):
    turns = _read_jsonl(TURNS)
    labels = {r['key']: r for r in _read_jsonl(LABELS)}
    shown = collections.Counter(_week(r['ts']) for r in turns if r['ts'])
    fixes = collections.defaultdict(collections.Counter)
    quotes = collections.defaultdict(list)
    for r in turns:
        lab = labels.get(r['key'])
        if lab and lab['pattern'] in PATTERNS:
            wk = _week(r['ts'])
            fixes[wk][lab['pattern']] += 1
            q = lab.get('quote') or r['owner'][:120]
            quotes[(wk, lab['pattern'])].append(' '.join(q.split()).replace('"', "'"))
    target = week or report_week()
    weeks = sorted(w for w in shown if w <= target)[-8:]
    lines = ['# Draft Feedback, Weekly', '',
             f'Generated {datetime.datetime.now().strftime("%Y-%m-%d %H:%M")} by `.agent/scripts/draft_feedback.py report`. '
             'Baseline and pattern definitions: '
             '[draft_feedback_baseline_2026-09-27.md](draft_feedback_baseline_2026-09-27.md).', '',
             '## Correction rate', '', '| Week | Drafts shown | Corrections | Rate |', '| :--- | ---: | ---: | ---: |']
    for wk in weeks:
        n = sum(fixes[wk].values())
        lines.append(f'| {wk} | {shown[wk]} | {n} | {100 * n / max(shown[wk], 1):.0f}% |')
    last = weeks[-1] if weeks else None
    pending = unlabelled(last) if last else 0
    if pending:
        lines += ['', f'**{pending} replies in {last} are not classified yet** (no classifier was '
                  'available), so its rate is incomplete. The hourly catch-up retries until they are.']
    lines += ['', f'## Patterns in {last}', '', '| Pattern | This week | Previous 4 weeks |', '| :--- | ---: | ---: |']
    prev = collections.Counter()
    for wk in weeks[-5:-1]:
        prev.update(fixes[wk])
    for p in PATTERNS:
        if fixes[last][p] or prev[p]:
            lines.append(f'| {p} | {fixes[last][p]} | {prev[p]} |')
    repeat = [p for p in PATTERNS if fixes[last][p] >= 3]
    lines += ['', '## Needs a machine check', '']
    if repeat:
        lines.append('These came back three or more times this week. The prose rule is not holding:')
        lines.append('')
        for p in repeat:
            lines.append(f'- **{p}** ({fixes[last][p]}): {PATTERNS[p]}')
            for q in quotes[(last, p)][:3]:
                lines.append(f'  - "{q}"')
    else:
        lines.append('Nothing repeated three or more times this week.')
    fired = lint_counts(last) if last else {}
    lines += ['', '## Machine checks fired', '',
              'Times `draft_lint_stop.py` blocked a draft in chat before the owner saw it '
              '(checks shipped 5 Oct 2026, W41):', '']
    lines.append(', '.join(f'{p} {n}' for p, n in sorted(fired.items())) if fired else 'None logged this week.')
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    if last:
        n = sum(fixes[last].values())
        print(f'report: {last} {n}/{shown[last]} corrected ({100 * n / max(shown[last], 1):.0f}%), '
              f'repeating: {", ".join(repeat) or "none"} -> {REPORT}')

def cmd_seed(args):
    """Import the 27 Sep hand labels. Every baseline correction not hand-labelled is NONE."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('classify', os.path.join(args.dir, 'classify.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    by_id = {}
    for b in glob.glob(os.path.join(args.dir, 'batch_*.jsonl')):
        for r in _read_jsonl(b):
            by_id[(r['session'], r['ts'])] = r['id']
    have = {r['key'] for r in _read_jsonl(LABELS)}
    out = []
    for r in _read_jsonl(TURNS):
        if r['approval'] or r['key'] in have or r['ts'] > '2026-09-27T11':
            continue
        sid = by_id.get((r['key'].split(':', 1)[0], r['ts']))
        pat = mod.L.get(sid, 'NONE') if sid is not None else 'NONE'
        out.append({'key': r['key'], 'pattern': pat, 'quote': r['owner'][:120], 'by': 'hand-2026-09-27'})
    _append_jsonl(LABELS, out)
    print(f'seed: {len(out)} baseline labels, {sum(o["pattern"] != "NONE" for o in out)} corrections')

def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding='utf-8')
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description='Weekly draft-feedback tracking')
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('collect', 'classify', 'report', 'weekly'):
        p = sub.add_parser(name)
        if name in ('report', 'weekly'):
            p.add_argument('--week', help='ISO week to report, e.g. 2026-W40 (default: '
                           'the week holding the most recent Saturday)')
        if name == 'weekly':
            p.add_argument('--if-due', action='store_true',
                           help='exit quietly when the report already covers that week')
    sp = sub.add_parser('seed-baseline')
    sp.add_argument('dir')
    a = ap.parse_args()
    if a.cmd == 'seed-baseline':
        return cmd_seed(a)
    week = getattr(a, 'week', None)
    if a.cmd == 'weekly' and a.if_due:
        # Cron never replays a missed slot, and the WSL host is often off at 14:30 on a
        # Saturday (W40 was lost that way: host up at 16:00 on 3 Oct). So the job runs
        # every hour Saturday to Monday and does the work once per report week.
        due = week or report_week()
        if _reported_week() == due and not unlabelled(due):
            return
        print(f'{datetime.datetime.now():%Y-%m-%d %H:%M} weekly: {due} is due')
    if a.cmd in ('collect', 'weekly'):
        cmd_collect()
    if a.cmd in ('classify', 'weekly'):
        cmd_classify()
    if a.cmd in ('report', 'weekly'):
        cmd_report(week=week)
    if a.cmd == 'weekly':
        commit_outputs()

def commit_outputs():
    """Cron path: commit and push only this script's outputs, never the whole tree."""
    paths = [os.path.relpath(p, BASE_DIR) for p in (TURNS, LABELS, REPORT, LINT_LOG) if os.path.exists(p)]
    def git(*args):
        return subprocess.run(['git', '-C', BASE_DIR, *args], capture_output=True, text=True)
    git('add', *paths)
    if git('diff', '--cached', '--quiet').returncode == 0:
        print('commit: nothing new')
        return
    git('commit', '-q', '-m', 'chore(draft-feedback): weekly eval', '--', *paths)
    pull = git('pull', '-q', '--rebase', '--autostash')
    push = git('push', '-q')
    print(f'commit: pushed' if push.returncode == 0 else
          f'commit: push failed ({(pull.stderr or push.stderr).strip()[:200]})')

if __name__ == '__main__':
    main()
