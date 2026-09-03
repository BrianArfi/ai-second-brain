#!/usr/bin/env python3
"""
harness_health.py - Monthly self-check of the harness: is every cron job
actually running, is every ledger/state file fresh, are there skills nobody
has touched in 60 days, and (--full) do the connector tokens still work.

Design (per plan komponen 8, 2026-07-10): pure-Python L1 collector clone of
mention_ledger.py's conventions - stdlib only, atomic state write, BASE_DIR
from __file__. This is a HEALTH CHECK, not a ledger: there is no
open/answered lifecycle, just point-in-time findings persisted so `report`
can diff "new since last run" and the monthly file keeps a durable record.

Checks:
  1. Cron jobs of THIS repo (scoped by BASE_DIR string in the crontab line)
     vs heartbeat rows / log mtimes -> silent (>3x expected cadence),
     recent fail rows, needs_reauth flags. Non-repo (You etc) crontab
     blocks are reported as "other, not touched" - never evaluated further.
  2. State-file staleness vs the expectation table (component 2/3/4/5 files
     may not exist yet if those builders haven't landed - skip gracefully).
  3. Unused skills: any .agent/skills/<name> dir never mentioned in
     journal/activity_log.jsonl in the last 60 days.
  4. --full only: cheapest read-only auth probe per token.env found under
     .agent/skills/*/token.env (slack auth.test, fathom list --limit 1,
     jira daily-digest dry check, gcal list --days-forward 0, metabase -
     skipped, no token.env / no script found). Missing token.env -> skip
     gracefully, never fail the run.

Subcommands:
  run [--full]   do all checks, save state, append monthly report row
  report [--all] print latest findings as briefing-ready markdown

State: journal/state/harness_health.json
Reports: journal/harness_health/<YYYY-MM>.md (one run = one section, appended)
"""

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7))
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
STATE_PATH = os.path.join(BASE_DIR, 'journal', 'state', 'harness_health.json')
REPORT_DIR = os.path.join(BASE_DIR, 'journal', 'harness_health')
ACTIVITY_LOG = os.path.join(BASE_DIR, 'journal', 'activity_log.jsonl')
HEARTBEAT_LOG = os.path.join(BASE_DIR, 'dashboard-data', 'agent_heartbeat.jsonl')
SKILLS_DIR = os.path.join(BASE_DIR, '.agent', 'skills')
HEARTBEAT_PY = os.path.join(BASE_DIR, '.agent', 'scripts', 'heartbeat.py')
ROUTINES_PATH = os.path.join(BASE_DIR, 'journal', 'state', 'routines.json')

API_PAUSE = 0.15
SILENT_MULTIPLIER = 3          # silent = no activity for > 3x expected cadence
UNUSED_SKILL_DAYS = 60
FAIL_LOOKBACK_DAYS = 30        # how far back to scan heartbeat for fail rows

# --------------------------------------------------------- cron registry ---
# Known repo cron jobs (hand-maintained; extend as new components land cron).
# cadence_minutes derived from the installed crontab schedule, kept here as
# the expectation baseline in case a line is edited/removed and we still want
# to flag "expected job missing from crontab".
CRON_REGISTRY = [
    {
        'job': 'maintenance',
        'match': 'scripts/maintenance.sh',
        'cadence_minutes': 24 * 60,
        'heartbeat_job': 'maintenance',              # heartbeat added 2026-07-11
        'log_file': os.path.join(BASE_DIR, 'scripts', 'maintenance.log'),
    },
    {
        'job': 'dashboard-keepalive',
        'match': '.agent/scripts/dashboard_keepalive.sh',
        'cadence_minutes': 60,
        'heartbeat_job': 'dashboard-keepalive',
        'log_file': os.path.join(BASE_DIR, '.agent', 'scripts', 'dashboard_keepalive.log'),
    },
    {
        # Meeting-recorder cron. Job id stays 'vexa-auto' (heartbeat rows + the
        # dashboard job map key off it), but since the Jul-2026 cutover the
        # client it runs drives *meetbot* (Rust, :8060), not vexa-lite; the
        # backend is set by MEETBOT=/VEXA_API_BASE= on the cron line itself.
        # Script path and log path are unchanged by the cutover, so this match
        # still resolves -- verified against the live crontab 19 Jul 2026.
        'job': 'vexa-auto',
        'match': 'meeting-recorder/vexa_bots.py',
        'cadence_minutes': 5,
        # Was 60: quiet cycles used to print nothing, so the log only moved on
        # the hourly idle heartbeat. cmd_auto now writes one trace line EVERY
        # cycle, so the signal cadence is the real cron cadence again and a
        # silent cron is caught in ~15min instead of ~3h.
        'heartbeat_job': 'vexa-auto',
        'log_file': '/tmp/vexa_auto.log',
    },
    {
        'job': 'mention-ledger',
        'match': 'slack-tracker/scripts/mention_ledger.py',
        'cadence_minutes': 30,
        'heartbeat_job': None,                       # no heartbeat integration yet
        'log_file': os.path.join(BASE_DIR, '.agent', 'skills', 'slack-tracker', 'ledger_cron.log'),
        'state_file': os.path.join(BASE_DIR, 'journal', 'state', 'slack_mention_ledger.json'),
        'state_ts_field': 'last_sweep',
    },
    # --- new harness components (cron installed 2026-07-10, Stage B) ---
    {
        'job': 'commitment-ledger',                  # 40 12,16,20 * * * (3x daily, on-window)
        'match': 'commitment-ledger/scripts/commitment_ledger.py',
        'cadence_minutes': 8 * 60,
        'heartbeat_job': 'commitment-ledger',
        'log_file': os.path.join(BASE_DIR, '.agent', 'skills', 'commitment-ledger', 'commitment_ledger_cron.log'),
        'state_file': os.path.join(BASE_DIR, 'journal', 'state', 'commitments.json'),
        'state_ts_field': 'last_sweep',
    },
    {
        'job': 'waiting-watchdog',                   # 7 * * * * (hourly, local-only)
        'match': 'waiting-watchdog/scripts/waiting_watchdog.py',
        'cadence_minutes': 60,
        'heartbeat_job': 'waiting-watchdog',
        'log_file': os.path.join(BASE_DIR, '.agent', 'skills', 'waiting-watchdog', 'waiting_watchdog_cron.log'),
        'state_file': os.path.join(BASE_DIR, 'journal', 'state', 'waiting_on.json'),
        'state_ts_field': 'last_sweep',
    },
    {
        'job': 'outcomes-loop',                      # 5 13 * * 1 (weekly Monday, on-window)
        'match': 'outcomes-loop/scripts/outcomes_loop.py',
        'cadence_minutes': 7 * 24 * 60,
        'heartbeat_job': 'outcomes-loop',
        'log_file': os.path.join(BASE_DIR, '.agent', 'skills', 'outcomes-loop', 'outcomes_loop_cron.log'),
        # outcomes.json last_check is ISO, not epoch — rely on heartbeat/log signal
    },
    {
        'job': 'premeeting-cards',                   # 32 12 * * 1-5 (weekdays, on-window)
        'match': 'premeeting-cards/scripts/premeeting_cards.py',
        'cadence_minutes': 24 * 60,
        'heartbeat_job': 'premeeting-cards',
        'log_file': os.path.join(BASE_DIR, '.agent', 'skills', 'premeeting-cards', 'premeeting_cron.log'),
        'state_file': os.path.join(BASE_DIR, 'journal', 'state', 'premeeting.json'),
        'state_ts_field': 'last_run',
    },
    {
        'job': 'harness-health',                     # 10 13 1 * * (monthly, --full, on-window)
        'match': 'harness-health/scripts/harness_health.py',
        'cadence_minutes': 31 * 24 * 60,
        'heartbeat_job': 'harness-health',
        'log_file': os.path.join(BASE_DIR, '.agent', 'skills', 'harness-health', 'harness_health_cron.log'),
        'state_file': os.path.join(BASE_DIR, 'journal', 'state', 'harness_health.json'),
        'state_ts_field': 'last_run',
    },
    {
        'job': 'token-tracker',                      # 50 12,18 * * * (2x daily, on-window)
        'match': 'token-tracker/scripts/token_usage.py',
        'cadence_minutes': 6 * 60,
        'heartbeat_job': 'token-tracker',
        'log_file': os.path.join(BASE_DIR, '.agent', 'skills', 'token-tracker', 'token_tracker_cron.log'),
        'state_file': os.path.join(BASE_DIR, 'journal', 'state', 'token_usage.json'),
    },
]

# All five designed cron jobs were installed on 2026-07-10 (Stage B
# integration) and moved into CRON_REGISTRY above — this reference list is
# kept for provenance only and is not evaluated.
DESIGNED_CRON = []

# ------------------------------------------------------- staleness table ---
# path relative to BASE_DIR; max_age_hours; ts_field to read inside the JSON
# (falls back to file mtime if absent/None); exempt = never flagged stale.
STALENESS_TABLE = [
    {'name': 'mention_ledger', 'path': 'journal/state/slack_mention_ledger.json',
     'max_age_hours': 2, 'ts_field': 'last_sweep', 'ts_kind': 'epoch'},
    {'name': 'commitments', 'path': 'journal/state/commitments.json',
     'max_age_hours': 12, 'ts_field': 'last_sweep', 'ts_kind': 'epoch'},
    {'name': 'waiting_on', 'path': 'journal/state/waiting_on.json',
     'max_age_hours': 3, 'ts_field': 'last_sweep', 'ts_kind': 'epoch'},
    {'name': 'outcomes', 'path': 'journal/state/outcomes.json',
     'max_age_hours': 8 * 24, 'ts_field': 'last_checked', 'ts_kind': 'epoch'},
    {'name': 'tickets', 'path': 'journal/state/tickets.json',
     'max_age_hours': 48, 'ts_field': 'updated_wib', 'ts_kind': 'iso'},
    {'name': 'decisions', 'path': 'journal/state/decisions.json',
     'max_age_hours': None, 'ts_field': None, 'ts_kind': None},   # exempt
    # The Hours tab froze on 9 Aug 2026 and nobody noticed until 3 Sep, because
    # this file was the only dashboard state with no age watch: the four ledgers
    # above are watched, work_hours.json was not. 6h covers a normal working day
    # with the tab open (it self-refreshes hourly) without firing overnight.
    {'name': 'work_hours', 'path': 'journal/state/work_hours.json',
     'max_age_hours': 6, 'ts_field': 'last_sweep_wib', 'ts_kind': 'iso'},
]

# ------------------------------------------------------------------ state --

def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {'last_run': None, 'findings': [], 'runs': 0}

def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f, indent=1, ensure_ascii=False)
    os.replace(tmp, STATE_PATH)

def now_wib():
    return datetime.now(WIB)

# -------------------------------------------------------------- crontab ----

def read_crontab():
    try:
        out = subprocess.run(['crontab', '-l'], capture_output=True, text=True, timeout=10)
    except Exception as e:
        return None, f'crontab -l failed: {e}'
    if out.returncode != 0:
        return None, (out.stderr or 'crontab -l returned non-zero').strip()
    lines = [l for l in out.stdout.splitlines() if l.strip() and not l.strip().startswith('#')]
    return lines, None

def cron_line_schedule(line):
    """Best-effort split of a crontab line into (schedule_fields, command)."""
    parts = line.split(None, 5)
    if len(parts) < 6:
        return None, line
    return parts[:5], parts[5]

def classify_cron_lines(lines):
    """Split into (repo_lines, other_lines). repo = command mentions this
    repo's absolute path. Never touches/flags the content of other_lines
    beyond counting them - they belong to other projects (You etc)."""
    repo_lines, other_lines = [], []
    for line in lines:
        if BASE_DIR in line:
            repo_lines.append(line)
        else:
            other_lines.append(line)
    return repo_lines, other_lines

def check_cron(findings):
    if platform.system() == 'Darwin':
        # Cron for this repo only runs on the WSL automation host. On a macOS
        # checkout, crontab -l reflects the local machine, not the repo's
        # jobs, so treat the whole check as not applicable rather than
        # producing false "missing job" findings.
        return {'installed_repo_jobs': [], 'other_repo_blocks_count': 0,
                'registry_status': [], 'error': None,
                'skipped': 'not_applicable_macos: automation host is WSL'}
    lines, err = read_crontab()
    result = {'installed_repo_jobs': [], 'other_repo_blocks_count': 0,
              'registry_status': [], 'error': err}
    if lines is None:
        findings.append({'kind': 'cron_read_error', 'severity': 'warn',
                          'detail': err or 'could not read crontab'})
        return result
    repo_lines, other_lines = classify_cron_lines(lines)
    result['other_repo_blocks_count'] = len(other_lines)
    if other_lines:
        findings.append({
            'kind': 'other_repo_cron_present', 'severity': 'info',
            'detail': f'{len(other_lines)} crontab line(s) belong to other projects '
                      '(e.g. You repliz/goakal blocks) - existence noted, not modified.'})

    for line in repo_lines:
        sched, cmd = cron_line_schedule(line)
        result['installed_repo_jobs'].append({'schedule': ' '.join(sched) if sched else None,
                                               'command': cmd})

    acks = load_job_acks()
    for reg in CRON_REGISTRY:
        job = reg['job']
        installed = any(reg['match'] in line for line in repo_lines)
        status = {'job': job, 'installed': installed}
        if not installed:
            status['state'] = 'missing_from_crontab'
            findings.append({'kind': 'cron_missing', 'severity': 'fail', 'job': job,
                              'detail': f'"{reg["match"]}" not found in installed crontab '
                                        '(expected per registry).'})
            result['registry_status'].append(status)
            continue

        # Determine last-activity timestamp: take the FRESHEST of all
        # available signals (heartbeat, state ts, log mtime). Preferring
        # heartbeat alone false-flagged jobs whose heartbeat is sparser than
        # their cron (e.g. vexa-auto: */5 cron, hourly idle heartbeat, but the
        # log is appended every tick).
        signals = []
        if reg.get('heartbeat_job'):
            hb = latest_heartbeat_ts(reg['heartbeat_job'])
            if hb:
                signals.append((hb, 'heartbeat'))
        if reg.get('state_file') and os.path.exists(reg['state_file']):
            try:
                with open(reg['state_file']) as f:
                    sd = json.load(f)
                v = sd.get(reg.get('state_ts_field'))
                if v:
                    signals.append((float(v), 'state_file'))
            except Exception:
                pass
        if reg.get('log_file') and os.path.exists(reg['log_file']):
            signals.append((os.path.getmtime(reg['log_file']), 'log_mtime'))
        last_ts, source = max(signals) if signals else (None, None)

        status['last_activity_ts'] = last_ts
        status['last_activity_source'] = source
        if last_ts is None:
            status['state'] = 'no_signal'
            findings.append({'kind': 'cron_no_signal', 'severity': 'warn', 'job': job,
                              'detail': 'installed in crontab but no heartbeat/state/log '
                                        'signal found yet (may be brand new).'})
        else:
            age_min = (time.time() - last_ts) / 60
            expected = reg.get('signal_cadence_minutes', reg['cadence_minutes'])
            if age_min > expected * SILENT_MULTIPLIER:
                status['state'] = 'silent'
                findings.append({
                    'kind': 'cron_silent', 'severity': 'fail', 'job': job,
                    'detail': f'no activity for {age_min/60:.1f}h, expected every '
                              f'{expected}min (>{SILENT_MULTIPLIER}x cadence breached), '
                              f'signal source={source}.'})
            else:
                status['state'] = 'ok'

        # fail rows / needs_reauth in the lookback window, if heartbeat-integrated.
        # Acked rows are excluded; only un-acked fails <48h old rate 'fail'
        # severity -- older residue (e.g. a resolved outage burst) is info.
        if reg.get('heartbeat_job'):
            ack = acks.get(reg['heartbeat_job'], 0.0)
            fails, fails_recent, reauths = heartbeat_fail_scan(
                reg['heartbeat_job'], FAIL_LOOKBACK_DAYS, ack_epoch=ack)
            status['recent_fails'] = fails
            status['recent_fails_48h'] = fails_recent
            status['recent_needs_reauth'] = reauths
            if fails:
                sev = 'fail' if fails_recent else 'info'
                aged = '' if fails_recent else ' (all >48h old -- resolved/aged; Ack on the dashboard to clear)'
                findings.append({'kind': 'cron_recent_fail', 'severity': sev, 'job': job,
                                  'detail': f'{fails} un-acked fail row(s) in last '
                                            f'{FAIL_LOOKBACK_DAYS}d, {fails_recent} within 48h{aged}.'})
            if reauths:
                findings.append({'kind': 'cron_needs_reauth', 'severity': 'warn', 'job': job,
                                  'detail': 'auth token expired for this job '
                                            f'({reauths} row(s) in {FAIL_LOOKBACK_DAYS}d) -- '
                                            'refresh the token; clears on the next successful run.'})
        result['registry_status'].append(status)
    return result

# ---------------------------------------------------------------- heartbeat --

def _read_heartbeat_rows():
    if not os.path.exists(HEARTBEAT_LOG):
        return []
    rows = []
    with open(HEARTBEAT_LOG, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows

def _row_epoch(row):
    try:
        return datetime.fromisoformat(row['ts_wib']).timestamp()
    except Exception:
        return None

def latest_heartbeat_ts(job):
    best = None
    for row in _read_heartbeat_rows():
        if row.get('job') != job:
            continue
        e = _row_epoch(row)
        if e and (best is None or e > best):
            best = e
    return best

def load_job_acks():
    """journal/state/job_acks.json: {job: ack_epoch}. Fail rows at/before the
    ack are considered handled (the owner pressed Ack on the dashboard)."""
    path = os.path.join(BASE_DIR, 'journal', 'state', 'job_acks.json')
    try:
        with open(path, encoding='utf-8') as f:
            return {k: float(v) for k, v in json.load(f).items()}
    except Exception:
        return {}

def heartbeat_fail_scan(job, lookback_days, ack_epoch=0.0):
    """Counts fail/needs_reauth rows in the window, EXCLUDING rows the owner has
    acked. Also returns how many un-acked fails are recent (<48h) -- only
    those justify 'fail' severity; older residue is informational."""
    cutoff = time.time() - lookback_days * 86400
    recent_cutoff = time.time() - 48 * 3600
    fails = fails_recent = reauths = 0
    for row in _read_heartbeat_rows():
        if row.get('job') != job:
            continue
        e = _row_epoch(row)
        if e is None or e < cutoff:
            continue
        if row.get('status') == 'fail' and e > ack_epoch:
            fails += 1
            if e >= recent_cutoff:
                fails_recent += 1
        if row.get('needs_reauth') and e > ack_epoch:
            reauths += 1
    return fails, fails_recent, reauths

def write_heartbeat(status, summary, needs_reauth=False):
    if not os.path.exists(HEARTBEAT_PY):
        return
    try:
        subprocess.run([sys.executable, HEARTBEAT_PY, '--job', 'harness-health',
                        '--status', status, '--summary', summary[:300]] +
                       (['--needs-reauth'] if needs_reauth else []),
                       capture_output=True, text=True, timeout=15)
    except Exception:
        pass

# -------------------------------------------------------------- staleness --

def _file_json_ts(path, ts_field, ts_kind):
    try:
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
    except Exception:
        return None
    if not ts_field:
        return None
    v = d.get(ts_field)
    if v is None:
        return None
    try:
        if ts_kind == 'epoch':
            return float(v)
        if ts_kind == 'iso':
            return datetime.fromisoformat(v).timestamp()
    except Exception:
        return None
    return None

def check_staleness(findings):
    results = []
    for row in STALENESS_TABLE:
        path = os.path.join(BASE_DIR, row['path'])
        entry = {'name': row['name'], 'path': row['path']}
        if not os.path.exists(path):
            entry['state'] = 'not_built_yet'
            results.append(entry)
            continue
        if row['max_age_hours'] is None:
            entry['state'] = 'exempt'
            results.append(entry)
            continue
        ts = _file_json_ts(path, row['ts_field'], row['ts_kind'])
        source = 'json_field'
        if ts is None:
            ts = os.path.getmtime(path)
            source = 'file_mtime'
        age_h = (time.time() - ts) / 3600
        entry['age_hours'] = round(age_h, 1)
        entry['source'] = source
        entry['max_age_hours'] = row['max_age_hours']
        if age_h > row['max_age_hours']:
            entry['state'] = 'stale'
            findings.append({'kind': 'state_stale', 'severity': 'fail', 'job': row['name'],
                              'detail': f'{row["name"]} last updated {age_h:.1f}h ago '
                                        f'(expected <= {row["max_age_hours"]}h, source={source}).'})
        else:
            entry['state'] = 'fresh'
        results.append(entry)
    return results

# ------------------------------------------------------------ unused skills --

def _load_routines_blob():
    """journal/state/routines.json commands (lowercased) - a skill named there
    is SOP/cron-driven by design, even if it's rarely mentioned by name in
    activity_log.jsonl. Graceful (empty blob) if the file is missing."""
    if not os.path.exists(ROUTINES_PATH):
        return ''
    try:
        with open(ROUTINES_PATH, encoding='utf-8') as f:
            data = json.load(f)
        routines = data.get('routines', []) if isinstance(data, dict) else []
        return json.dumps(routines, ensure_ascii=False).lower()
    except Exception:
        return ''

def _is_cron_or_sop_driven(name):
    """A skill is exempt from the unused-skill check if it's (a) matched by
    a CRON_REGISTRY entry's `match` string, or (b) named in a
    journal/state/routines.json routine's command - both mean the skill runs
    on a schedule/SOP and just never gets textually mentioned in
    activity_log.jsonl, not that nobody uses it."""
    name_l = name.lower()
    name_us = name_l.replace('-', '_')
    if any(name_l in reg['match'].lower() for reg in CRON_REGISTRY):
        return True
    blob = _load_routines_blob()
    return name_l in blob or name_us in blob

def check_unused_skills(findings):
    if not os.path.isdir(SKILLS_DIR):
        return []
    cutoff = time.time() - UNUSED_SKILL_DAYS * 86400
    log_text_recent = []
    if os.path.exists(ACTIVITY_LOG):
        with open(ACTIVITY_LOG, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                ts = None
                try:
                    ts = datetime.fromisoformat(row['ts_wib']).timestamp()
                except Exception:
                    pass
                if ts is None or ts >= cutoff:
                    log_text_recent.append(json.dumps(row, ensure_ascii=False).lower())
    blob = '\n'.join(log_text_recent)
    results = []
    for name in sorted(os.listdir(SKILLS_DIR)):
        skill_path = os.path.join(SKILLS_DIR, name)
        if not os.path.isdir(skill_path):
            continue
        mentioned = name.lower() in blob or name.replace('-', '_').lower() in blob
        cron_or_sop = _is_cron_or_sop_driven(name)
        results.append({'skill': name, 'mentioned_last_%dd' % UNUSED_SKILL_DAYS: mentioned,
                         'cron_or_sop_driven': cron_or_sop})
        if not mentioned and not cron_or_sop:
            findings.append({'kind': 'unused_skill', 'severity': 'info', 'job': name,
                              'detail': f'no activity_log mention in the last '
                                        f'{UNUSED_SKILL_DAYS}d.'})
    return results

# ------------------------------------------------------------- auth probes --

def _load_env_file(path):
    env = {}
    if not os.path.exists(path):
        return env
    for line in open(path, encoding='utf-8'):
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env

def _slack_probe(token_env_path):
    env = _load_env_file(token_env_path)
    token = env.get('SLACK_USER_TOKEN') or env.get('SLACK_BOT_TOKEN')
    if not token:
        return 'skip', 'no token in file'
    try:
        req = urllib.request.Request('https://slack.com/api/auth.test',
                                      headers={'Authorization': f'Bearer {token}'})
        resp = json.load(urllib.request.urlopen(req, timeout=15))
        return ('ok', resp.get('user', '?')) if resp.get('ok') else \
               ('needs_reauth', resp.get('error', 'auth.test failed'))
    except Exception as e:
        return 'fail', str(e)

def _fathom_probe():
    script = os.path.join(SKILLS_DIR, 'fathom-connector', 'scripts', 'fathom_client.py')
    if not os.path.exists(script):
        return 'skip', 'fathom_client.py not found'
    try:
        out = subprocess.run([sys.executable, script, '--action', 'list', '--limit', '1'],
                             capture_output=True, text=True, timeout=30)
        if out.returncode == 0:
            return 'ok', 'list --limit 1 succeeded'
        return 'needs_reauth', (out.stderr or out.stdout)[:200]
    except Exception as e:
        return 'fail', str(e)

def _jira_probe(token_env_path):
    env = _load_env_file(token_env_path)
    if not env.get('JIRA_API_TOKEN'):
        return 'skip', 'no JIRA_API_TOKEN in file'
    script = os.path.join(SKILLS_DIR, 'jira-connector', 'scripts', 'jira_client.py')
    if not os.path.exists(script):
        return 'skip', 'jira_client.py not found'
    try:
        out = subprocess.run([sys.executable, script, 'daily-digest'],
                             capture_output=True, text=True, timeout=45)
        if out.returncode == 0:
            return 'ok', 'daily-digest succeeded'
        return 'needs_reauth', (out.stderr or out.stdout)[:200]
    except Exception as e:
        return 'fail', str(e)

def _gcal_probe():
    script = os.path.join(SKILLS_DIR, 'google-calendar-connector', 'gcal_manager.py')
    if not os.path.exists(script):
        return 'skip', 'gcal_manager.py not found'
    try:
        out = subprocess.run([sys.executable, script, 'list', '--profile', 'work', '--json',
                              '--days-back', '0', '--days-forward', '0'],
                             capture_output=True, text=True, timeout=30)
        if out.returncode == 0:
            return 'ok', 'list days-forward=0 succeeded'
        return 'needs_reauth', (out.stderr or out.stdout)[:200]
    except Exception as e:
        return 'fail', str(e)

def _metabase_probe():
    script = os.path.join(SKILLS_DIR, 'metabase-connector', 'scripts', 'metabase.js')
    if not os.path.exists(script):
        return 'skip', 'no metabase.js / no session token file found'
    return 'skip', 'metabase session token is manual OAuth-cookie; not probed automatically'

def run_full_probes(findings):
    results = {}
    probes = [
        ('slack', os.path.join(SKILLS_DIR, 'slack-connector', 'token.env'),
         lambda p=os.path.join(SKILLS_DIR, 'slack-connector', 'token.env'): _slack_probe(p)),
        ('fathom', os.path.join(SKILLS_DIR, 'fathom-connector', 'token.env'), _fathom_probe),
        ('jira', os.path.join(SKILLS_DIR, 'jira-connector', 'token.env'),
         lambda p=os.path.join(SKILLS_DIR, 'jira-connector', 'token.env'): _jira_probe(p)),
        ('gcal', None, _gcal_probe),
        ('metabase', None, _metabase_probe),
    ]
    for name, token_path, fn in probes:
        if token_path is not None and not os.path.exists(token_path):
            results[name] = {'state': 'skip', 'detail': 'token.env not present'}
            continue
        state, detail = fn()
        results[name] = {'state': state, 'detail': detail}
        if state == 'needs_reauth':
            findings.append({'kind': 'auth_needs_reauth', 'severity': 'fail', 'job': name,
                              'detail': detail})
        elif state == 'fail':
            findings.append({'kind': 'auth_probe_fail', 'severity': 'warn', 'job': name,
                              'detail': detail})
        time.sleep(API_PAUSE)
    return results

# -------------------------------------------------------------------- run --

def cmd_run(args):
    findings = []
    t0 = time.time()
    cron_result = check_cron(findings)
    staleness_result = check_staleness(findings)
    unused_result = check_unused_skills(findings)
    probe_result = run_full_probes(findings) if args.full else None

    state = load_state()
    state['last_run'] = time.time()
    state['runs'] = state.get('runs', 0) + 1
    state['findings'] = findings
    state['cron'] = cron_result
    state['staleness'] = staleness_result
    state['unused_skills'] = unused_result
    if probe_result is not None:
        state['last_full_probe'] = {'ts': time.time(), 'results': probe_result}
    save_state(state)

    append_monthly_report(findings, cron_result, staleness_result, unused_result,
                           probe_result, args.full)

    fails = sum(1 for f in findings if f['severity'] == 'fail')
    warns = sum(1 for f in findings if f['severity'] == 'warn')
    summary = (f'{len(findings)} findings ({fails} fail, {warns} warn) - '
               f'{sum(1 for r in cron_result["registry_status"] if r.get("state") == "silent")} '
               f'silent cron, '
               f'{sum(1 for r in staleness_result if r.get("state") == "stale")} stale state, '
               f'{sum(1 for f in findings if f["kind"] == "unused_skill")} '
               f'unused skills')
    # Heartbeat status reflects whether THIS RUN completed, not what the audit
    # found -- findings are content (carried in the summary), not a job failure.
    # A crashed run simply never emits, which the silent-cron check catches.
    write_heartbeat('ok', summary,
                     needs_reauth=any(f['kind'] == 'auth_needs_reauth' for f in findings))
    print(f'harness_health run done in {time.time()-t0:.0f}s: {summary}')

def append_monthly_report(findings, cron_result, staleness_result, unused_result,
                           probe_result, full):
    os.makedirs(REPORT_DIR, exist_ok=True)
    month_path = os.path.join(REPORT_DIR, now_wib().strftime('%Y-%m') + '.md')
    lines = [f'## Run {now_wib().isoformat(timespec="seconds")} '
             f'({"full" if full else "quick"})', '']
    fails = [f for f in findings if f['severity'] == 'fail']
    warns = [f for f in findings if f['severity'] == 'warn']
    infos = [f for f in findings if f['severity'] == 'info']
    if not findings:
        lines.append('No findings. Harness clean.')
    for label, group in (('🔴 FAIL', fails), ('🟡 WARN', warns), ('ℹ️ INFO', infos)):
        if not group:
            continue
        lines.append(f'**{label}**')
        for f in group:
            job = f.get('job', '')
            lines.append(f'- [{f["kind"]}]{" " + job if job else ""}: {f["detail"]}')
        lines.append('')
    lines.append(f'Other-repo cron blocks present (not touched): '
                 f'{cron_result.get("other_repo_blocks_count", 0)}')
    lines.append('')
    with open(month_path, 'a', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')

# ------------------------------------------------------------------ report --

def cmd_report(args):
    state = load_state()
    findings = state.get('findings', [])
    if not findings:
        if state.get('last_run'):
            print('No findings from last run. Harness clean.')
        else:
            print('harness_health has never been run. Run `harness_health.py run` first.')
        return
    fails = [f for f in findings if f['severity'] == 'fail']
    warns = [f for f in findings if f['severity'] == 'warn']
    infos = [f for f in findings if f['severity'] == 'info']
    last_run = state.get('last_run')
    last_run_str = datetime.fromtimestamp(last_run, WIB).strftime('%Y-%m-%d %H:%M WIB') \
        if last_run else 'never'
    print(f'## 🩺 Harness Health (last run: {last_run_str})\n')
    if not findings:
        print('No findings. Harness clean.')
        return
    for label, group in (('🔴 FAIL', fails), ('🟡 WARN', warns)):
        if not group:
            continue
        print(f'**{label}**')
        for f in group:
            job = f.get('job', '')
            print(f'- [{f["kind"]}]{" " + job if job else ""}: {f["detail"]}')
        print()
    if args.all and infos:
        print('**ℹ️ INFO**')
        for f in infos:
            job = f.get('job', '')
            print(f'- [{f["kind"]}]{" " + job if job else ""}: {f["detail"]}')

# -------------------------------------------------------------------- main --

def main():
    p = argparse.ArgumentParser(description='Harness health monthly self-check')
    sub = p.add_subparsers(dest='cmd')
    rp = sub.add_parser('run')
    rp.add_argument('--full', action='store_true',
                     help='also run cheapest read-only auth probes per token.env')
    repp = sub.add_parser('report')
    repp.add_argument('--all', action='store_true', help='include info-level findings')
    args = p.parse_args()
    if args.cmd == 'report':
        cmd_report(args)
    else:
        if not hasattr(args, 'full'):
            args.full = False
        cmd_run(args)

if __name__ == '__main__':
    main()
