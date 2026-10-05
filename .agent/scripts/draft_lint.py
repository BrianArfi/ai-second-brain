#!/usr/bin/env python3
"""draft_lint.py - Machine checks for the corrections the owner keeps giving on message drafts.

The weekly draft-feedback report (`draft_feedback.py`) showed the same five corrections
coming back three or more times a week in W39 (2026-09-21 to 27), with a prose rule
already in place for each one. A prose rule fails exactly when the drafter is busy, so
each pattern gets a check here instead. One library, four callers:

  - `.claude/hooks/draft_lint_stop.py`   drafts shown in chat (Stop hook, blocks once)
  - `.claude/hooks/reply_draft_guard.py` drafts written to `journal/drafts/`
  - `.claude/hooks/send_slop_guard.py`   the text of an actual send
  - the automated drafters (reply-queue, chase-queue, inbox-hub) via `autofix()`

Pattern -> check:

  EXTRA_LINE      `extra_lines()`  the added sentence he cuts: an offer, a walkthrough, a
                  promise of extra work, pressure citing an old item, a history receipt.
                  `autofix()` deletes the sentence, because deleting it is what he does.
  WRONG_FACT      `name_case()`    a person's name written lowercase ("julie", "YourManager").
                  `unknown_handles()` a typed @Name that resolves to nobody.
                  `owner_claims()` "X owns / prepared / is handling" with no source on the line.
  AI_VOICE        `ai_voice()`     tool and system jargon in a message to a person, or
                  abstract words with nothing concrete under them.
  NOT_NEEDED      `draft_context.py` (separate, needs Slack): the owner already replied or
  MISSED_CONTEXT  reacted, another session owns or sent it, plus the full thread and his
                  last messages in the channel.

CLI:
  draft_lint.py check --text "..." | --file draft.md [--json]
  draft_lint.py replay [--week 2026-W39]   run the checks over corrected and approved
                                           drafts in journal/draft_feedback/ and report
                                           catch rate and false-positive rate per pattern
"""
import argparse
import collections
import datetime
import json
import os
import re
import sys

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------- EXTRA_LINE --
# 31 of 150 corrections 10 Aug to 27 Sep, 6 in W39. Each entry: (regex, label).
# Written against the sentences he actually cut, quoted in the comments.
EXTRA_LINE = [
    # "If Marmin supports neither, tell me and I raise it with Marmin directly."
    # A choice question ("tell me which one and i will send the invite") was approved
    # every time; the cut ones offer to escalate or chase on his behalf.
    (re.compile(r"(?i)\b(tell me|let me know|say so|ping me)\b[^.?!\n]{0,40}\b(and|then) i('ll| will)? ?(raise|escalate|chase|take it up|follow up|push|flag|go to|talk to)\b"),
     'an offer to escalate: "tell me and I raise it with..."'),
    (re.compile(r"(?i)\bgive me [^.?!\n]{0,30}\band i('ll| will)\b"),
     'an offer: "give me X and I will..."'),
    # "give me till thursday ya"
    (re.compile(r"(?i)\bgive me (till|until|by|a day|a few days|some time)\b"),
     'a promise of extra work with a deadline'),
    # "i can build the same kind of index i did for B2C"
    (re.compile(r"(?i)\bi (can|could) (also )?(build|put together|prepare|draft|set up|make|write|create|share)\b[^.?!\n]{0,60}\b(if|for you|same|too|as well)\b"),
     'an offer of extra work nobody asked for'),
    (re.compile(r"(?i)\bi('ll| will) (file|raise|pull|build|chase|escalate|take)\b[^.?!\n]{0,60}\b(this|next) week\b"),
     'a promise of extra scope'),
    # "happy to walk you through it after tonight's migration call ya" (24 Sep, made a rule)
    (re.compile(r"(?i)\b(happy|glad|free) to (walk|take|run|talk) (you|him|her|them|everyone)\b"),
     'a walkthrough offer: point to the doc instead'),
    (re.compile(r"(?i)\b(walk|take) (you|him|her|them) through\b"),
     'a walkthrough offer: point to the doc instead'),
    # "happy to jump on a call"
    (re.compile(r"(?i)\b(happy to|can|could|glad to)\s+(hop|jump|get)\s+on\s+a\s+(quick\s+)?call\b"),
     'a call offer'),
    (re.compile(r"(?i)\b(happy|glad) to (help|support|assist|discuss|explain|clarify)\b"),
     'an offer: "happy to help"'),
    (re.compile(r"(?i)\b(let me know|lmk) if (you|anything|there)\b[^.?!\n]{0,40}\b(need|questions?|help|unclear)\b"),
     'an open offer: "let me know if you need anything"'),
    (re.compile(r"(?i)\b(still (open|unanswered|pending) since|open since|unanswered since|asked (you )?on \d{1,2} \w{3}|it'?s been \d+ (days|weeks))\b"),
     'pressure that re-cites an old open item'),
    (re.compile(r"(?i)\b(just a (gentle |quick )?reminder|gentle reminder|friendly reminder|circling back|following up on (my|this|the))\b"),
     'a reminder line'),
    (re.compile(r"(?i)\bor let me close it\b|\bassign it or\b"),
     'an ultimatum'),
    (re.compile(r"(?i)\byou told \w+ in \w+\b|\bas (you|i) said (on|in) \d|\bas discussed (on|in|last|yesterday)\b|\bper (my|our) (last|previous)\b"),
     'a history receipt'),
    (re.compile(r"(?i)\b(the )?(same|like the one|as) (we|you|i) did for\b"),
     'a comparison to another workstream (keep to what was asked)'),
]

# ---------------------------------------------------------------- WRONG_FACT --
# Lowercase names that are also ordinary English or Indonesian words.
NAME_STOPWORDS = {
    'mark', 'eve', 'ivy', 'paris', 'peace', 'melody', 'milan', 'eand', 'ben', 'holly',
    'customerexperience', 'dunia', 'asma', 'amal', 'will', 'may', 'grace', 'hope',
    'rose', 'max', 'art', 'bill', 'ray', 'rich', 'sky', 'sunny', 'jean', 'gift', 'akshayk',
}
OWNER_CLAIM = re.compile(
    r"\b([A-Z][a-z]{2,}(?: [A-Z][a-z]+)?) (owns|now owns|is the owner of|prepared|is preparing|"
    r"is handling|will own|is responsible for|is on it|built|wrote|drafted)\b")
PLAIN_AT = re.compile(r'(?<![\w<])@([A-Z][a-z]+(?: [A-Z][a-z]+){0,2})')

# ------------------------------------------------------------------ AI_VOICE --
JARGON = {
    'api', 'endpoint', 'schema', 'payload', 'webhook', 'json', 'backend', 'frontend',
    'config', 'cron', 'hook', 'hooks', 'repo', 'script', 'skrip', 'scripts', 'pipeline',
    'ledger', 'node', 'token', 'tokens', 'mermaid', 'llm', 'prompt', 'deploy', 'commit',
    'regex', 'cli', 'sdk', 'mcp', 'haiku', 'gemini', 'glm', 'ollama', 'groq', 'kimi',
    'subagent', 'harness', 'yaml', 'jsonl', 'fallback', 'quota', 'kuota', 'deterministic',
    'heuristic', 'idempotent', 'enum', 'boolean', 'halalas', 'halala', 'serializer',
}
ABSTRACT = [
    'ekosistem', 'paradigma', 'transformasi', 'sinergi', 'holistik', 'game changer',
    'level up', 'revolusi', 'era baru', 'masa depan', 'leverage', 'seamless', 'empower',
    'unlock', 'journey', 'landscape', 'synergy', 'holistic', 'transformative',
    'next level', 'naik kelas', 'cara kerja baru', 'mindset', 'value proposition',
]
READER_WORDS = re.compile(r"(?i)\b(you|your|lo|lu|elo|kamu|kalian|anda|km)\b")
IDENT = re.compile(r"`[^`]+`|\b\w+\.(py|md|json|sh|js|ts)\b|\b[a-z]+_[a-z_]+\b|(?<![\w/:.])/[a-z][\w-]+")

MASK_RE = re.compile(r'<[^>]*>|https?://\S+|`[^`]*`|\[[^\]]*\]\([^)]*\)')

def _mask(text):
    return MASK_RE.sub(lambda m: ' ' * len(m.group(0)), text)

def _sentences(text):
    """Split into sentences, keeping line structure: a bullet is its own sentence."""
    out = []
    for line in text.split('\n'):
        for s in re.split(r'(?<=[.?!])\s+(?=[A-Za-z@<"\'])', line):
            if s.strip():
                out.append(s.strip())
    return out

def extra_lines(text):
    """[(label, sentence)] for each sentence matching an EXTRA_LINE pattern."""
    hits = []
    for s in _sentences(text):
        for rx, label in EXTRA_LINE:
            if rx.search(s):
                hits.append((label, s))
                break
    return hits

_INDEX = None

def _index():
    global _INDEX
    if _INDEX is None:
        try:
            import slack_mentions
            _INDEX = slack_mentions.build_index()
        except Exception:
            _INDEX = {}
    return _INDEX

def _display(key):
    return ' '.join(w[:1].upper() + w[1:] for w in key.split())

def name_case(text, index=None):
    """[(as written, correct)] for a known person's name written in lowercase.

    the owner, 21 Sep 2026: "Julie, not julie, YourManager, not YourManager." Lowercase is normal in his
    messages; names are the exception.
    """
    index = _index() if index is None else index
    scan = _mask(text)
    found, seen = [], set()
    for key in sorted(index, key=len, reverse=True):
        if key in NAME_STOPWORDS or len(key) < 3 or not key.isascii() or not key.replace(' ', '').isalpha():
            continue
        pat = r'(?<![\w@./#-])' + r'\s+'.join(re.escape(w) for w in key.split()) + r'(?![\w@]|\.\w)'
        for m in re.finditer(pat, scan):          # case-sensitive: lowercase only
            word = text[m.start():m.end()]
            if word != word.lower() or word in seen:
                continue
            seen.add(word)
            found.append((word, _display(key)))
    return found

def fix_name_case(text, index=None):
    for wrong, right in name_case(text, index):
        text = re.sub(r'(?<![\w@./#-])' + re.escape(wrong) + r'(?![\w@]|\.\w)', right, text)
    return text

def unknown_handles(text, index=None):
    """Typed @Names that match nobody in the Slack index."""
    index = _index() if index is None else index
    if not index:
        return []
    out = []
    for m in PLAIN_AT.finditer(text):
        name = m.group(1)
        words = name.split()
        if not any(' '.join(words[:i]).lower() in index for i in range(len(words), 0, -1)):
            out.append('@' + name)
    return out

def owner_claims(text):
    """Sentences that name an owner and carry no link, id or permalink as the source."""
    out = []
    for s in _sentences(text):
        if OWNER_CLAIM.search(s) and not re.search(r'https?://|\b[A-Z]{2,5}-\d{2,5}\b|<@U', s):
            out.append(s)
    return out

def ai_voice(text):
    """Notes when a message to a person reads technical or abstract."""
    masked = _mask(text)
    words = re.findall(r"[A-Za-z][\w'-]*", masked)
    n = len(words)
    if n < 12:
        return []
    jargon = [w for w in words if w.lower() in JARGON]
    idents = IDENT.findall(text)
    low = ' '.join(text.lower().split())
    abstract = [a for a in ABSTRACT if a in low]
    notes = []          # (severity, message)
    tech = len(jargon) + len(idents)
    rate = 100.0 * tech / n
    if tech >= 4 and rate >= 6:
        sample = ', '.join(sorted({j.lower() for j in jargon})[:6])
        sev = 'block' if tech >= 8 and rate >= 10 else 'warn'
        notes.append((sev, f'{tech} technical terms in {n} words ({sample}). Unless the reader '
                      'is an engineer, say what they get or have to do, not how the system works'))
    if len(abstract) >= 2:
        notes.append(('block', 'abstract words with nothing concrete under them (' +
                      ', '.join(abstract[:4]) + '). Name the concrete thing: who, what changes, by when'))
    if n >= 60 and not READER_WORDS.search(masked):
        notes.append(('warn', 'no sentence is about the reader. Lead with what changes for them'))
    return notes

def lint(text, index=None):
    """All text-level findings for one outbound message. [{pattern, severity, msg}]."""
    out = []
    for label, s in extra_lines(text):
        out.append({'pattern': 'EXTRA_LINE', 'severity': 'block',
                    'msg': f'{label}: "{s[:140]}". Delete the sentence'})
    for wrong, right in name_case(text, index):
        out.append({'pattern': 'WRONG_FACT', 'severity': 'block',
                    'msg': f'name in lowercase: "{wrong}" -> "{right}"'})
    for h in unknown_handles(text, index):
        out.append({'pattern': 'WRONG_FACT', 'severity': 'warn',
                    'msg': f'{h} matches nobody in the Slack roster. Check the name and the person'})
    for s in owner_claims(text):
        out.append({'pattern': 'WRONG_FACT', 'severity': 'warn',
                    'msg': f'owner claim with no source: "{s[:120]}". Check it against the source '
                           'message or ledger record, or cut it'})
    for sev, note in ai_voice(text):
        out.append({'pattern': 'AI_VOICE', 'severity': sev, 'msg': note})
    return out

def autofix(text, index=None):
    """(fixed_text, notes). Deterministic fixes for the automated drafters.

    Cuts every EXTRA_LINE sentence and capitalises known names. What cannot be fixed by
    rule (AI voice, owner claims, unknown handles) comes back as notes for the reviewer.
    """
    notes = []
    for label, s in extra_lines(text):
        rest = text.replace(s, '').strip()
        if len(rest.split()) >= 3:          # never cut a message down to nothing
            text = rest
            notes.append(f'cut ({label}): "{s[:100]}"')
    text = re.sub(r'[ \t]{2,}', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    fixed = fix_name_case(text, index)
    if fixed != text:
        notes.append('names capitalised')
    for f in lint(fixed, index):
        if f['pattern'] != 'EXTRA_LINE' and not f['msg'].startswith('name in lowercase'):
            notes.append(f['msg'])
    return fixed, notes

# --------------------------------------------------------- draft extraction --
ORIGINAL_HDR = re.compile(r'^\s*(\*\*[^*\n]{1,60}\*\*\s*[·•-]|.*\b(original|thread so far|earlier in thread|she wrote|he wrote|asked)\b)', re.I)
CODE_LANGS = {'bash', 'sh', 'shell', 'json', 'python', 'py', 'yaml', 'yml', 'sql', 'mermaid',
              'gherkin', 'js', 'ts', 'javascript', 'typescript', 'diff', 'powershell', 'ps1', 'text'}

def draft_segments(text):
    """The outbound message text inside an assistant turn or a draft file.

    A draft is a `>` quote block or an untagged code fence. A quote block that sits
    under an original-message header (`**Name** · time`, "Original", "Thread so far")
    is somebody else's message and is skipped.
    """
    lines = text.split('\n')
    segs, cur, in_fence, fence_ok, skip = [], [], False, False, False
    prev = ''
    for ln in lines:
        s = ln.strip()
        if s.startswith('```'):
            if in_fence:
                if fence_ok and cur:
                    segs.append('\n'.join(cur))
                cur, in_fence = [], False
            else:
                lang = s[3:].strip().lower()
                in_fence, fence_ok, cur = True, lang not in CODE_LANGS or lang == '', []
                fence_ok = fence_ok and lang == ''
            continue
        if in_fence:
            cur.append(ln)
            continue
        if s.startswith('>'):
            if not cur:
                skip = bool(ORIGINAL_HDR.search(prev))
            cur.append(re.sub(r'^\s*>\s?', '', ln))
            continue
        if cur:
            if not skip:
                segs.append('\n'.join(cur))
            cur = []
        if s:
            prev = s
    if cur and not in_fence and not skip:
        segs.append('\n'.join(cur))
    out = []
    for seg in segs:
        body = seg.strip()
        if len(body.split()) < 4 or body.startswith(('[!', 'DRAFTS ONLY')):
            continue
        out.append(body)
    return out

# ------------------------------------------------------------------- replay --
def _week(ts):
    y, w, _ = datetime.date.fromisoformat(ts[:10]).isocalendar()
    return f'{y}-W{w:02d}'

def _read(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding='utf-8') as f:
        return [json.loads(l) for l in f if l.strip()]

def replay(week=None):
    data = os.path.join(BASE_DIR, 'journal', 'draft_feedback')
    turns = _read(os.path.join(data, 'turns.jsonl'))
    labels = {r['key']: r for r in _read(os.path.join(data, 'labels.jsonl'))}
    hits = collections.defaultdict(lambda: [0, 0])     # pattern -> [caught, total]
    fp = [0, 0]                                        # [approved drafts flagged, approved]
    detail = []
    for t in turns:
        if week and _week(t['ts']) != week:
            continue
        segs = draft_segments(t['draft_turn'])
        if len(t['draft_turn']) >= 1500:
            # The stored turn is the last 1500 characters, so a quote can start mid-line
            # with its `>` cut off. The fragment before the first blank line is that quote.
            segs.append(t['draft_turn'].split('\n\n', 1)[0])
        found = [f for s in segs for f in lint(s) if f['severity'] == 'block']
        pats = {f['pattern'] for f in found}
        lab = labels.get(t['key'])
        if t['approval'] or (lab and lab['pattern'] == 'NONE'):
            fp[1] += 1
            if found:
                fp[0] += 1
                detail.append(('FP', t['key'], found[0]['msg'][:110]))
            continue
        if not lab or lab['pattern'] not in ('EXTRA_LINE', 'WRONG_FACT', 'AI_VOICE'):
            continue
        p = lab['pattern']
        hits[p][1] += 1
        if p in pats:
            hits[p][0] += 1
            detail.append(('HIT', t['key'], p + ': ' + next(f['msg'] for f in found if f['pattern'] == p)[:110]))
        else:
            detail.append(('MISS', t['key'], p + ': ' + (lab.get('quote') or '')[:110]))
    return hits, fp, detail

def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding='utf-8')
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('check')
    c.add_argument('--text')
    c.add_argument('--file')
    c.add_argument('--json', action='store_true')
    c.add_argument('--raw', action='store_true', help='treat the whole input as the message, no segment extraction')
    r = sub.add_parser('replay')
    r.add_argument('--week')
    r.add_argument('--verbose', action='store_true')
    a = ap.parse_args()
    if a.cmd == 'check':
        text = a.text if a.text is not None else open(a.file, encoding='utf-8').read()
        segs = [text] if a.raw else (draft_segments(text) or [text])
        found = [f for s in segs for f in lint(s)]
        if a.json:
            print(json.dumps(found, ensure_ascii=False, indent=1))
        else:
            for f in found:
                print(f"{f['severity'].upper():5} {f['pattern']:10} {f['msg']}")
            print('clean' if not found else f'{len(found)} finding(s)')
        return 1 if any(f['severity'] == 'block' for f in found) else 0
    hits, fp, detail = replay(a.week)
    print(f'replay {a.week or "all weeks"}')
    for p, (k, n) in sorted(hits.items()):
        print(f'  {p:12} caught {k}/{n}')
    print(f'  approved drafts flagged (false positives): {fp[0]}/{fp[1]}')
    if a.verbose:
        for kind, key, msg in detail:
            print(f'  {kind:4} {key} {msg}')
    return 0

if __name__ == '__main__':
    sys.exit(main())
