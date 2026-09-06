#!/usr/bin/env python3
"""
token_usage.py - Token consumption tracker for the PSB Claude Code harness.

Answers the owner's question: how many tokens (and how much API-equivalent money)
does the harness burn, split PER TASK TYPE (morning-update, weekly-report,
subagents, workflow agents, headless ai-tasks, plain interactive...).

Design (2026-07-11):
  - Incremental sweep over the Claude Code transcript store at
    ~/.claude/projects/-home-you-antigravity-projects-product-second-brain/
    (ALL *.jsonl recursively: top-level sessions, <session>/subagents/*.jsonl,
    <session>/subagents/workflows/wf_*/*.jsonl).
  - Per file we persist {mtime, size, summary} in journal/state/token_usage.json;
    only changed files are reparsed (files are append-only, full reparse of a
    changed file is fine and keeps the code simple).
  - Every JSONL row with message.usage contributes input/output/cache-read/
    cache-write tokens under message.model. Cost uses pricing.json (extracted
    from the bundled claude-api skill - NEVER from memory). Unknown model id
    with nonzero tokens => cost is null for those rows, tracked in
    unknown_models, never guessed.
  - Costs are "API-equivalent" estimates: the owner pays a subscription, so this is
    a consumption gauge, not a bill. Real offload spend lives in agy-bridge.

Task-type classification per file:
  path .../subagents/workflows/...   -> workflow-agent
  path .../subagents/...             -> subagent
  first ~20 rows contain <command-name>X</command-name> or a user message
  starting '/X '                     -> X   (morning-update, mom, prd, ...)
  top-level file whose start matches a journal/ai_runs/*.json run window
  (+/- 3 min) and is not a VS Code session -> ai-<kind>
  everything else                    -> interactive

Subcommands:
  sweep [--full]   incremental scan + aggregate refresh + heartbeat
  report [--json]  briefing-ready markdown table per task type

State: journal/state/token_usage.json (atomic tmp+replace)
Cron (installed by the owner, on-window rule):
  50 12,18 * * * flock -n /tmp/token_tracker.lock python3 <abs>/token_usage.py sweep >> <skill>/token_tracker_cron.log 2>&1
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
SKILL_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
PRICING_PATH = os.path.join(SKILL_DIR, 'pricing.json')
STATE_PATH = os.path.join(BASE_DIR, 'journal', 'state', 'token_usage.json')
AI_RUNS_DIR = os.path.join(BASE_DIR, 'journal', 'ai_runs')
HEARTBEAT = os.path.join(BASE_DIR, '.agent', 'scripts', 'heartbeat.py')
# Claude Code names a project directory after the checkout path with every separator turned
# into a dash. This repo lives at a different absolute path on each machine, so the slug is
# derived rather than written down -- the hardcoded WSL one below stayed correct on the
# automation host and made the sweep a no-op on the Mac, where it printed "transcripts dir
# missing" and the dashboard's Usage tab has been empty ever since macOS became a daily driver.
TRANSCRIPT_ROOT = os.path.join(os.path.expanduser('~'), '.claude', 'projects')
LEGACY_WSL_SLUG = '-home-you-antigravity-projects-product-second-brain'

def transcript_dirs():
    """Every project directory on THIS machine that holds this repo's sessions.

    A list, not one path: a machine can legitimately carry both its own slug and a copy of
    another machine's history, and both are this repo's usage.
    """
    slugs = [BASE_DIR.replace(os.sep, '-'), LEGACY_WSL_SLUG]
    out = []
    for slug in slugs:
        path = os.path.join(TRANSCRIPT_ROOT, slug)
        if os.path.isdir(path) and path not in out:
            out.append(path)
    return out

WIB = timezone(timedelta(hours=7))
WINDOW_DAYS = 30
CLASSIFY_SCAN_LINES = 80        # physical lines ~ first 20 real rows + session-start hook noise
AI_TASK_JOIN_SLACK = 180        # seconds either side of an ai_runs window
AI_TASK_DEFAULT_RUN_SECS = 45 * 60   # runs missing finished_wib: assume the runner's stale cap

# Local CLI toggles are not "task types" - a session that opens with /model or
# /glm is still whatever it does next (usually interactive).
NON_TASK_COMMANDS = {
    'model', 'effort', 'glm', 'clear', 'compact', 'help', 'login', 'logout',
    'status', 'mcp', 'context', 'resume', 'config', 'memory', 'cost',
}

CMD_RE = re.compile(r'<command-name>/?([A-Za-z0-9_:-]+)</command-name>')
SLASH_RE = re.compile(r'^/([A-Za-z0-9_:-]+)(?:\s|$)')

# Keyword buckets for command-less interactive sessions (first user message,
# lowercased). First match wins; order = specificity. Keeps the owner's recurring
# ad-hoc asks visible as their own task types instead of one 'interactive' blob.
ADHOC_BUCKETS = [
    ('adhoc-harness-dev', ['dashboard', 'harness', 'cron', 'skill', 'workflow', 'agent',
                           'tracker', 'ledger', 'token']),
    ('adhoc-prd-brd',     ['prd', 'brd', 'requirement', 'spec ', 'user stor']),
    ('adhoc-roadmap',     ['roadmap', 'q3 plan', 'q4 plan', 'quarter', 'one-pager', 'onepager']),
    ('adhoc-slack-comms', ['slack', 'balas', 'bales', 'reply', 'kirim pesan', 'draft message',
                           'dm ', 'escalat']),
    ('adhoc-meeting',     ['meeting', 'mom ', 'transcript', 'fathom', 'notulen', 'minutes']),
    ('adhoc-doc-report',  ['report', 'laporan', 'gdoc', 'google doc', 'document', 'doc ']),
    ('adhoc-analysis',    ['analisa', 'analys', 'review ', 'evaluasi', 'compare', 'banding',
                           'audit', 'cek ', 'check ', 'investigasi']),
    ('adhoc-git-sync',    ['sync', 'push', 'commit', 'rebase']),
]

# ── small utils ──────────────────────────────────────────────────────────────

def _iso_epoch(ts):
    try:
        return datetime.fromisoformat(ts.replace('Z', '+00:00')).timestamp()
    except Exception:
        return None

def _wib_date(epoch):
    return datetime.fromtimestamp(epoch, WIB).strftime('%Y-%m-%d')

def _atomic_write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(obj, fh, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, path)

def heartbeat(job, status, summary):
    try:
        subprocess.run([sys.executable, HEARTBEAT, '--job', job, '--status', status,
                        '--summary', summary], capture_output=True, text=True, timeout=15)
    except Exception as e:
        print(f'  ! heartbeat failed (non-fatal): {e}', file=sys.stderr)

def load_pricing():
    with open(PRICING_PATH, encoding='utf-8') as fh:
        p = json.load(fh)
    models = p.get('models') or {}
    aliases = p.get('aliases') or {}
    resolved = dict(models)
    for alias, target in aliases.items():
        if target in models:
            resolved[alias] = models[target]
    return resolved

def load_state():
    try:
        with open(STATE_PATH, encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return {'files': {}}

# ── ai_runs join table ───────────────────────────────────────────────────────

def load_ai_runs():
    """[{kind, start, end}] windows for headless ai-task classification."""
    runs = []
    if not os.path.isdir(AI_RUNS_DIR):
        return runs
    for name in os.listdir(AI_RUNS_DIR):
        if not (name.startswith('air-') and name.endswith('.json')):
            continue
        try:
            with open(os.path.join(AI_RUNS_DIR, name), encoding='utf-8') as fh:
                m = json.load(fh)
        except Exception:
            continue
        start = m.get('started_epoch')
        if not start:
            continue
        end = _iso_epoch(m.get('finished_wib') or '') or (start + AI_TASK_DEFAULT_RUN_SECS)
        runs.append({'kind': m.get('kind') or 'unknown', 'start': float(start), 'end': float(end)})
    return runs

def match_ai_run(first_epoch, ai_runs):
    for r in ai_runs:
        if (r['start'] - AI_TASK_JOIN_SLACK) <= first_epoch <= (r['end'] + AI_TASK_JOIN_SLACK):
            return f"ai-{r['kind']}"
    return None

# ── per-file parse ───────────────────────────────────────────────────────────

def _model_cost(model, t, pricing):
    """Cost in USD for one model's token bucket, or None when model unpriced."""
    pr = pricing.get(model)
    if not pr:
        return None
    return (t['in'] * pr['input'] + t['out'] * pr['output']
            + t['cr'] * pr['cache_read'] + t['cw5'] * pr['cache_write_5m']
            + t['cw1'] * pr['cache_write_1h']) / 1e6

def parse_file(path, rel, pricing, ai_runs):
    """Stream one transcript; return the persisted summary dict."""
    is_workflow = '/subagents/workflows/' in rel.replace(os.sep, '/')
    is_subagent = '/subagents/' in rel.replace(os.sep, '/')
    task_type = 'workflow-agent' if is_workflow else ('subagent' if is_subagent else None)

    models = {}          # model -> {'in','out','cr','cw5','cw1','reqs'}
    days = {}            # WIB date -> {'tokens': int, 'cost': float}
    unknown = set()
    seen_msg_ids = set()  # one API response = N content-block rows w/ IDENTICAL usage; count once
    first_epoch = None
    last_epoch = None
    entrypoint = None
    first_user_text = None
    line_no = 0

    with open(path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            line_no += 1

            # classification scan: head of file for metadata + first user text;
            # command tags are matched over the WHOLE file (sessions often start
            # with plain chat and only later invoke /mom etc) — first match wins.
            if line_no <= CLASSIFY_SCAN_LINES:
                if entrypoint is None and '"entrypoint"' in line:
                    m = re.search(r'"entrypoint":"([^"]*)"', line)
                    if m:
                        entrypoint = m.group(1)
                if first_epoch is None and '"timestamp"' in line:
                    m = re.search(r'"timestamp":"([^"]*)"', line)
                    if m:
                        first_epoch = _iso_epoch(m.group(1))
                if first_user_text is None and '"type":"user"' in line and '"content":"' in line:
                    try:
                        row = json.loads(line)
                        content = (row.get('message') or {}).get('content')
                        text = content if isinstance(content, str) else ''
                        t = text.strip()
                        # skip harness noise (command wrappers, hook output, reminders)
                        if t and not t.startswith('<') and not t.startswith('Caveat:'):
                            first_user_text = t[:300].lower()
                    except Exception:
                        pass
            # only trust command tags in USER rows AND near the START of the
            # message content — real invocations lead with <command-message>/
            # <command-name>; embedded mentions (sessions that discuss/edit
            # this parser) sit deep inside prose/code.
            if task_type is None and '<command-name>' in line and '"type":"user"' in line:
                # candidates are rare -> full parse is cheap and precise:
                # a real invocation is a USER row whose message.content is a
                # PLAIN STRING with the tag near the start. Tool-result rows
                # (also type user, content = list) can't spoof that.
                try:
                    row = json.loads(line)
                    content = (row.get('message') or {}).get('content')
                    if isinstance(content, str):
                        ti = content.find('<command-name>')
                        if 0 <= ti < 160:
                            m = CMD_RE.search(content, ti)
                            if m and m.group(1).lower() not in NON_TASK_COMMANDS:
                                task_type = m.group(1).lower()
                except Exception:
                    pass
            elif task_type is None and line_no <= CLASSIFY_SCAN_LINES and \
                    '"type":"user"' in line and '"content":"/' in line:
                # cheap gate; confirm with a real parse
                try:
                    row = json.loads(line)
                    content = (row.get('message') or {}).get('content')
                    text = content if isinstance(content, str) else ''
                    m = SLASH_RE.match(text.strip())
                    if m and m.group(1).lower() not in NON_TASK_COMMANDS:
                        task_type = m.group(1).lower()
                except Exception:
                    pass

            # cheap skip before json.loads: only assistant rows carry usage
            if '"usage"' not in line or '"message"' not in line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            msg = row.get('message')
            if not isinstance(msg, dict):
                continue
            u = msg.get('usage')
            if not isinstance(u, dict):
                continue
            model = msg.get('model') or 'unknown'
            if model == '<synthetic>':
                continue
            # Dedupe: the transcript logs one row PER CONTENT BLOCK of the same
            # API message, each repeating the same usage object (verified
            # 2026-07-11 on real transcripts). Count each message id once.
            mid = msg.get('id')
            if mid:
                if mid in seen_msg_ids:
                    continue
                seen_msg_ids.add(mid)
            tin = int(u.get('input_tokens') or 0)
            tout = int(u.get('output_tokens') or 0)
            tcr = int(u.get('cache_read_input_tokens') or 0)
            cc = u.get('cache_creation')
            if isinstance(cc, dict):
                cw5 = int(cc.get('ephemeral_5m_input_tokens') or 0)
                cw1 = int(cc.get('ephemeral_1h_input_tokens') or 0)
            else:
                cw5 = int(u.get('cache_creation_input_tokens') or 0)
                cw1 = 0
            if not (tin or tout or tcr or cw5 or cw1):
                continue

            t = models.setdefault(model, {'in': 0, 'out': 0, 'cr': 0, 'cw5': 0, 'cw1': 0, 'reqs': 0})
            t['in'] += tin
            t['out'] += tout
            t['cr'] += tcr
            t['cw5'] += cw5
            t['cw1'] += cw1
            t['reqs'] += 1

            ep = _iso_epoch(row.get('timestamp') or '')
            if ep:
                if first_epoch is None or ep < first_epoch:
                    first_epoch = ep
                if last_epoch is None or ep > last_epoch:
                    last_epoch = ep
                d = days.setdefault(_wib_date(ep), {'tokens': 0, 'cost': 0.0})
                d['tokens'] += tin + tout + tcr + cw5 + cw1
                pr = pricing.get(model)
                if pr:
                    d['cost'] += (tin * pr['input'] + tout * pr['output'] + tcr * pr['cache_read']
                                  + cw5 * pr['cache_write_5m'] + cw1 * pr['cache_write_1h']) / 1e6
                else:
                    unknown.add(model)

    # finish classification for top-level session files
    if task_type is None:
        if models and first_epoch and 'vscode' not in (entrypoint or ''):
            task_type = match_ai_run(first_epoch, ai_runs)
        if task_type is None and first_user_text:
            # keyword buckets for command-less interactive sessions, so the owner's
            # recurring ad-hoc asks get their own rows instead of one big
            # 'interactive' blob. First match wins, order = specificity.
            for bucket, words in ADHOC_BUCKETS:
                if any(w in first_user_text for w in words):
                    task_type = bucket
                    break
        if task_type is None:
            task_type = 'interactive'

    cost = 0.0
    priced_any = False
    for model, t in models.items():
        c = _model_cost(model, t, pricing)
        if c is not None:
            cost += c
            priced_any = True
    totals = {
        'in': sum(t['in'] for t in models.values()),
        'out': sum(t['out'] for t in models.values()),
        'cr': sum(t['cr'] for t in models.values()),
        'cw': sum(t['cw5'] + t['cw1'] for t in models.values()),
        'reqs': sum(t['reqs'] for t in models.values()),
    }
    return {
        'task_type': task_type,
        'entrypoint': entrypoint,
        'first_epoch': first_epoch,
        'last_epoch': last_epoch,
        'models': models,
        'days': {d: {'tokens': v['tokens'], 'cost': round(v['cost'], 6)} for d, v in days.items()},
        'tokens': totals,
        'cost_usd': round(cost, 6) if priced_any else (None if unknown else 0.0),
        'unknown_models': sorted(unknown),
    }

# ── aggregate ────────────────────────────────────────────────────────────────

def _summary_in_range(s, start_str, end_str):
    """A file summary counts toward a [start_str, end_str] WIB-date window when
    any of its per-day activity keys fall inside the range. Falls back to the
    file's last_epoch WIB date for older summaries that predate the `days` map.
    Whole-file attribution (tokens/task-type/model) matches the original 30d
    semantics — the by_day rows below stay exact per day."""
    days = s.get('days') or {}
    if days:
        return any(start_str <= d <= end_str for d in days)
    last = s.get('last_epoch')
    return bool(last) and start_str <= _wib_date(last) <= end_str

def build_aggregate(files, now, start_date=None, end_date=None):
    """Aggregate over a WIB-date window. Default (no dates) = the trailing
    WINDOW_DAYS ending today, preserving the cron/sweep contract. Pass
    start_date/end_date (datetime.date) for an arbitrary custom range; the
    dashboard uses this to serve period + date-filtered views live off the
    stored per-file summaries (no transcript reparse)."""
    today = datetime.now(WIB).date()
    if end_date is None:
        end_date = today
    if start_date is None:
        start_date = end_date - timedelta(days=WINDOW_DAYS - 1)
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    start_str = start_date.strftime('%Y-%m-%d')
    end_str = end_date.strftime('%Y-%m-%d')
    window_days = (end_date - start_date).days + 1

    in_window = []
    for rel, ent in files.items():
        s = ent.get('summary') or {}
        if s.get('tokens', {}).get('reqs') and _summary_in_range(s, start_str, end_str):
            in_window.append(s)

    totals = {'sessions': len(in_window), 'input_tokens': 0, 'output_tokens': 0,
              'cache_read_tokens': 0, 'cache_write_tokens': 0, 'est_cost_usd': 0.0}
    by_type = {}
    by_model = {}
    by_day = {}
    unknown_models = set()

    for s in in_window:
        t = s['tokens']
        totals['input_tokens'] += t['in']
        totals['output_tokens'] += t['out']
        totals['cache_read_tokens'] += t['cr']
        totals['cache_write_tokens'] += t['cw']
        if s.get('cost_usd') is not None:
            totals['est_cost_usd'] += s['cost_usd']
        unknown_models.update(s.get('unknown_models') or [])

        g = by_type.setdefault(s['task_type'] or 'interactive',
                               {'runs': 0, 'in': 0, 'out': 0, 'cr': 0, 'cw': 0, 'cost': 0.0})
        g['runs'] += 1
        g['in'] += t['in']
        g['out'] += t['out']
        g['cr'] += t['cr']
        g['cw'] += t['cw']
        if s.get('cost_usd') is not None:
            g['cost'] += s['cost_usd']

        for model, mt in (s.get('models') or {}).items():
            gm = by_model.setdefault(model, {'runs': 0, 'total_tokens': 0, 'cost': 0.0, 'priced': True})
            gm['runs'] += 1
            gm['total_tokens'] += mt['in'] + mt['out'] + mt['cr'] + mt['cw5'] + mt['cw1']
            if model in unknown_models or model in (s.get('unknown_models') or []):
                gm['priced'] = False

        for d, v in (s.get('days') or {}).items():
            gd = by_day.setdefault(d, {'total_tokens': 0, 'est_cost_usd': 0.0})
            gd['total_tokens'] += v['tokens']
            gd['est_cost_usd'] += v['cost']

    # per-model cost needs the pricing table again (cheap - recompute from sums)
    pricing = load_pricing()
    for model, gm in by_model.items():
        pr = pricing.get(model)
        if pr is None:
            gm['est_cost_usd'] = None
            continue
        agg = {'in': 0, 'out': 0, 'cr': 0, 'cw5': 0, 'cw1': 0}
        for s in in_window:
            mt = (s.get('models') or {}).get(model)
            if mt:
                for k in agg:
                    agg[k] += mt[k]
        gm['est_cost_usd'] = round((agg['in'] * pr['input'] + agg['out'] * pr['output']
                                    + agg['cr'] * pr['cache_read'] + agg['cw5'] * pr['cache_write_5m']
                                    + agg['cw1'] * pr['cache_write_1h']) / 1e6, 4)

    grand_cost = totals['est_cost_usd'] or 0.0
    by_task_type = []
    for typ, g in sorted(by_type.items(), key=lambda kv: -kv[1]['cost']):
        tot_tokens = g['in'] + g['out'] + g['cr'] + g['cw']
        by_task_type.append({
            'type': typ,
            'runs': g['runs'],
            'avg_input': int(g['in'] / g['runs']),
            'avg_output': int(g['out'] / g['runs']),
            'avg_total': int(tot_tokens / g['runs']),
            'avg_cost_usd': round(g['cost'] / g['runs'], 4),
            'total_cost_usd': round(g['cost'], 2),
            'total_tokens': tot_tokens,
            'share_cost_pct': round(100.0 * g['cost'] / grand_cost, 1) if grand_cost else 0.0,
        })

    # by_day: exactly the WIB dates in [start, end], zero-filled
    day_rows = []
    for i in range(window_days - 1, -1, -1):
        d = (end_date - timedelta(days=i)).strftime('%Y-%m-%d')
        v = by_day.get(d, {'total_tokens': 0, 'est_cost_usd': 0.0})
        day_rows.append({'date': d, 'total_tokens': v['total_tokens'],
                         'est_cost_usd': round(v['est_cost_usd'], 4)})

    totals['est_cost_usd'] = round(totals['est_cost_usd'], 2)
    return {
        'window_days': window_days,
        'range_start': start_str,
        'range_end': end_str,
        'totals': totals,
        'by_task_type': by_task_type,
        'by_model': [{'model': m, 'runs': g['runs'], 'total_tokens': g['total_tokens'],
                      'est_cost_usd': g['est_cost_usd']}
                     for m, g in sorted(by_model.items(),
                                        key=lambda kv: -(kv[1]['est_cost_usd'] or 0))],
        'by_day': day_rows,
        'unknown_models': sorted(unknown_models),
    }

# ── subcommands ──────────────────────────────────────────────────────────────

def _jsonl_under(dirs):
    """Every transcript file under every project dir, including the subagent trees."""
    for base in dirs:
        for root, _dirs, names in os.walk(base):
            for name in sorted(names):
                if name.endswith('.jsonl'):
                    yield os.path.join(root, name)

def cmd_sweep(args):
    t0 = time.time()
    dirs = transcript_dirs()
    if not dirs:
        print(f'no transcript dir for {BASE_DIR} under {TRANSCRIPT_ROOT}', file=sys.stderr)
        heartbeat('token-tracker', 'fail', 'transcripts dir missing')
        return 1

    pricing = load_pricing()
    pricing_mtime = os.path.getmtime(PRICING_PATH)
    state = load_state()
    old_files = state.get('files') or {}
    # pricing change invalidates stored per-file costs -> full reparse
    full = args.full or (state.get('pricing_mtime') and
                         abs(state['pricing_mtime'] - pricing_mtime) > 1e-6)
    ai_runs = load_ai_runs()

    files = {}
    scanned = reparsed = errors = 0
    for path in _jsonl_under(dirs):
            # Keys are relative to ~/.claude/projects, not to one project dir, so two dirs
            # holding a file of the same name cannot collide in the state file.
            rel = os.path.relpath(path, TRANSCRIPT_ROOT)
            try:
                st = os.stat(path)
            except OSError:
                continue
            scanned += 1
            prev = old_files.get(rel)
            if (not full and prev and prev.get('mtime') == st.st_mtime
                    and prev.get('size') == st.st_size):
                files[rel] = prev
                continue
            try:
                summary = parse_file(path, rel, pricing, ai_runs)
                files[rel] = {'mtime': st.st_mtime, 'size': st.st_size, 'summary': summary}
                reparsed += 1
            except Exception as e:
                errors += 1
                print(f'  ! parse failed {rel}: {e}', file=sys.stderr)

    now = time.time()
    aggregate = build_aggregate(files, now)
    wall = round(time.time() - t0, 2)
    new_state = {
        'last_sweep': datetime.now(WIB).isoformat(timespec='seconds'),
        'last_sweep_epoch': now,
        'sweep_seconds': wall,
        'pricing_mtime': pricing_mtime,
        'files': files,
        'aggregate': aggregate,
    }
    _atomic_write(STATE_PATH, new_state)

    tot = aggregate['totals']
    top = aggregate['by_task_type'][0] if aggregate['by_task_type'] else None
    summary = (f"{scanned} files ({reparsed} reparsed, {errors} errors) in {wall}s; "
               f"30d: {tot['sessions']} sessions, "
               f"{(tot['input_tokens'] + tot['output_tokens'] + tot['cache_read_tokens'] + tot['cache_write_tokens']) / 1e6:.1f}M tokens, "
               f"~${tot['est_cost_usd']} est"
               + (f"; top: {top['type']} ${top['total_cost_usd']}" if top else ''))
    print(f'token-tracker sweep: {summary}')
    heartbeat('token-tracker', 'ok' if errors == 0 else 'fail', summary)
    return 0

def cmd_report(args):
    state = load_state()
    agg = state.get('aggregate')
    if not agg:
        print('No token_usage.json state yet - run `token_usage.py sweep` first.')
        return 1
    if args.json:
        print(json.dumps(agg, indent=1))
        return 0

    tot = agg['totals']
    grand = (tot['input_tokens'] + tot['output_tokens']
             + tot['cache_read_tokens'] + tot['cache_write_tokens'])
    print(f"## Token usage - last {agg['window_days']}d "
          f"(swept {state.get('last_sweep', '?')}, {state.get('sweep_seconds', '?')}s)")
    print()
    print(f"Claude = API-equivalent estimate (the owner is on a subscription); "
          f"the real offload cost is in agy.")
    print()
    print(f"**Totals:** {tot['sessions']} sessions | in {tot['input_tokens']:,} | "
          f"out {tot['output_tokens']:,} | cache-read {tot['cache_read_tokens']:,} | "
          f"cache-write {tot['cache_write_tokens']:,} | all {grand:,} | "
          f"est **${tot['est_cost_usd']:,}**")
    print()
    print('| Task type | Runs | Avg in | Avg out | Avg total | Avg cost | Total cost | Share |')
    print('| :-- | --: | --: | --: | --: | --: | --: | --: |')
    for r in agg['by_task_type']:
        print(f"| {r['type']} | {r['runs']} | {r['avg_input']:,} | {r['avg_output']:,} | "
              f"{r['avg_total']:,} | ${r['avg_cost_usd']} | ${r['total_cost_usd']:,} | "
              f"{r['share_cost_pct']}% |")
    print()
    print('| Model | Runs | Total tokens | Est cost |')
    print('| :-- | --: | --: | --: |')
    for r in agg['by_model']:
        cost = f"${r['est_cost_usd']:,}" if r['est_cost_usd'] is not None else 'n/a (unpriced)'
        print(f"| {r['model']} | {r['runs']} | {r['total_tokens']:,} | {cost} |")
    if agg.get('unknown_models'):
        print()
        print(f"Unpriced model ids (cost=null rows): {', '.join(agg['unknown_models'])}")
    return 0

def main():
    ap = argparse.ArgumentParser(description='Harness token usage tracker (per task type)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sp = sub.add_parser('sweep', help='incremental transcript scan + aggregate refresh')
    sp.add_argument('--full', action='store_true', help='force reparse of every file')
    rp = sub.add_parser('report', help='markdown report per task type')
    rp.add_argument('--json', action='store_true', help='dump the aggregate block as JSON')
    args = ap.parse_args()
    if args.cmd == 'sweep':
        return cmd_sweep(args)
    if args.cmd == 'report':
        return cmd_report(args)
    return 2

if __name__ == '__main__':
    sys.exit(main())
