#!/usr/bin/env python3
"""
render_followup_tracker.py - Regenerates journal/master_followup_tracker.md as a
GENERATED VIEW over the PM ledgers.

Design (2026-07-24): the tracker used to be hand-maintained and drifted stale
(last hand-update 23 Jun while commitments.json/waiting_on.json/decisions.json
moved on daily). The three ledgers are the SSOT; this script embeds each
ledger's own `report` output verbatim (never re-derives), same rule the
morning/evening update SOPs already apply everywhere else. Never hand-edit the
output file - edit the ledgers instead:
  commitment_ledger.py add/close   -> journal/state/commitments.json
  waiting_watchdog.py add/close    -> journal/state/waiting_on.json
  decision_log.py add/decide       -> journal/state/decisions.json

Run: python3 .agent/skills/project-tracking-update/scripts/render_followup_tracker.py
Called from daily_update_runner.py (both morning and evening modes) and from
evening-update.md step 5a. Safe to run any time; idempotent apart from the
"Last generated" timestamp.
"""

import json
import os
import subprocess
import sys
from datetime import timezone, timedelta, datetime

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))

# The three ledger CLIs below quote Slack text verbatim, codes and all, so the tracker carried
# `<@U8VMF9CPQ>` where a name belongs. `mentions_only`, not `render`: this file is markdown with
# placeholders of its own (`<YYMMDD>`, `<SELLER_PREFIX>`, `<NNNN>` are all in it today) and the
# full decoder would strip the brackets off each of them.
sys.path.insert(0, os.path.join(BASE_DIR, '.agent', 'scripts'))
from slack_text import mentions_only  # noqa: E402
OUT_PATH = os.path.join(BASE_DIR, 'journal', 'master_followup_tracker.md')

# Every COM-/WAIT-/DEC- id in the rendered view becomes a click through to that
# record's card on the local dashboard. The ledger `report` output stays plain
# text (it is read in a terminal); the linking happens here, on the way into the
# markdown file. Import is best-effort so a missing helper degrades to the old
# plain-text tracker instead of failing the render.
sys.path.insert(0, os.path.join(BASE_DIR, '.agent', 'scripts'))
try:
    from ledger_link import linkify
except ImportError:
    def linkify(text, code=True):
        return text

COMMITMENTS_PATH = os.path.join(BASE_DIR, 'journal', 'state', 'commitments.json')
WAITING_PATH = os.path.join(BASE_DIR, 'journal', 'state', 'waiting_on.json')

WIB = timezone(timedelta(hours=7))

SCRIPTS = {
    'commitments': ['python3', os.path.join(BASE_DIR, '.agent/skills/commitment-ledger/scripts/commitment_ledger.py'), 'report'],
    'waiting_on': ['python3', os.path.join(BASE_DIR, '.agent/skills/waiting-watchdog/scripts/waiting_watchdog.py'), 'report'],
    'decisions': ['python3', os.path.join(BASE_DIR, '.agent/skills/decision-log/scripts/decision_log.py'), 'report'],
}

def atomic_write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(text)
    os.replace(tmp, path)

def load_json_safe(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default

def run_report(key):
    cmd = SCRIPTS[key]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=BASE_DIR)
        if r.returncode != 0:
            return None, f'! {key} report exited {r.returncode}: {r.stderr.strip()[:300]}'
        out = r.stdout.strip()
        return (out if out else '_(nothing to report)_'), None
    except Exception as e:
        return None, f'! {key} report failed to run: {e}'

def health_check():
    commitments = load_json_safe(COMMITMENTS_PATH, {'items': {}})
    waiting = load_json_safe(WAITING_PATH, {'items': {}})

    c_items = commitments.get('items', {}).values()
    w_items = waiting.get('items', {}).values()

    today = datetime.now(WIB).strftime('%Y-%m-%d')
    c_open = sum(1 for it in c_items if it.get('status') == 'open')
    c_overdue = sum(1 for it in c_items if it.get('status') == 'open' and it.get('due') and it.get('due') < today)
    w_active = sum(1 for it in w_items if it.get('status') in ('open', 'breached'))
    w_breached = sum(1 for it in w_items if it.get('status') == 'breached')

    lines = [
        '| Metric | Value |',
        '|:---|:---|',
        f'| Open commitments (the owner owes) | {c_open} |',
        f'| Overdue commitments | {c_overdue} |',
        f'| Active waiting-on (others owe the owner) | {w_active} |',
        f'| Flagged for escalation | {w_breached} |',
    ]
    return '\n'.join(lines)

def render():
    now = datetime.now(WIB).strftime('%Y-%m-%d %H:%M WIB')
    sections = {}
    errors = []
    for key in ('commitments', 'waiting_on', 'decisions'):
        out, err = run_report(key)
        if err:
            errors.append(err)
            sections[key] = f'_(could not render: {err})_'
        else:
            sections[key] = linkify(out)

    parts = [
        '# 🎯 Master Follow-up & To-Do Tracker',
        '',
        f'> [!IMPORTANT]',
        f'> **GENERATED FILE - do not edit by hand.** Sources: `journal/state/commitments.json`, '
        f'`journal/state/waiting_on.json`, `journal/state/decisions.json`. To change what appears here, '
        f'edit the ledgers (`commitment_ledger.py`, `waiting_watchdog.py`, `decision_log.py`), then regenerate: '
        f'`python3 .agent/skills/project-tracking-update/scripts/render_followup_tracker.py`',
        '>',
        f'> **Every id below is clickable.** `COM-`/`WAIT-`/`DEC-` links open that '
        f'record on the local dashboard ({os.environ.get("PSB_DASHBOARD_BASE", "http://localhost:3737")}) '
        f'with its owner, SLA, source, timeline and notes. If a link does nothing, the dashboard is down: '
        f'`bash .agent/scripts/ensure_dashboard.sh`',
        '',
        f'**Last generated**: {now}',
        '',
        '---',
        '',
        sections['commitments'],
        '',
        '---',
        '',
        sections['waiting_on'],
        '',
        '---',
        '',
        sections['decisions'],
        '',
        '---',
        '',
        '## 📊 Health Check',
        '',
        health_check(),
        '',
    ]

    atomic_write(OUT_PATH, mentions_only('\n'.join(parts)))
    print(f'rendered {os.path.relpath(OUT_PATH, BASE_DIR)}')
    for e in errors:
        print(e, file=sys.stderr)
    return 0 if not errors else 1

if __name__ == '__main__':
    sys.exit(render())
