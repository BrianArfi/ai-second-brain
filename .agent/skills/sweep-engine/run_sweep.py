#!/usr/bin/env python3
"""
run_sweep.py - PSB sweep v2 orchestrator. Runs every adapter as its own process with its
own timeout, then hands the items to sweep_core.

  python3 .agent/skills/sweep-engine/run_sweep.py [--only a,b] [--skip a,b] [--refresh-ledger]

  --stream journal/sweep/stream   also hand over each adapter's items the moment it finishes:
                                   STREAM <adapter> <n> <survivors path> on stdout, so the
                                   caller can label and branch before slow adapters are done

Output:
  journal/sweep/latest/report.md       ranked report: needs you, FYI, filtered out (n), sources
  journal/sweep/latest/survivors.json  conversations for the model pass (labels.json)
  journal/sweep/state.json             per-conversation state (dismissals, labels, what closed)

A failed or timed-out adapter never stops the run. It is named in the report's Sources
section, so a gap is visible instead of silent.
"""

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
PY = sys.executable or 'python3'
RUN_DIR = os.path.join(BASE, '_temp', 'sweep_run')
OUT_DIR = os.path.join(BASE, 'journal', 'sweep', 'latest')
STATE = os.path.join(BASE, 'journal', 'sweep', 'state.json')
FILTER = os.path.join(HERE, 'filter.json')
LEDGER_CLI = os.path.join(BASE, '.agent', 'skills', 'slack-tracker', 'scripts', 'mention_ledger.py')

# name -> timeout seconds. jira runs after gmail so it can read the ticket keys that
# arrived only as notification mail (a mention on a ticket the owner does not watch).
PHASE1 = {'slack_ledger': 60, 'slack_threads': 420, 'gmail': 180, 'calendar': 90,
          'linear': 90, 'github': 60}
PHASE2 = {'jira': 180}

def spawn(name, timeout, extra=()):
    cmd = [PY, os.path.join(HERE, 'adapters_psb.py'), name, '--out', RUN_DIR, *extra]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            cwd=BASE), time.time() + timeout, timeout

def reap(procs, on_done=None):
    """Collect adapters in the order they finish, so on_done fires as early as possible."""
    health, procs = [], dict(procs)
    while procs:
        for name, (p, deadline, timeout) in list(procs.items()):
            if p.poll() is None and time.time() < deadline:
                continue
            del procs[name]
            if p.poll() is None:
                p.kill()
                p.communicate()
                health.append({'adapter': name, 'status': 'failed', 'items': 0,
                               'note': f'timed out after {timeout}s; its items are missing from this run'})
                continue
            out, err = p.communicate()
            hp = os.path.join(RUN_DIR, f'{name}.health.json')
            if os.path.exists(hp):
                health += json.load(open(hp, encoding='utf-8'))
                if on_done:
                    on_done(name, hp)
            else:
                tail = (err or out or '').strip().splitlines()[-1:] or ['no output']
                health.append({'adapter': name, 'status': 'failed', 'items': 0,
                               'note': f'exit {p.returncode}: {tail[0][:200]}'})
        time.sleep(1)
    return health

def stream_partial(stream_dir, state, name, health_path):
    """Run the core on one adapter's items against a copy of the state, keep only that
    adapter's conversations, and announce them. The canonical state is not touched."""
    jl = os.path.join(RUN_DIR, f'{name}.jsonl')
    out = os.path.join(stream_dir, name)
    if not os.path.exists(jl):
        print(f'STREAM {name} 0 -', flush=True)
        return
    os.makedirs(out, exist_ok=True)
    tmp_state = os.path.join(out, 'state.json')
    if os.path.exists(state):
        shutil.copyfile(state, tmp_state)
    keys = set()
    for line in open(jl, encoding='utf-8'):
        it = json.loads(line)
        keys.add(it.get('collapse_into') or it.get('conversation_key'))
    r = subprocess.run([PY, os.path.join(HERE, 'sweep_core.py'), 'run', '--items', jl, '--state', tmp_state,
                        '--filter', FILTER, '--out', out, '--health', health_path],
                       capture_output=True, text=True)
    sp = os.path.join(out, 'survivors.json')
    if r.returncode != 0 or not os.path.exists(sp):
        print(f'STREAM {name} failed {(r.stderr or r.stdout).strip()[-200:]}', flush=True)
        return
    surv = [c for c in json.load(open(sp, encoding='utf-8')) if c.get('key') in keys]
    json.dump(surv, open(sp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'STREAM {name} {len(surv)} {sp}', flush=True)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', default='')
    ap.add_argument('--skip', default='')
    ap.add_argument('--refresh-ledger', action='store_true',
                    help='run the Slack mention ledger sweep first (skipped with a note if its lock is busy)')
    ap.add_argument('--state', default=STATE)
    ap.add_argument('--out', default=OUT_DIR)
    ap.add_argument('--stream', default='',
                    help='dir: hand over each adapter as it finishes (STREAM lines on stdout)')
    a = ap.parse_args()
    only = {x for x in a.only.split(',') if x}
    skip = {x for x in a.skip.split(',') if x}
    want = lambda n: (not only or n in only) and n not in skip      # noqa: E731

    if not os.path.exists(FILTER):    # first run on a fresh copy: start from the example
        shutil.copyfile(os.path.join(HERE, 'filter.example.json'), FILTER)
    shutil.rmtree(RUN_DIR, ignore_errors=True)
    os.makedirs(RUN_DIR, exist_ok=True)
    health = []
    if a.refresh_ledger and want('slack_ledger'):
        try:
            r = subprocess.run([PY, LEDGER_CLI, 'sweep'], cwd=BASE, capture_output=True, text=True, timeout=240)
            if r.returncode != 0:
                health.append({'adapter': 'ledger_refresh', 'status': 'skipped', 'items': 0,
                               'note': (r.stderr or r.stdout).strip().splitlines()[-1][:200]})
        except subprocess.TimeoutExpired:
            health.append({'adapter': 'ledger_refresh', 'status': 'skipped', 'items': 0,
                           'note': 'ledger sweep over 240s (lock held by cron?); used the last ledger state'})

    on_done = None
    if a.stream:
        shutil.rmtree(a.stream, ignore_errors=True)
        os.makedirs(a.stream, exist_ok=True)
        on_done = lambda n, hp: stream_partial(a.stream, a.state, n, hp)      # noqa: E731
    procs = {n: spawn(n, t) for n, t in PHASE1.items() if want(n)}
    health += reap(procs, on_done)
    keys = set()
    gm = os.path.join(RUN_DIR, 'gmail.jsonl')
    if os.path.exists(gm):
        for line in open(gm, encoding='utf-8'):
            ci = json.loads(line).get('collapse_into') or ''
            if ci.startswith('jira:'):
                keys.add(ci[5:])
    procs = {n: spawn(n, t, ['--extra-keys', ','.join(sorted(keys))]) for n, t in PHASE2.items() if want(n)}
    health += reap(procs, on_done)

    hpath = os.path.join(RUN_DIR, 'all.health.json')
    json.dump(health, open(hpath, 'w', encoding='utf-8'))
    items = sorted(glob.glob(os.path.join(RUN_DIR, '*.jsonl')))
    if not items:
        print(json.dumps(health, indent=1))
        sys.exit('no adapter produced items')
    if a.stream:
        print('STREAM done', flush=True)
    r = subprocess.run([PY, os.path.join(HERE, 'sweep_core.py'), 'run', '--items', *items,
                        '--state', a.state, '--filter', FILTER, '--out', a.out, '--health', hpath],
                       capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    for h in health:
        if h['status'] != 'ok':
            print(f"!! {h['adapter']}: {h['status']} - {h.get('note', '')}")
    sys.exit(r.returncode)

if __name__ == '__main__':
    main()
