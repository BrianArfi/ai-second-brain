#!/usr/bin/env python3
"""Speaker identity resolver: turn "Speaker 3" into a name, with a stated tier.

Why this exists
---------------
Fathom returns real names. The local recorders do not: whisper.cpp returns no
labels at all and Gemini returns "Speaker 1", "Speaker 2". CLAUDE.md forbids
guessing who an unresolved speaker is, so every action item spoken by an
anonymous label is dropped from the MOM. 19 transcripts and 8 MOMs in this repo
carry a bare "Speaker N" today.

The fix is not a better guess. It is a **confidence ladder**, borrowed from
silverstein/minutes: each mapping records HOW it was established, and the
pipeline only trusts the tiers that cannot be wrong.

    tier          established by                                     trusted
    ----          --------------                                     -------
    confirmed     the owner, via `speaker_map.py confirm`                 yes
    enrolled      voice fingerprint match (hook only, see below)      yes
    self-id       the speaker says their own name in their own turn   yes
    addressed     someone says the name, that speaker answers next    no
    sole-remaining one label and one attendee left unassigned         no

"Trusted" is what `apply` writes back into the transcript and what the MOM
prompt is allowed to treat as a name. Untrusted tiers are proposals: they show
up in `pending` for the owner to confirm, and the label stays "Speaker N" until he
does. Nothing here ever invents a name: every candidate must already appear in
the meeting roster or in journal/state/people.json.

The enrolled tier is a documented hole, not a feature. Voice fingerprinting
needs a speaker-embedding model (minutes uses pyannote-rs) which this repo does
not have. `enroll` and the `enrolled` tier exist so the store schema does not
have to change when it lands.

Store: journal/state/speaker_maps.json, keyed by transcript basename.
This is NOT one of the four lock-protected ledgers, so it takes no ledger lock.

Usage:
  speaker_map.py resolve <transcript.md> [--attendee NAME ...] [--json]
  speaker_map.py resolve --all [--json]
  speaker_map.py apply <transcript.md> [--dry-run]
  speaker_map.py confirm <transcript.md> --speaker "Speaker 2" --name "Teammate Chennupati"
  speaker_map.py pending [--json]
"""

import argparse
import json
import os
import re
import sys

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
TRANSCRIPTS_DIR = os.path.join(BASE_DIR, 'Clients', 'Work', 'meetings', 'transcripts')
STORE_PATH = os.path.join(BASE_DIR, 'journal', 'state', 'speaker_maps.json')
PEOPLE_PATH = os.path.join(BASE_DIR, 'journal', 'state', 'people.json')
REGISTRY_PATH = os.path.join(BASE_DIR, 'journal', 'fathom_registry.json')

TRUSTED_TIERS = ('confirmed', 'enrolled', 'self-id')
ALL_TIERS = ('confirmed', 'enrolled', 'self-id', 'addressed', 'sole-remaining')

# "**00:12]** Speaker 2: text"  and  "**[00:12]** Speaker 2: text"  and bare
# "Speaker 2: text". The timestamp prefix is optional because transcribe.py has
# emitted three different shapes over its life.
LINE_RE = re.compile(r'^(?P<prefix>(?:\*\*\[?[\d:]+\]?\*\*\s*)?)'
                     r'(?P<label>Speaker\s+\d+)\s*:\s*(?P<text>.*)$')

SELF_ID_PATTERNS = [
    r"\bi'?m\s+(?P<n>[A-Z][a-z]+)",
    r"\bthis is\s+(?P<n>[A-Z][a-z]+)",
    r"\bmy name is\s+(?P<n>[A-Z][a-z]+)",
    r"\b(?P<n>[A-Z][a-z]+)\s+(?:here|speaking)\b",
]

# ---------------------------------------------------------------- utilities --

def load_json_safe(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default

def atomic_write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(text)
    os.replace(tmp, path)

def load_store():
    return load_json_safe(STORE_PATH, {'version': 1, 'maps': {}})

def save_store(store):
    atomic_write(STORE_PATH, json.dumps(store, indent=1, ensure_ascii=False) + '\n')

def known_names():
    """Every name this repo already believes in. A candidate outside this set is
    discarded rather than trusted: the transcript is the wrong place to learn
    that a person exists."""
    names = set()
    people = load_json_safe(PEOPLE_PATH, {})
    entries = people.get('people') if isinstance(people, dict) else people
    if isinstance(entries, dict):
        entries = list(entries.values())
    for p in (entries or []):
        if isinstance(p, dict) and p.get('name'):
            names.add(p['name'])
            for a in (p.get('aliases') or []):
                names.add(a)
    return names

def _clean_participant(p):
    p = str(p).strip()
    if '@' in p:
        p = p.split('@')[0].replace('.', ' ').replace('_', ' ')
    return re.sub(r'\s+', ' ', p).strip()

MOM_PARTICIPANTS_RE = re.compile(r'^\|\s*(?:Participants|Attendees)\s*\|(?P<v>.+?)\|\s*$',
                                 re.IGNORECASE | re.MULTILINE)

def _roster_from_mom(mom_path):
    """The MOM header carries the calendar invite roster. It is the only place
    the attendee list survives for local recordings, whose registry entries are
    written by the recorder and never see the invite."""
    if not mom_path:
        return []
    full = mom_path if os.path.isabs(mom_path) else os.path.join(BASE_DIR, mom_path)
    if not os.path.exists(full):
        return []
    with open(full, encoding='utf-8') as f:
        m = MOM_PARTICIPANTS_RE.search(f.read())
    if not m:
        return []
    value = re.sub(r'\(.*?\)', '', m.group('v'))          # balanced roles
    out = []
    for chunk in value.split(','):
        # A role in parentheses may itself hold a comma, which splits it across
        # two chunks and leaves one bare "(" behind: cut from there. The row also
        # often ends with a prose sentence ("Roster from the Work calendar
        # invite."), so stop at the first full stop.
        name = chunk.split('(')[0].split(')')[-1].split('.')[0]
        name = re.sub(r'\s+', ' ', name).strip(' .;')
        if name and re.match(r"^[A-Z][\w'-]*(\s+[A-Z][\w'-]*)*$", name) and len(name) > 2:
            out.append(name)
    return list(dict.fromkeys(out))

def roster_for(transcript_path):
    """Attendees of this meeting. Three sources, in falling order of directness:
    the registry entry for this audio, the Fathom recording it is cross-linked
    to, and the MOM header. Local entries carry an empty participants list, so
    in practice the MOM is what usually answers."""
    stem = os.path.splitext(os.path.basename(transcript_path))[0]
    registry = load_json_safe(REGISTRY_PATH, {})
    hit = None
    for rec in registry.values():
        lp = rec.get('local_path') or ''
        if lp and os.path.splitext(os.path.basename(lp))[0] == stem:
            hit = rec
            break
    if hit is None:
        return []
    people = [_clean_participant(p) for p in (hit.get('participants') or [])]
    if not people:
        for rid in (hit.get('related_recordings') or []):
            twin = registry.get(str(rid)) or {}
            people = [_clean_participant(p) for p in (twin.get('participants') or [])]
            if people:
                break
    if not people:
        people = _roster_from_mom(hit.get('mom_path'))
    return [p for p in people if p]

def parse_turns(text):
    """[(label, utterance)] in order, for every Speaker N line."""
    turns = []
    for line in text.splitlines():
        m = LINE_RE.match(line.strip())
        if m:
            turns.append((re.sub(r'\s+', ' ', m.group('label')), m.group('text').strip()))
    return turns

def _first_names(names):
    return {n.split()[0].lower(): n for n in names if n.split()}

# ---------------------------------------------------------------- resolution --

def resolve(transcript_path, extra_attendees=None):
    """Return {label: {name, tier, evidence}} plus the roster used.

    Rules run cheapest-first and never overwrite a stronger tier. A candidate
    name must be in the roster or the people roster; anything else is dropped.
    """
    with open(transcript_path, encoding='utf-8') as f:
        text = f.read()
    turns = parse_turns(text)
    labels = []
    for lab, _ in turns:
        if lab not in labels:
            labels.append(lab)

    roster = list(dict.fromkeys((roster_for(transcript_path) or []) + list(extra_attendees or [])))
    pool = set(roster) | known_names()
    first_of = _first_names(pool)

    out = {}

    def claim(label, name, tier, evidence):
        cur = out.get(label)
        if cur and ALL_TIERS.index(cur['tier']) <= ALL_TIERS.index(tier):
            return
        out[label] = {'name': name, 'tier': tier, 'evidence': evidence}

    # tier: self-id -- the speaker names themselves inside their own turn.
    for label, utt in turns:
        for pat in SELF_ID_PATTERNS:
            m = re.search(pat, utt, re.IGNORECASE)
            if not m:
                continue
            cand = first_of.get(m.group('n').lower())
            if cand:
                claim(label, cand, 'self-id', f'said "{m.group(0).strip()}"')

    # tier: addressed -- A ends a turn on a vocative, B answers next.
    for i in range(len(turns) - 1):
        speaker_a, utt = turns[i]
        speaker_b = turns[i + 1][0]
        if speaker_a == speaker_b:
            continue
        m = re.search(r'\b(?:thanks|thank you|over to you|go ahead|hi|hey)[,\s]+'
                      r'(?P<n>[A-Z][a-z]+)\b[.?!]?\s*$', utt.strip(), re.IGNORECASE)
        if not m:
            m = re.search(r'^(?P<n>[A-Z][a-z]+)[,]\s', utt.strip())
        if not m:
            continue
        cand = first_of.get(m.group('n').lower())
        if cand:
            claim(speaker_b, cand, 'addressed',
                  f'{speaker_a} said "{m.group(0).strip()}" immediately before')

    # tier: sole-remaining -- exactly one label and one attendee left over, and
    # the room adds up. Without the len() equality this fires on any one-name
    # roster, which is the common case for a local recording whose registry
    # entry only ever saw the owner, and it would then name every other voice in a
    # ten-person call after the one person it happens to know.
    assigned = {v['name'] for v in out.values()}
    open_labels = [l for l in labels if l not in out]
    open_people = [p for p in roster if p not in assigned]
    if len(labels) == len(roster) >= 2 and len(open_labels) == 1 and len(open_people) == 1:
        claim(open_labels[0], open_people[0], 'sole-remaining',
              'last unassigned label, last unassigned attendee on the roster')

    return {'labels': labels, 'roster': roster, 'speakers': out}

def store_key(transcript_path):
    return os.path.basename(transcript_path)

def merge_into_store(transcript_path, result):
    """Persist, preserving any tier the resolver cannot produce (confirmed,
    enrolled). A re-run must never demote a mapping the owner confirmed by hand."""
    store = load_store()
    key = store_key(transcript_path)
    prev = (store['maps'].get(key) or {}).get('speakers') or {}
    merged = dict(result['speakers'])
    for label, rec in prev.items():
        if rec.get('tier') in ('confirmed', 'enrolled'):
            merged[label] = rec
    store['maps'][key] = {'labels': result['labels'], 'roster': result['roster'],
                          'speakers': merged}
    save_store(store)
    return merged

# -------------------------------------------------------------------- output --

def unresolved(entry):
    sp = entry.get('speakers') or {}
    return [l for l in (entry.get('labels') or [])
            if (sp.get(l) or {}).get('tier') not in TRUSTED_TIERS]

def trusted_map(transcript_path):
    """{label: name} for tiers the pipeline is allowed to believe. This is the
    one function the watcher should call."""
    entry = load_store()['maps'].get(store_key(transcript_path)) or {}
    return {l: r['name'] for l, r in (entry.get('speakers') or {}).items()
            if r.get('tier') in TRUSTED_TIERS}

def _print_result(path, entry):
    print(f'{os.path.basename(path)}')
    if not entry.get('labels'):
        print('  no "Speaker N" labels in this transcript, nothing to resolve')
        return
    print(f'  roster: {", ".join(entry["roster"]) or "(none on the registry entry)"}')
    for label in entry['labels']:
        rec = (entry.get('speakers') or {}).get(label)
        if not rec:
            print(f'  {label:<12} UNRESOLVED')
            continue
        mark = 'trusted ' if rec['tier'] in TRUSTED_TIERS else 'proposal'
        print(f'  {label:<12} {rec["name"]:<24} [{rec["tier"]}, {mark}] {rec["evidence"]}')

# ------------------------------------------------------------------ commands --

def _transcript_paths(arg_all, path):
    if arg_all:
        if not os.path.isdir(TRANSCRIPTS_DIR):
            return []
        return sorted(os.path.join(TRANSCRIPTS_DIR, f)
                      for f in os.listdir(TRANSCRIPTS_DIR) if f.endswith('.md'))
    return [path]

def cmd_resolve(args):
    paths = _transcript_paths(args.all, args.transcript)
    payload = {}
    for p in paths:
        if not os.path.exists(p):
            print(f'no such transcript: {p}', file=sys.stderr)
            return 1
        result = resolve(p, args.attendee)
        if not result['labels']:
            continue
        merge_into_store(p, result)
        entry = load_store()['maps'][store_key(p)]
        payload[os.path.basename(p)] = entry
        if not args.json:
            _print_result(p, entry)
    if args.json:
        print(json.dumps(payload, indent=1, ensure_ascii=False))
    return 0

def cmd_apply(args):
    path = args.transcript
    mapping = trusted_map(path)
    if not mapping:
        print('no trusted mapping for this transcript, run resolve or confirm first')
        return 1
    with open(path, encoding='utf-8') as f:
        text = f.read()
    out_lines, changed = [], 0
    for line in text.splitlines():
        m = LINE_RE.match(line.strip())
        if m and re.sub(r'\s+', ' ', m.group('label')) in mapping:
            label = re.sub(r'\s+', ' ', m.group('label'))
            # Keep the original label in parentheses: the mapping is a claim, and
            # a reader must always be able to audit it against the raw output.
            line = f'{m.group("prefix")}{mapping[label]} ({label}): {m.group("text")}'
            changed += 1
        out_lines.append(line)
    new = '\n'.join(out_lines) + ('\n' if text.endswith('\n') else '')
    if args.dry_run:
        print(f'would rewrite {changed} line(s): ' +
              ', '.join(f'{k} -> {v}' for k, v in mapping.items()))
        return 0
    atomic_write(path, new)
    print(f'rewrote {changed} line(s) in {os.path.basename(path)}: ' +
          ', '.join(f'{k} -> {v}' for k, v in mapping.items()))
    return 0

def cmd_confirm(args):
    path = args.transcript
    if not os.path.exists(path):
        print(f'no such transcript: {path}', file=sys.stderr)
        return 1
    store = load_store()
    key = store_key(path)
    entry = store['maps'].get(key)
    if entry is None:
        result = resolve(path, None)
        merge_into_store(path, result)
        store = load_store()
        entry = store['maps'][key]
    label = re.sub(r'\s+', ' ', args.speaker)
    if label not in (entry.get('labels') or []):
        print(f'{label} does not appear in this transcript. '
              f'Labels present: {", ".join(entry.get("labels") or []) or "none"}',
              file=sys.stderr)
        return 1
    entry.setdefault('speakers', {})[label] = {
        'name': args.name, 'tier': 'confirmed', 'evidence': 'confirmed by the owner'}
    store['maps'][key] = entry
    save_store(store)
    print(f'{label} -> {args.name} [confirmed]. Write it into the transcript with: '
          f'speaker_map.py apply "{path}"')
    return 0

def cmd_pending(args):
    """Every label still unresolved or only proposed. This is the queue that
    belongs in the morning update, and the reason an untrusted tier is not
    silently applied."""
    store = load_store()
    rows = []
    for key, entry in sorted(store['maps'].items()):
        open_labels = unresolved(entry)
        if not open_labels:
            continue
        rows.append((key, open_labels, entry))
    if args.json:
        print(json.dumps([{'transcript': k, 'unresolved': l,
                           'proposals': {x: (e['speakers'] or {}).get(x)
                                         for x in l if (e['speakers'] or {}).get(x)}}
                          for k, l, e in rows], indent=1, ensure_ascii=False))
        return 0
    if not rows:
        print('no unresolved speakers')
        return 0
    total = sum(len(l) for _, l, _ in rows)
    print(f'{total} unresolved speaker(s) across {len(rows)} transcript(s)\n')
    for key, open_labels, entry in rows:
        print(key)
        for label in open_labels:
            rec = (entry.get('speakers') or {}).get(label)
            if rec:
                print(f'  {label:<12} proposal: {rec["name"]} [{rec["tier"]}] {rec["evidence"]}')
            else:
                print(f'  {label:<12} no candidate')
        print()
    return 0

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd')

    r = sub.add_parser('resolve', help='work out who each Speaker N is')
    r.add_argument('transcript', nargs='?')
    r.add_argument('--all', action='store_true')
    r.add_argument('--attendee', action='append', help='extra roster name, repeatable')
    r.add_argument('--json', action='store_true')

    a = sub.add_parser('apply', help='write trusted names into the transcript')
    a.add_argument('transcript')
    a.add_argument('--dry-run', action='store_true')

    c = sub.add_parser('confirm', help='the owner names a speaker by hand')
    c.add_argument('transcript')
    c.add_argument('--speaker', required=True)
    c.add_argument('--name', required=True)

    p = sub.add_parser('pending', help='labels still waiting on a name')
    p.add_argument('--json', action='store_true')

    args = ap.parse_args()
    handler = {'resolve': cmd_resolve, 'apply': cmd_apply,
               'confirm': cmd_confirm, 'pending': cmd_pending}.get(args.cmd)
    if not handler:
        ap.print_help()
        return 1
    if args.cmd == 'resolve' and not args.all and not args.transcript:
        ap.error('resolve needs a transcript path or --all')
    return handler(args)

if __name__ == '__main__':
    sys.exit(main())
