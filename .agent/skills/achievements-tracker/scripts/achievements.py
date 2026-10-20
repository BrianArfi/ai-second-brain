#!/usr/bin/env python3
"""Achievements tracker: finds product wins in real data and turns them into CV bullets.

Flow
  scan          monthly, from a scheduler. Reads the product's monthly numbers from the
                configured source, finds series that grew a lot against their recent low
                (or since a segment launched), and records each one as a candidate with a
                draft CV bullet, the numbers, caveats and the owner's logged decisions in
                that window. New or changed candidates go to the owner in one message.
                When the source cannot be reached, the scan records that and tries again
                on the next run.
  auto-approve  silence is approval: a candidate with no reply after `grace_days` goes
                into the CV with the draft wording. Newer numbers replace the older bullet.
  approve       the owner said yes (or gave their wording): insert the bullet into the CV,
                log it in the evidence file, mark it approved.
  reject        the owner said no: remove the bullet if it went in, and keep the record so
                the same win is raised again only when it grows 25% more.
  list / synced review helpers.

Everything specific to one person lives in the config file: CV path, product name, the
data source, where the message goes. See config.example.json and SKILL.md.

Stdlib only.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

SKILL_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

# ---------------------------------------------------------------- config

def _load_config():
    path = os.environ.get('ACHIEVEMENTS_CONFIG')
    if not path:
        own = os.path.join(SKILL_DIR, 'config.json')
        path = own if os.path.exists(own) else os.path.join(SKILL_DIR, 'config.example.json')
    with open(path, encoding='utf-8') as f:
        cfg = json.load(f)
    root = os.environ.get('ACHIEVEMENTS_ROOT') or cfg.get('root') or '../../..'
    cfg['root'] = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(path)), root))
    return cfg

CFG = _load_config()

def rpath(key):
    return os.path.join(CFG['root'], CFG[key])

TZ = timezone(timedelta(hours=CFG.get('timezone_offset_hours', 0)))
GRACE_DAYS = CFG.get('grace_days', 7)
MIN_RATIO = CFG.get('min_ratio', 1.5)          # the closed month is at least this x its recent low
LOOKBACK = CFG.get('lookback_months', 6)        # "recent" = this many months before it
FLOORS = CFG.get('floors', {})                  # ignore tiny numbers, per metric
REFRESH_RATIO = 1.25                            # an approved or rejected win comes back only this much bigger
UNREACHABLE_ALERT_DAY = CFG.get('unreachable_alert_day', 10)
MONEY = set(CFG.get('money_metrics', ['revenue']))
CURRENCY = CFG.get('currency', '')
PRODUCT = CFG.get('product', 'the product')
GROUP_METRIC = CFG.get('group_metric', 'revenue')

def now():
    return datetime.now(TZ)

def load_state():
    p = rpath('state_path')
    if os.path.exists(p):
        with open(p, encoding='utf-8') as f:
            state = json.load(f)
    else:
        state = {'next_seq': 1, 'candidates': {}, 'scans': {}}
    for c in state['candidates'].values():  # keys from before the generic version
        if c['key'] == 'store':
            c['key'] = 'total'
        elif c['key'].startswith('group:') and c['key'].count(':') == 2:
            c['key'] = c['key'].rsplit(':', 1)[0]
        if c.get('scope') == 'store':
            c['scope'] = 'total'
    return state

def save_state(state):
    p = rpath('state_path')
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.write('\n')
    os.replace(tmp, p)

def month_add(m, n):
    y, mo = map(int, m.split('-'))
    idx = y * 12 + (mo - 1) + n
    return f'{idx // 12:04d}-{idx % 12 + 1:02d}'

def month_name(m):
    return datetime.strptime(m, '%Y-%m').strftime('%b %Y')

def fmt(metric, v):
    if metric not in MONEY:
        return f'{int(v):,}'
    cur = f'{CURRENCY} ' if CURRENCY else ''
    return f'{cur}{v / 1e6:.2f}M' if v >= 1e6 else f'{cur}{v / 1e3:.0f}K'

def metric_label(metric):
    return CFG.get('metric_labels', {}).get(metric, f'monthly {metric}')

def group_label(grp):
    return CFG.get('group_labels', {}).get(grp, grp.replace('-', ' '))

def read_env(path):
    env = {}
    if os.path.exists(path):
        for line in open(path, encoding='utf-8', errors='replace'):
            if '=' in line and not line.lstrip().startswith('#'):
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip().strip('"\'')
    return env

# ---------------------------------------------------------------- sources

class Unreachable(Exception):
    pass

class NeedsReauth(Exception):
    pass

def _metabase_rows(src, sql):
    """Rows as dicts. Runs the connector in a temp dir so its results file never collides."""
    js = os.path.join(CFG['root'], src.get('connector', '.agent/skills/metabase-connector/scripts/metabase.js'))
    env = read_env(os.path.join(CFG['root'], '.env'))
    env.update(read_env(os.path.join(os.path.dirname(js), '..', '.env')))
    with tempfile.TemporaryDirectory() as tmp:
        try:
            p = subprocess.run(['node', js, 'sql', str(src['db_id']), sql], cwd=tmp,
                               capture_output=True, text=True, timeout=180,
                               env={**env, **os.environ})  # metabase.js reads .env from cwd only
        except subprocess.TimeoutExpired:
            raise Unreachable('timeout')
        blob = (p.stdout or '') + (p.stderr or '')
        if re.search(r'401|unauthenticated|Unauthorized', blob, re.I):
            raise NeedsReauth(blob[-300:])
        out = os.path.join(tmp, 'scratch', 'metabase_results.json')
        if not os.path.exists(out):
            raise Unreachable(blob[-300:])
        with open(out, encoding='utf-8') as f:
            return json.load(f)

def _rows_to_series(total_rows, group_rows):
    """Rows carry `m` (YYYY-MM), metric columns, and `grp` for group rows."""
    total = {r['m']: {k: (v or 0) for k, v in r.items() if k != 'm'} for r in total_rows if r.get('m')}
    groups = {}
    for r in group_rows:
        if r.get('m') and r.get('grp'):
            groups.setdefault(r['grp'], {})[r['m']] = {k: (v or 0) for k, v in r.items() if k not in ('m', 'grp')}
    return {'total': total, 'groups': groups}

def fetch_series():
    """{'total': {month: {metric: n}}, 'groups': {grp: {month: {metric: n}}}}"""
    src = CFG['source']
    kind = src.get('kind')
    if kind == 'metabase':
        return _rows_to_series(_metabase_rows(src, src['sql_total']),
                               _metabase_rows(src, src['sql_groups']) if src.get('sql_groups') else [])
    if kind == 'series-file':
        # Written by someone else: an agent turn that read a connected tool, an export, a sheet.
        p = os.path.join(CFG['root'], src['path'])
        if not os.path.exists(p):
            raise Unreachable(f'no series file at {src["path"]}')
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    if kind == 'command':
        try:
            p = subprocess.run(src['argv'], cwd=CFG['root'], capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise Unreachable(str(e))
        if p.returncode != 0:
            raise Unreachable((p.stdout + p.stderr)[-300:])
        return json.loads(p.stdout)
    sys.exit(f'config: unknown source kind {kind!r} (metabase | series-file | command)')

# ---------------------------------------------------------------- detection

def detect(series, month):
    """One finding per series that is a win in `month` (a closed month)."""
    findings = []

    def check(key, scope, metric, data):
        cur = (data.get(month) or {}).get(metric) or 0
        if not cur or cur < FLOORS.get(metric, 0):
            return
        window = [month_add(month, -i) for i in range(LOOKBACK, 0, -1)]
        present = [m for m in window if (data.get(m) or {}).get(metric)]
        launched = scope == 'group' and window[0] not in data and present
        if launched:
            base_m = present[0]  # first month on sale
        elif present:
            base_m = min(present, key=lambda m: data[m][metric])
        else:
            return
        base = data[base_m][metric]
        since = [m for m in window if m > base_m and m in data]
        if not base or cur / base < MIN_RATIO:
            return
        if any((data[m].get(metric) or 0) > cur for m in since):
            return  # not a new high since the low
        peak_m = max(data, key=lambda m: (data[m].get(metric) or 0))
        caveats = []
        cb, cc = data[base_m].get('cancel_pct'), data[month].get('cancel_pct')
        if cb is not None and cc is not None and cc - cb >= 5:
            caveats.append(f'Cancellations rose from {cb}% to {cc}% over the same period.')
        if peak_m < base_m and data[peak_m][metric] > cur:
            caveats.append(f'Still below the all-time peak ({month_name(peak_m)}: '
                           f'{fmt(metric, data[peak_m][metric])}); frame it as a recovery.')
        findings.append({'key': key, 'scope': scope, 'metric': metric, 'kind': 'launch' if launched else 'recovery',
                         'base_month': base_m, 'base': base, 'month': month, 'value': cur,
                         'ratio': round(cur / base, 1), 'caveats': caveats})

    total = series.get('total', series.get('store', {}))
    metrics = CFG.get('total_metrics') or sorted({k for v in total.values() for k in v if k != 'cancel_pct'})
    for metric in metrics:
        check(f'total:{metric}', 'total', metric, total)
    for grp, data in series.get('groups', {}).items():
        check(f'group:{grp}', 'group', GROUP_METRIC, data)

    # One bullet for the whole product, not one per metric: the lead metric first.
    tot = [f for f in findings if f['scope'] == 'total']
    if tot:
        lead_name = CFG.get('lead_metric', metrics[0] if metrics else None)
        lead = next((f for f in tot if f['metric'] == lead_name), tot[0])
        tot.sort(key=lambda f: f is not lead)
        merged = dict(lead, key='total', parts=[
            {k: f[k] for k in ('metric', 'base_month', 'base', 'value', 'ratio')} for f in tot],
            caveats=list(dict.fromkeys(c for f in tot for c in f['caveats'])))
        findings = [merged] + [f for f in findings if f['scope'] != 'total']
    return findings

def owner_decisions(base_month, month, key):
    """The owner's logged decisions in the window that match the series: the interview story."""
    d_cfg = CFG.get('decisions') or {}
    if not d_cfg.get('path'):
        return []
    path = os.path.join(CFG['root'], d_cfg['path'])
    if not os.path.exists(path):
        return []
    with open(path, encoding='utf-8') as f:
        items = json.load(f).get('items', {})
    items = items.values() if isinstance(items, dict) else items
    if key.startswith('group:'):
        grp = key.split(':', 1)[1]
        pattern = next((p for k, p in d_cfg.get('group_keywords', {}).items() if k in grp),
                       re.escape(group_label(grp)))
    else:
        pattern = d_cfg.get('total_keywords', re.escape(PRODUCT))
    start = datetime.strptime(month_add(base_month, -1), '%Y-%m').replace(tzinfo=TZ).timestamp()
    end = datetime.strptime(month_add(month, 1), '%Y-%m').replace(tzinfo=TZ).timestamp()
    who = d_cfg.get('decider_match', '')
    out = []
    for d in sorted(items, key=lambda d: d.get('decided_at') or 0, reverse=True):
        if d.get('status') != 'decided' or who not in (d.get('decider_slug') or ''):
            continue
        if not (start <= (d.get('decided_at') or 0) < end):
            continue
        if re.search(pattern, f"{d.get('title', '')} {d.get('project', '')}", re.I):
            out.append(f"{d['id']}: {d.get('title', '')}")
    return out[:5]

def draft_bullet(f):
    span = f"{month_name(f['base_month'])} and {month_name(f['month'])}"
    if f['scope'] == 'total':
        parts = [f"{metric_label(p['metric'])} from {fmt(p['metric'], p['base'])} to "
                 f"{fmt(p['metric'], p['value'])} ({p['ratio']}x)" for p in f.get('parts', [f])]
        joined = parts[0] if len(parts) == 1 else ', '.join(parts[:-1]) + ' and ' + parts[-1]
        return f"**{CFG.get('total_bullet_label', 'Product Growth')}:** Grew {PRODUCT}'s {joined} between {span}."
    name = group_label(f['key'].split(':', 1)[1])
    m = f['metric']
    if f['kind'] == 'launch':
        return (f"**{name.title()} Launch:** Launched {name} on {PRODUCT} and scaled its "
                f"{m} {f['ratio']}x, from {fmt(m, f['base'])} in the first month ({month_name(f['base_month'])}) "
                f"to {fmt(m, f['value'])} per month ({month_name(f['month'])}).")
    return (f"**{name.title()} Growth:** Grew {name} {m} on {PRODUCT} {f['ratio']}x, "
            f"from {fmt(m, f['base'])} to {fmt(m, f['value'])} per month between {span}.")

# ---------------------------------------------------------------- notify

def notify(text):
    """One message to the owner. Kinds: whatsapp-bridge, command (text on stdin), none."""
    n = CFG.get('notify') or {'kind': 'none'}
    kind = n.get('kind', 'none')
    if kind == 'none':
        print('notify (none configured):\n' + text)
        return False
    if kind == 'whatsapp-bridge':
        # A local whatsapp-mcp style bridge logged in as the owner: the recipient is their own
        # number, so this lands in "Message yourself". WA_SEND_MODE is set for this call only.
        code = ('import sys, whatsapp\n'
                'ok, resp = whatsapp.send_message(sys.argv[1], sys.stdin.read())\n'
                'print(ok, resp)\n'
                'sys.exit(0 if ok else 1)\n')
        argv, cwd = ['python3', '-c', code, n['recipient']], n['server_dir']
        env = dict(os.environ, WA_SEND_MODE='direct', WHATSAPP_API_URL=n.get('api_url', 'http://localhost:8080/api'))
    elif kind == 'command':
        argv, cwd, env = n['argv'], CFG['root'], dict(os.environ)
    else:
        print(f'notify: unknown kind {kind!r}')
        return False
    try:
        p = subprocess.run(argv, input=text, cwd=cwd, capture_output=True, text=True, timeout=60, env=env)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f'notify failed: {e}')
        return False
    ok = p.returncode == 0
    print(f'notify: sent ({kind})' if ok else f'notify failed: {(p.stdout + p.stderr)[-200:]}')
    return ok

# ---------------------------------------------------------------- CV and evidence files

def insert_cv_bullet(text):
    path, heading = rpath('cv_path'), CFG['cv_section']
    with open(path, encoding='utf-8') as f:
        lines = f.read().split('\n')
    start = next((i for i, l in enumerate(lines) if l.startswith(heading)), None)
    if start is None:
        sys.exit(f'CV: section {heading!r} not found in {path}')
    last = start
    for i in range(start + 1, len(lines)):
        if lines[i].startswith('**') or lines[i].startswith('---') or lines[i].startswith('#'):
            break
        if re.match(r'^[*-] {1,3}\S', lines[i]):
            last = i
    lines.insert(last + 1, f'*   {text}')
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(lines))

def remove_cv_bullet(text):
    if not text:
        return False
    path = rpath('cv_path')
    with open(path, encoding='utf-8') as f:
        body = f.read()
    new = body.replace(f'*   {text}\n', '', 1)
    if new != body:
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(new)
        return True
    return False

def approve_candidate(state, c, text, replace=True, how='owner'):
    replaced = []
    for old_id in (c.get('updates', []) if replace else []):
        old = state['candidates'].get(old_id)
        if old and remove_cv_bullet(old.get('cv_text', '')):
            old['status'] = 'superseded'
            replaced.append(old_id)
    insert_cv_bullet(text)
    stamp = now().strftime('%d %b %Y')
    entry = [f"\n### {c['id']} (approved {stamp}, {how})", '', f'CV bullet: "{text}"', '',
             f"Data: {c['key']}, {month_name(c['base_month'])} {c['base']:,} to "
             f"{month_name(c['month'])} {c['value']:,} ({c['ratio']}x). "
             f"Source: {CFG['source'].get('label', CFG['source'].get('kind'))}."]
    entry += [f'Caveat: {cv}' for cv in c['caveats']]
    entry += [f'Decision: {d}' for d in c.get('decisions', [])]
    if replaced:
        entry.append(f"Replaces: {', '.join(replaced)}.")
    ev_path = rpath('evidence_path')
    ev = open(ev_path, encoding='utf-8').read() if os.path.exists(ev_path) else '# Achievements evidence\n'
    if '## Approved by the achievements tracker' not in ev:
        ev = ev.rstrip('\n') + '\n\n## Approved by the achievements tracker\n'
    os.makedirs(os.path.dirname(ev_path), exist_ok=True)
    with open(ev_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(ev.rstrip('\n') + '\n' + '\n'.join(entry) + '\n')
    c.update(status='approved', cv_text=text, approved_at=now().isoformat(timespec='minutes'),
             approved_how=how, docs_synced=False)
    return replaced

# ---------------------------------------------------------------- commands

def cmd_scan(args):
    state = load_state()
    today = now()
    month = args.month or month_add(today.strftime('%Y-%m'), -1)
    scan = state['scans'].setdefault(month, {'attempts': 0})
    if scan.get('done_at') and not args.force:
        print(f'scan: {month} already scanned on {scan["done_at"]}')
        return
    scan['attempts'] += 1
    scan['last_attempt_at'] = today.isoformat(timespec='minutes')
    try:
        if args.fixture:
            with open(args.fixture, encoding='utf-8') as f:
                series = json.load(f)
        else:
            series = fetch_series()
    except (Unreachable, NeedsReauth) as e:
        reason = 'needs_reauth' if isinstance(e, NeedsReauth) else 'unreachable'
        scan['last_error'] = reason
        print(f'scan: source {reason}; will retry on the next run ({str(e)[:120]})')
        if today.day >= UNREACHABLE_ALERT_DAY and not scan.get('alerted') and not args.dry_run:
            hint = CFG.get('unreachable_hint', {}).get(reason, 'Check the data connection, then ask Claude to run the scan.')
            scan['alerted'] = notify(f'CV tracker: I could not read the numbers for {month_name(month)} yet. {hint}')
        if not args.dry_run:
            save_state(state)
        return

    findings = detect(series, month)
    changed = []
    for f in findings:
        f['decisions'] = owner_decisions(f['base_month'], month, f['key'])
        f['bullet'] = draft_bullet(f)
        existing = [c for c in state['candidates'].values() if c['key'] == f['key']]
        approved = [c for c in existing if c['status'] == 'approved']
        pending = [c for c in existing if c['status'] == 'pending']
        rejected = [c for c in existing if c['status'] == 'rejected']
        if approved and f['value'] < max(c['value'] for c in approved) * REFRESH_RATIO:
            continue
        if rejected and f['value'] < max(c['value'] for c in rejected) * REFRESH_RATIO:
            continue
        if pending:
            c = pending[0]
            if c['value'] == f['value'] and c['month'] == f['month']:
                continue
            c.update(f, updated_at=today.isoformat(timespec='minutes'))
        else:
            cid = f"ACH-{state['next_seq']:04d}"
            state['next_seq'] += 1
            c = dict(f, id=cid, status='pending', created_at=today.isoformat(timespec='minutes'),
                     updates=[c2['id'] for c2 in approved])
            state['candidates'][cid] = c
        changed.append(c)

    scan.update(done_at=today.isoformat(timespec='minutes'), findings=len(findings), last_error=None)
    print(f'scan: {month}: {len(findings)} win(s) found, {len(changed)} new or changed')
    for c in changed:
        print(f"  {c['id']} {c['key']}: {c['bullet']}")
    if args.dry_run:
        return
    save_state(state)
    if changed:
        due = (today + timedelta(days=GRACE_DAYS)).strftime('%a %d %b')
        lines = [f'CV tracker: {len(changed)} new win(s) from {month_name(month)}. '
                 f'They go into your CV on {due} unless you say otherwise.', '']
        for c in changed:
            lines.append(f"{c['id']}: {c['bullet']}")
            for cv in c['caveats']:
                lines.append(f'  Caveat: {cv}')
            if c['updates']:
                lines.append(f"  Replaces your older bullet {', '.join(c['updates'])} (newer numbers).")
            lines.append('')
        lines.append('To change one, tell Claude "reject ACH-xxxx" or give your wording. Nothing to do if they look right.')
        notify('\n'.join(lines))

def auto_approve(state, today, dry_run=False):
    """Approve every pending candidate older than GRACE_DAYS. Returns the approved ones."""
    done = []
    for c in list(state['candidates'].values()):
        if c['status'] != 'pending':
            continue
        since = datetime.fromisoformat(c.get('updated_at') or c['created_at'])
        if today - since < timedelta(days=GRACE_DAYS):
            continue
        if not dry_run:
            approve_candidate(state, c, c['bullet'], replace=True, how=f'auto, no reply in {GRACE_DAYS} days')
        done.append(c)
    return done

def cmd_auto_approve(args):
    state = load_state()
    done = auto_approve(state, now(), args.dry_run)
    print(f'auto-approve: {len(done)} candidate(s)' + (' (dry run)' if args.dry_run else ''))
    for c in done:
        print(f"  {c['id']}: {c['bullet']}")
    if done and not args.dry_run:
        save_state(state)
        notify(f'CV tracker: added to your CV (no reply in {GRACE_DAYS} days):\n\n'
               + '\n'.join(f"{c['id']}: {c['bullet']}" for c in done)
               + '\n\nFix the wording at your monthly CV review.')

def cmd_list(args):
    state = load_state()
    rows = [c for c in state['candidates'].values()
            if args.all or c['status'] == 'pending' or c.get('docs_synced') is False]
    if not rows:
        print('No pending candidates.' if not args.all else 'No candidates.')
    for c in rows:
        print(f"{c['id']} [{c['status']}] {c['key']} ({month_name(c['month'])})\n  {c['bullet']}")
        for cv in c['caveats']:
            print(f'  Caveat: {cv}')
        for d in c.get('decisions', []):
            print(f'  Decision: {d}')
    last = sorted(state['scans'].items())[-1:] if state['scans'] else []
    for m, s in last:
        print(f"Last scan {m}: " + (f"done {s['done_at']}" if s.get('done_at')
                                     else f"not done yet ({s.get('last_error')}, {s['attempts']} attempts)"))

def cmd_approve(args):
    state = load_state()
    c = state['candidates'].get(args.id)
    if not c:
        sys.exit(f'unknown candidate {args.id}')
    if c['status'] != 'pending':
        sys.exit(f"{args.id} is {c['status']}, not pending")
    replaced = approve_candidate(state, c, args.text or c['bullet'], not args.keep)
    save_state(state)
    print(f"approved {args.id}: added to the CV and the evidence file"
          + (f"; replaced {', '.join(replaced)}" if replaced else ''))
    print('docs_sync: pending. Update any copies of the CV (see SKILL.md), then run `synced`.')

def cmd_reject(args):
    state = load_state()
    c = state['candidates'].get(args.id)
    if not c:
        sys.exit(f'unknown candidate {args.id}')
    removed = c['status'] == 'approved' and remove_cv_bullet(c.get('cv_text', ''))
    c.update(status='rejected', reject_reason=args.reason, rejected_at=now().isoformat(timespec='minutes'))
    if removed:
        c['docs_synced'] = False  # copies of the CV still show the bullet until the next sync
    save_state(state)
    print(f'rejected {args.id}' + ('; removed its bullet from the CV' if removed else ''))

def cmd_synced(args):
    state = load_state()
    n = 0
    for c in state['candidates'].values():
        if c['status'] in ('approved', 'rejected') and c.get('docs_synced') is False:
            c['docs_synced'] = True
            n += 1
    save_state(state)
    print(f'marked {n} change(s) as synced to the CV copies')

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('scan')
    s.add_argument('--month', help='closed month to scan, YYYY-MM (default: last month)')
    s.add_argument('--force', action='store_true', help='rescan a month that is already done')
    s.add_argument('--dry-run', action='store_true', help='print findings; no state write, no message')
    s.add_argument('--fixture', help='read the series from a JSON file instead of the source (tests)')
    s.set_defaults(fn=cmd_scan)
    lst = sub.add_parser('list')
    lst.add_argument('--all', action='store_true')
    lst.set_defaults(fn=cmd_list)
    a = sub.add_parser('approve')
    a.add_argument('id')
    a.add_argument('--text', help="the owner's wording for the bullet (default: the draft)")
    a.add_argument('--keep', action='store_true',
                   help='keep the older approved bullet(s) this one updates (default: replace them)')
    a.set_defaults(fn=cmd_approve)
    r = sub.add_parser('reject')
    r.add_argument('id')
    r.add_argument('--reason', default='')
    r.set_defaults(fn=cmd_reject)
    sub.add_parser('synced').set_defaults(fn=cmd_synced)
    aa = sub.add_parser('auto-approve', help='approve pending candidates older than grace_days')
    aa.add_argument('--dry-run', action='store_true')
    aa.set_defaults(fn=cmd_auto_approve)
    args = ap.parse_args()
    args.fn(args)

if __name__ == '__main__':
    main()
