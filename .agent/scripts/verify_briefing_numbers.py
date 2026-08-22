#!/usr/bin/env python3
"""verify_briefing_numbers.py - deterministic accuracy tripwire for briefings.

Cross-checks numbers, ticket keys, and dates in a synthesized briefing
markdown against the harvest JSON sidecar produced by daily_update_runner.py
(HarvestAccumulator schema: {mode, date, generated_at, markdown_path,
sections: {jira, calendar, slack, todo_p0, files_modified, files_created,
backlogs, fathom, ...}, meta: {step_errors, ...}}).

Verdicts (honest heuristics):
  OK         - the claim matches the harvest data
  MISMATCH   - the harvest data exists AND contradicts the claim -> exit 1
  UNVERIFIED - the claim cannot be checked against the harvest (no matching
               source data); listed for human review, does NOT fail the run

Usage:
  python3 .agent/scripts/verify_briefing_numbers.py \
      --briefing daily_update_evening.md \
      --harvest _temp/harvest_evening_2026-07-04.json
"""

import argparse
import json
import re
import sys

# Ticket keys like ABC-123, ABC-123, S-01
TICKET_KEY_RE = re.compile(r'\b([A-Z][A-Z0-9]{0,9}-\d{1,6})\b')
ISO_DATE_RE = re.compile(r'\b(\d{4}-\d{2}-\d{2})\b')
# "14 Jul" / "14 July" style dates
DAYMON_RE = re.compile(
    r'\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\b',
    re.IGNORECASE)
# "3 tickets", "5 tiket", "2 P0", "4 meetings", "12 files", "3 channels"
COUNT_RE = re.compile(
    r'\b(\d{1,4})\s+(open\s+)?'
    r'(tickets?|tiket|P0s?|meetings?|events?|channels?|files?|blockers?|'
    r'action\s+items?|messages?|errors?)\b',
    re.IGNORECASE)

def flatten_strings(obj, out):
    """Collect every string inside a nested JSON structure."""
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, list):
        for x in obj:
            flatten_strings(x, out)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            out.append(str(k))
            flatten_strings(v, out)

def load_harvest(path):
    with open(path, 'r', encoding='utf-8') as f:
        harvest = json.load(f)
    sections = harvest.get('sections', {})
    corpus_lines = []
    flatten_strings(sections, corpus_lines)
    corpus = '\n'.join(corpus_lines)

    slack = sections.get('slack', {}) or {}
    metrics = {
        # keyword -> (metric value or None if unknowable, description)
        'p0': (len(sections.get('todo_p0') or []), 'sections.todo_p0'),
        'channel': (len(slack), 'slack channels harvested'),
        'error': (len((harvest.get('meta') or {}).get('step_errors') or []),
                  'meta.step_errors'),
    }
    # File counts are capped upstream (60/40), so only usable as a check
    # when the briefing number is <= the cap; handled in check_counts.
    metrics['file'] = (
        (len(sections.get('files_modified') or []),
         len(sections.get('files_created') or [])),
        'sections.files_modified / files_created')
    return harvest, corpus, corpus_lines, metrics

COUNT_KEYWORD_MAP = {
    'p0': 'p0',
    'channel': 'channel',
    'error': 'error',
    'file': 'file',
}

def normalize_keyword(word):
    w = word.lower().rstrip('s')
    if w.startswith('p0'):
        return 'p0'
    if w.startswith('channel'):
        return 'channel'
    if w.startswith('error'):
        return 'error'
    if w.startswith('file'):
        return 'file'
    return None  # tickets/meetings/etc: no reliable single harvest metric

def check_ticket_keys(briefing_text, corpus, results):
    keys = sorted(set(TICKET_KEY_RE.findall(briefing_text)))
    for key in keys:
        if key in corpus:
            results.append(('OK', f'ticket key {key} found in harvest'))
        else:
            # Absence is not proof of hallucination (harvest may be partial),
            # so this is UNVERIFIED, not MISMATCH.
            results.append(('UNVERIFIED',
                            f'ticket key {key} not present anywhere in harvest sidecar'))

def check_dates(briefing_lines, corpus, corpus_lines, results):
    for line in briefing_lines:
        dates = set(ISO_DATE_RE.findall(line))
        daymons = {f'{int(d)} {m[:3].title()}' for d, m in DAYMON_RE.findall(line)}
        if not dates and not daymons:
            continue
        keys = set(TICKET_KEY_RE.findall(line))
        for date in sorted(dates | daymons):
            claim = f'date "{date}" in briefing line: {line.strip()[:90]}'
            if date in corpus:
                results.append(('OK', claim + ' | found in harvest'))
                continue
            # If the line names a ticket key, compare against that key's
            # harvest lines: a DIFFERENT date there is a contradiction.
            contradiction = None
            for key in keys:
                for cl in corpus_lines:
                    if key in cl:
                        other = set(ISO_DATE_RE.findall(cl)) | {
                            f'{int(d)} {m[:3].title()}'
                            for d, m in DAYMON_RE.findall(cl)}
                        if other and date not in other:
                            contradiction = (key, sorted(other))
                            break
                if contradiction:
                    break
            if contradiction:
                results.append(('MISMATCH',
                                claim + f' | harvest lines for {contradiction[0]} '
                                f'carry different date(s): {contradiction[1]}'))
            else:
                results.append(('UNVERIFIED', claim + ' | no matching date in harvest'))

def check_counts(briefing_lines, metrics, results):
    for line in briefing_lines:
        for m in COUNT_RE.finditer(line):
            claimed = int(m.group(1))
            keyword = normalize_keyword(m.group(3))
            claim = f'count "{m.group(0)}" in briefing line: {line.strip()[:90]}'
            if keyword is None or keyword not in metrics:
                results.append(('UNVERIFIED',
                                claim + ' | no deterministic harvest metric for this noun'))
                continue
            actual, desc = metrics[keyword]
            if keyword == 'file':
                mod, cre = actual
                # Upstream caps at 60/40: only a contradiction if claim differs
                # from modified, created, AND their union bound, below the caps.
                if claimed in (mod, cre, mod + cre):
                    results.append(('OK', claim + f' | matches {desc} ({mod}/{cre})'))
                elif mod < 60 and cre < 40 and claimed > mod + cre:
                    results.append(('MISMATCH',
                                    claim + f' | harvest has only {mod} modified + {cre} created'))
                else:
                    results.append(('UNVERIFIED',
                                    claim + f' | ambiguous vs {desc} ({mod}/{cre}, capped lists)'))
            else:
                if claimed == actual:
                    results.append(('OK', claim + f' | matches {desc} = {actual}'))
                else:
                    results.append(('MISMATCH',
                                    claim + f' | {desc} = {actual}, briefing says {claimed}'))

def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--briefing', required=True, help='briefing markdown file')
    parser.add_argument('--harvest', required=True,
                        help='harvest sidecar JSON (_temp/harvest_<mode>_<date>.json)')
    parser.add_argument('--quiet-ok', action='store_true',
                        help='suppress OK lines, print only MISMATCH/UNVERIFIED')
    args = parser.parse_args()

    with open(args.briefing, 'r', encoding='utf-8') as f:
        briefing_text = f.read()
    briefing_lines = briefing_text.splitlines()

    harvest, corpus, corpus_lines, metrics = load_harvest(args.harvest)

    results = []
    check_ticket_keys(briefing_text, corpus, results)
    check_dates(briefing_lines, corpus, corpus_lines, results)
    check_counts(briefing_lines, metrics, results)

    counts = {'OK': 0, 'MISMATCH': 0, 'UNVERIFIED': 0}
    for verdict, detail in results:
        counts[verdict] += 1
        if verdict == 'OK' and args.quiet_ok:
            continue
        print(f'[{verdict}] {detail}')

    print(f"\nSummary: {counts['OK']} OK, {counts['MISMATCH']} MISMATCH, "
          f"{counts['UNVERIFIED']} UNVERIFIED "
          f"(briefing={args.briefing}, harvest mode={harvest.get('mode')}, "
          f"date={harvest.get('date')})")
    if counts['MISMATCH']:
        print('RESULT: FAIL - fix every MISMATCH before delivering the briefing.')
        return 1
    print('RESULT: PASS - no contradictions found (UNVERIFIED items need human judgment).')
    return 0

if __name__ == '__main__':
    sys.exit(main())
