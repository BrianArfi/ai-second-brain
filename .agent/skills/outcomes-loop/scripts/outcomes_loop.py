#!/usr/bin/env python3
"""
outcomes_loop.py - Post-launch outcomes loop: track shipped features against
their PRD success-criteria metrics, pulled weekly from Mixpanel and Metabase.

Design (per plan `bangun-aja-semuanya-barengan-wiggly-noodle`, Component 4):
  - Pure mechanical collector, no LLM. Clones the mention_ledger.py conventions:
    BASE_DIR from __file__, atomic .tmp + os.replace state writes, argparse
    subcommands, stdlib only.
  - `check` pulls each metric's configured source (Mixpanel query-events /
    query-funnel / retention, or Metabase `sql`) and compares last_value
    against target/direction -> on_track | off_track | no_data | needs_reauth.
  - A Metabase non-zero exit OR an auth-shaped error (401 / "Unauthenticated" /
    session expired / fetch failed) -> that metric's status becomes
    `needs_reauth`, the whole run still completes, and a heartbeat row with
    --needs-reauth is written. The check command NEVER crashes the run over a
    single bad metric.
  - history[] capped at 26 entries per metric (~6 months weekly).

Subcommands:
  add-feature     register a shipped feature to track
  add-metric      attach a success-criteria metric to a feature
  check           pull latest values for all (or one) feature's metrics
  report          briefing-ready markdown (on_track/off_track/no_data/needs_reauth)
  close-feature   stop tracking (status -> closed), keeps history

State: journal/state/outcomes.json
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
STATE_PATH = os.path.join(BASE_DIR, 'journal', 'state', 'outcomes.json')
MIXPANEL_CLIENT = os.path.join(BASE_DIR, '.agent', 'skills', 'mixpanel-connector', 'scripts', 'mixpanel_client.py')
METABASE_CLIENT = os.path.join(BASE_DIR, '.agent', 'skills', 'metabase-connector', 'scripts', 'metabase.js')

WIB = timezone(timedelta(hours=7))
HISTORY_CAP = 26
API_PAUSE = 0.15
SUBPROC_TIMEOUT = 60

AUTH_ERROR_RE = re.compile(
    r'401|unauthenticated|unauthorized|session.*(expired|invalid)|not[_ ]logged[_ ]in|'
    r'fetch failed|econnrefused|enotfound',
    re.IGNORECASE)

# ------------------------------------------------------------------- state --

def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {'features': {}, 'last_check': None, 'needs_reauth': False}

def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f, indent=1, ensure_ascii=False)
    os.replace(tmp, STATE_PATH)

def now_wib_iso():
    return datetime.now(WIB).isoformat(timespec='seconds')

def slugify(text):
    s = re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')
    return re.sub(r'-+', '-', s)

# --------------------------------------------------------------- fetchers --

def run_cmd(cmd, timeout=SUBPROC_TIMEOUT):
    """Run a subprocess, always returning (rc, stdout, stderr) - never raises."""
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return out.returncode, out.stdout, out.stderr
    except subprocess.TimeoutExpired as e:
        return 124, e.stdout or '', (e.stderr or '') + '\ntimeout'
    except Exception as e:
        return 1, '', str(e)

def _clean_note(text):
    """Strip Node's noisy MODULE_TYPELESS_PACKAGE_JSON warning boilerplate so
    last_note surfaces the actual error line instead of the warning."""
    if not text:
        return text
    lines = [l for l in text.splitlines()
             if 'MODULE_TYPELESS_PACKAGE_JSON' not in l
             and 'Reparsing as ES module' not in l
             and 'add "type": "module"' not in l
             and '--trace-warnings' not in l]
    return '\n'.join(lines).strip() or text

def _is_auth_error(rc, out, err):
    blob = f'{out}\n{err}'
    if rc != 0 and AUTH_ERROR_RE.search(blob):
        return True
    if AUTH_ERROR_RE.search(blob):
        return True
    return False

def _extract_json(text):
    """Best-effort: find the first {...} or [...] block and json.loads it."""
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r'(\{.*\}|\[.*\])', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            return None
    return None

def _sum_numeric_leaves(obj, cap=100000):
    """Fallback aggregator: sum every numeric leaf in a nested dict/list. Used
    when the response shape isn't one we specifically parse (still gives a
    directionally-useful single number rather than no_data)."""
    total = 0.0
    seen = 0

    def walk(o):
        nonlocal total, seen
        if seen > cap:
            return
        if isinstance(o, bool):
            return
        if isinstance(o, (int, float)):
            total += o
            seen += 1
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(obj)
    return total if seen else None

def fetch_mixpanel(source):
    """source: {mode: query-events|query-funnel|retention, ...params, lookback_days}
    Returns (value, status, note). status in {'ok','no_data','needs_reauth'}"""
    mode = source.get('mode', 'query-events')
    lookback = int(source.get('lookback_days', 7))
    to_date = datetime.now(WIB).date()
    from_date = to_date - timedelta(days=lookback)
    cmd = [sys.executable, MIXPANEL_CLIENT]
    if mode == 'query-events':
        cmd += ['query-events', '--events', source.get('events', ''),
                '--from', str(from_date), '--to', str(to_date)]
        if source.get('unit'):
            cmd += ['--unit', source['unit']]
    elif mode == 'query-funnel':
        cmd += ['query-funnel', '--id', str(source.get('funnel_id', '')),
                '--from', str(from_date), '--to', str(to_date)]
        if source.get('unit'):
            cmd += ['--unit', source['unit']]
    elif mode == 'retention':
        cmd += ['retention', '--from', str(from_date), '--to', str(to_date)]
        if source.get('born_event'):
            cmd += ['--born-event', source['born_event']]
        if source.get('unit'):
            cmd += ['--unit', source['unit']]
    else:
        return None, 'no_data', f'unknown mixpanel mode: {mode}'

    rc, out, err = run_cmd(cmd)
    if _is_auth_error(rc, out, err):
        return None, 'needs_reauth', (err or out)[:300]
    parsed = _extract_json(out)
    if parsed is None:
        return None, 'no_data', 'unparseable mixpanel response'
    if isinstance(parsed, dict) and 'error' in parsed:
        err_blob = json.dumps(parsed)
        if AUTH_ERROR_RE.search(err_blob):
            return None, 'needs_reauth', err_blob[:300]
        return None, 'no_data', err_blob[:300]

    value = None
    if mode == 'query-funnel' and isinstance(parsed, dict):
        # mixpanel funnel payloads commonly key by date -> {steps:[...], ...}
        # or carry an overall_conv_ratio. Try both shapes conservatively.
        if 'overall_conv_ratio' in parsed:
            value = parsed['overall_conv_ratio']
        else:
            for v in parsed.get('data', parsed).values() if isinstance(parsed.get('data', parsed), dict) else []:
                if isinstance(v, dict) and 'steps' in v and v['steps']:
                    value = v['steps'][-1].get('overall_conv_ratio')
                    break
    if value is None:
        value = _sum_numeric_leaves(parsed)
    if value is None:
        return None, 'no_data', 'no numeric value found in response'
    return value, 'ok', None

def fetch_metabase(source):
    """source: {db_id, sql}. Returns (value, status, note)."""
    db_id = source.get('db_id')
    sql = source.get('sql')
    if not db_id or not sql:
        return None, 'no_data', 'missing db_id/sql'
    cmd = ['node', METABASE_CLIENT, 'sql', str(db_id), sql]
    rc, out, err = run_cmd(cmd)
    if rc != 0 or _is_auth_error(rc, out, err):
        return None, 'needs_reauth', _clean_note(err or out)[:300]
    parsed = _extract_json(out)
    if parsed is None:
        return None, 'no_data', 'unparseable metabase response'
    value = None
    rows = None
    if isinstance(parsed, dict):
        rows = parsed.get('rows') or parsed.get('data', {}).get('rows') if isinstance(parsed.get('data'), dict) else parsed.get('rows')
    elif isinstance(parsed, list):
        rows = parsed
    if rows:
        first_row = rows[0]
        if isinstance(first_row, dict):
            for v in first_row.values():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    value = v
                    break
        elif isinstance(first_row, list):
            for v in first_row:
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    value = v
                    break
    if value is None:
        value = _sum_numeric_leaves(parsed)
    if value is None:
        return None, 'no_data', 'no numeric value found in response'
    return value, 'ok', None

def evaluate_direction(value, target, direction):
    if value is None or target is None:
        return 'no_data'
    try:
        value = float(value)
        target = float(target)
    except (TypeError, ValueError):
        return 'no_data'
    if direction == 'above':
        return 'on_track' if value >= target else 'off_track'
    if direction == 'below':
        return 'on_track' if value <= target else 'off_track'
    return 'no_data'

# ---------------------------------------------------------------- commands --

def cmd_add_feature(args):
    state = load_state()
    fid = args.id or slugify(args.title)
    if fid in state['features']:
        sys.exit(f'feature already exists: {fid}')
    state['features'][fid] = {
        'id': fid,
        'title': args.title,
        'shipped_on': args.shipped_on,
        'prd': args.prd,
        'project': args.project,
        'review_until': args.review_until,
        'status': 'active',
        'metrics': [],
        'created_at': now_wib_iso(),
        'updated_at': now_wib_iso(),
    }
    save_state(state)
    print(f'added feature: {fid}')

def cmd_add_metric(args):
    state = load_state()
    feat = state['features'].get(args.feature)
    if not feat:
        sys.exit(f'feature not found: {args.feature}')
    source = {}
    if args.source_kind == 'mixpanel':
        source = {'kind': 'mixpanel', 'mode': args.mode or 'query-events'}
        if args.events:
            source['events'] = args.events
        if args.funnel_id:
            source['funnel_id'] = args.funnel_id
        if args.born_event:
            source['born_event'] = args.born_event
        if args.unit:
            source['unit'] = args.unit
        source['lookback_days'] = args.lookback_days
    elif args.source_kind == 'metabase':
        source = {'kind': 'metabase', 'db_id': args.db_id, 'sql': args.sql}
    else:
        sys.exit('--source-kind must be mixpanel or metabase')
    metric = {
        'name': args.name,
        'target': args.target,
        'direction': args.direction,
        'source': source,
        'last_value': None,
        'last_checked': None,
        'history': [],
        'status': 'no_data',
    }
    feat['metrics'].append(metric)
    feat['updated_at'] = now_wib_iso()
    save_state(state)
    print(f'added metric "{args.name}" to {args.feature}')

def cmd_check(args):
    state = load_state()
    features = state['features']
    if args.feature:
        if args.feature not in features:
            sys.exit(f'feature not found: {args.feature}')
        targets = {args.feature: features[args.feature]}
    else:
        targets = {fid: f for fid, f in features.items() if f.get('status') == 'active'}

    any_reauth = False
    n_checked = 0
    for fid, feat in targets.items():
        for metric in feat.get('metrics', []):
            src = metric.get('source', {})
            kind = src.get('kind')
            if kind == 'mixpanel':
                value, status, note = fetch_mixpanel(src)
            elif kind == 'metabase':
                value, status, note = fetch_metabase(src)
            else:
                value, status, note = None, 'no_data', f'unknown source kind: {kind}'
            n_checked += 1

            if status == 'ok':
                m_status = evaluate_direction(value, metric.get('target'), metric.get('direction'))
                metric['last_value'] = value
            elif status == 'needs_reauth':
                m_status = 'needs_reauth'
                any_reauth = True
            else:
                m_status = 'no_data'

            metric['status'] = m_status
            metric['last_checked'] = now_wib_iso()
            if note:
                metric['last_note'] = note[:300]
            metric.setdefault('history', []).append({
                'ts': now_wib_iso(),
                'value': value,
                'status': m_status,
            })
            metric['history'] = metric['history'][-HISTORY_CAP:]
            time.sleep(API_PAUSE)
        feat['updated_at'] = now_wib_iso()

    state['last_check'] = now_wib_iso()
    state['needs_reauth'] = any_reauth
    save_state(state)

    heartbeat = os.path.join(BASE_DIR, '.agent', 'scripts', 'heartbeat.py')
    summary = f'{n_checked} metrics checked across {len(targets)} feature(s)'
    hb_cmd = [sys.executable, heartbeat, '--job', 'outcomes-loop',
              '--status', 'ok', '--summary', summary]
    if any_reauth:
        hb_cmd.append('--needs-reauth')
    run_cmd(hb_cmd, timeout=15)
    print(f'check done: {summary}' + (' (needs_reauth: some metrics)' if any_reauth else ''))

STATUS_ICON = {
    'on_track': '✅', 'off_track': '🔴', 'no_data': '⚪', 'needs_reauth': '⚠️',
}

def cmd_report(args):
    state = load_state()
    features = state['features']
    items = [(fid, f) for fid, f in features.items()
             if args.all or f.get('status') == 'active']
    if not items:
        print('No tracked features. Outcomes loop is empty.')
        return
    if state.get('needs_reauth'):
        print('⚠️ Some metrics need re-auth (Metabase session or Mixpanel access) — see below.\n')
    print(f'## 📈 Post-launch Outcomes ({len(items)} feature(s))\n')
    for fid, feat in items:
        print(f'### {feat["title"]}  `{fid}` [{feat.get("status")}]')
        meta = []
        if feat.get('shipped_on'):
            meta.append(f'shipped {feat["shipped_on"]}')
        if feat.get('project'):
            meta.append(feat['project'])
        if feat.get('prd'):
            meta.append(f'[PRD]({feat["prd"]})')
        if feat.get('review_until'):
            meta.append(f'review until {feat["review_until"]}')
        if meta:
            print('- ' + ' · '.join(meta))
        if not feat.get('metrics'):
            print('- (no metrics attached yet)')
        for m in feat.get('metrics', []):
            icon = STATUS_ICON.get(m.get('status'), '⚪')
            val = m.get('last_value')
            target = m.get('target')
            direction = '>=' if m.get('direction') == 'above' else '<='
            checked = m.get('last_checked') or 'never'
            line = f'  - {icon} **{m["name"]}**: {val} (target {direction} {target}) · checked {checked}'
            print(line)
            if m.get('status') in ('no_data', 'needs_reauth') and m.get('last_note'):
                print(f'      ↳ {m["last_note"][:160]}')
        print()

def cmd_close_feature(args):
    state = load_state()
    feat = state['features'].get(args.feature)
    if not feat:
        sys.exit(f'feature not found: {args.feature}')
    feat['status'] = 'closed'
    feat['closed_at'] = now_wib_iso()
    feat['updated_at'] = now_wib_iso()
    save_state(state)
    print(f'closed feature: {args.feature}')

# -------------------------------------------------------------------- main --

def main():
    p = argparse.ArgumentParser(description='Post-launch outcomes loop')
    sub = p.add_subparsers(dest='cmd')

    af = sub.add_parser('add-feature')
    af.add_argument('--title', required=True)
    af.add_argument('--id', help='override id (default: slugify(title))')
    af.add_argument('--shipped-on', dest='shipped_on')
    af.add_argument('--prd', help='PRD URL/link')
    af.add_argument('--project')
    af.add_argument('--review-until', dest='review_until')

    am = sub.add_parser('add-metric')
    am.add_argument('--feature', required=True)
    am.add_argument('--name', required=True)
    am.add_argument('--target', type=float, required=True)
    am.add_argument('--direction', choices=['above', 'below'], required=True)
    am.add_argument('--source-kind', choices=['mixpanel', 'metabase'], required=True)
    am.add_argument('--mode', choices=['query-events', 'query-funnel', 'retention'])
    am.add_argument('--events')
    am.add_argument('--funnel-id')
    am.add_argument('--born-event')
    am.add_argument('--unit')
    am.add_argument('--lookback-days', type=int, default=7)
    am.add_argument('--db-id')
    am.add_argument('--sql')

    ck = sub.add_parser('check')
    ck.add_argument('--feature')

    rp = sub.add_parser('report')
    rp.add_argument('--all', action='store_true')

    cf = sub.add_parser('close-feature')
    cf.add_argument('--feature', required=True)

    args = p.parse_args()
    dispatch = {
        'add-feature': cmd_add_feature,
        'add-metric': cmd_add_metric,
        'check': cmd_check,
        'report': cmd_report,
        'close-feature': cmd_close_feature,
    }
    fn = dispatch.get(args.cmd)
    if not fn:
        p.print_help()
        sys.exit(1)
    fn(args)

if __name__ == '__main__':
    main()
