#!/usr/bin/env python3
"""Morning carry-over check: what lands today or tomorrow that the usual reports hide.

Added 5 Oct 2026. That morning the marketplace mapping from Palak (WAIT-0930) was
due the same day and fed Julie's review the next day, and the briefing missed it:
  - the waiting-on report only lists BREACHED records, and this one was not yet;
  - the calendar pull stops around midday tomorrow, so tomorrow's meetings were unseen;
  - the previous briefing said "due Senin 5 Okt" in plain text and nothing re-read it.

This prints three blocks, each a must-read for the morning Top 5:
  1. Open commitments and waiting-on records due today or tomorrow (breached or not)
  2. Tomorrow's Work calendar
  3. Lines in the most recent earlier Dashboard briefing that name today or tomorrow

    python3 .agent/scripts/carryover_check.py [--date YYYY-MM-DD]
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WIB = timezone(timedelta(hours=7))
ID_MON = ['Jan', 'Feb', 'Mar', 'Apr', 'Mei', 'Jun', 'Jul', 'Ags', 'Sep', 'Okt', 'Nov', 'Des']
EN_MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
ID_DAY = ['Senin', 'Selasa', 'Rabu', 'Kamis', 'Jumat', 'Sabtu', 'Minggu']

def _items(path):
    try:
        d = json.load(open(os.path.join(BASE, path), encoding='utf-8'))
    except Exception as e:
        print(f"  (could not read {path}: {e})")
        return []
    it = d.get('items', d) if isinstance(d, dict) else d
    it = it.values() if isinstance(it, dict) else it
    return [i for i in it if isinstance(i, dict)]

def date_patterns(day):
    """Regexes that match how briefings write a date: 5 Okt, Oct 5, 2026-10-05, Senin 5."""
    d = day.day
    pats = [day.strftime('%Y-%m-%d'),
            rf"\b{d}\s+({ID_MON[day.month-1]}|{EN_MON[day.month-1]})\w*\b",
            rf"\b({EN_MON[day.month-1]}|{ID_MON[day.month-1]})\w*\s+{d}\b",
            rf"\b{ID_DAY[day.weekday()]}\s+{d}\b"]
    return re.compile('|'.join(pats), re.I)

def ledger_block(today, tomorrow):
    print("## 1. Due today or tomorrow (breached or not)")
    n = 0
    for c in _items('journal/state/commitments.json'):
        if c.get('status') != 'open' or not c.get('due'):
            continue
        if c['due'] in (str(today), str(tomorrow)):
            tag = 'TODAY' if c['due'] == str(today) else 'tomorrow'
            print(f"- [{tag}] {c['id']} (the owner owes {c.get('to') or '?'}): {(c.get('text') or '')[:140]}  [node:{c.get('node')}]")
            n += 1
    end_tomorrow = datetime.combine(tomorrow, datetime.max.time(), WIB).timestamp()
    start_today = datetime.combine(today, datetime.min.time(), WIB).timestamp()
    for w in _items('journal/state/waiting_on.json'):
        if w.get('status') != 'open' or not w.get('since') or not w.get('sla_hours'):
            continue
        due = w['since'] + float(w['sla_hours']) * 3600
        if start_today <= due <= end_tomorrow:
            dd = datetime.fromtimestamp(due, WIB)
            tag = 'TODAY' if dd.date() == today else 'tomorrow'
            print(f"- [{tag} {dd:%H:%M} WIB] {w['id']} (waiting on {w.get('owner')}): {(w.get('what') or '')[:140]}  [node:{w.get('node')}]")
            n += 1
    if not n:
        print("- nothing")

def calendar_block(tomorrow):
    print(f"\n## 2. Tomorrow's meetings ({tomorrow:%a %d %b}): prep starts today")
    script = os.path.join(BASE, '.agent', 'skills', 'google-calendar-connector', 'gcal_manager.py')
    try:
        out = subprocess.run([sys.executable, script, 'list', '--profile', 'work',
                              '--days-back', '-1', '--days-forward', '2'],
                             capture_output=True, text=True, timeout=90).stdout
    except Exception as e:
        print(f"- calendar unavailable: {e}")
        return
    hits = [l for l in out.splitlines() if l.startswith(f"{tomorrow}T")]
    skip = re.compile(r'prayer|focus|home|lunch', re.I)
    hits = [l for l in hits if not skip.search(l)]
    if not out.strip() or 'Error' in out[:200]:
        print("- calendar unavailable: " + out.strip().splitlines()[0][:200] if out.strip() else "- calendar unavailable")
        return
    for l in hits:
        print(f"- {l[11:16]} {l.split(' - ', 1)[-1]}")
    if not hits and out.strip():
        print("- no meetings")

def briefing_block(today, tomorrow):
    print("\n## 3. Last briefing lines that name today or tomorrow")
    try:
        text = open(os.path.join(BASE, 'Dashboard.md'), encoding='utf-8').read()
    except Exception as e:
        print(f"- Dashboard unreadable: {e}")
        return
    sections = re.split(r'(?m)^(?=## [^\n]*\([^)\n]*(?:Pagi|Malam)\))', text)
    today_hdr = date_patterns(today)
    prev = next((s for s in sections[1:] if s.startswith('## ') and not today_hdr.search(s.splitlines()[0])), None)
    if not prev:
        print("- no earlier briefing found")
        return
    print(f"_from: {prev.splitlines()[0][3:120]}_")
    pat_t, pat_m = date_patterns(today), date_patterns(tomorrow)
    n = 0
    for line in prev.splitlines()[1:]:
        if pat_t.search(line) or pat_m.search(line):
            print(f"- {line.strip()[:400]}")
            n += 1
    if not n:
        print("- none")

def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', help='YYYY-MM-DD, default today WIB')
    a = ap.parse_args()
    today = datetime.strptime(a.date, '%Y-%m-%d').date() if a.date else datetime.now(WIB).date()
    tomorrow = today + timedelta(days=1)
    print(f"# Carry-over check for {today:%a %d %b %Y} (WIB)\n")
    ledger_block(today, tomorrow)
    calendar_block(tomorrow)
    briefing_block(today, tomorrow)

if __name__ == '__main__':
    main()
