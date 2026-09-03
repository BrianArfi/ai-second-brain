#!/usr/bin/env python3
"""work_hours.py: reconstruct the owner's working hours per day from digital traces.

Sources (all local, no network except optional Google Calendar):
  - Claude Code session transcripts (~/.claude/projects/*/):
      entrypoint claude-vscode/cli  -> interactive AI workstream (counts)
      entrypoint sdk-cli            -> automation (cron digests etc, excluded;
                                       counted separately as automated_runs)
      subagent files under <session-uuid>/ inherit the parent session.
  - Antigravity conversations (~/.gemini/antigravity-cli/conversations/*.db):
      one SQLite file per conversation, opened READ-ONLY. See the "Antigravity
      reader" section below for what this source can and cannot tell us.
  - Meetings: journal/fathom_registry.json (actually recorded) merged with
      Google Calendar (gcal_manager.py --profile work, cached per day).
  - Git commits (this repo + any repo in WORK_HOURS_GIT_REPOS) as markers.

Every stream carries a `runtime` tag ('claude-code' | 'antigravity' | 'meetings')
and each day carries a `runtimes` breakdown, so the Hours tab can say WHICH
runtimes it counted instead of blending them into one unattributed number.

Model:
  - A workday runs 04:00 WIB -> 04:00 WIB next day (late-night work counts to
    the day it started on).
  - Each interactive session = one stream. Event timestamps are clustered into
    blocks: a gap > GAP_MIN minutes splits a block.
  - actual_h    = union of all blocks (meetings + sessions)  -> "jam kerja beneran"
  - effective_h = sum of per-stream hours (parallel counted N times)
  - leverage    = effective / actual  -> the productivity multiplier
  - attention_h = union of human-typed message clusters + meetings -> hands-on floor

Antigravity reader: what it gives us, and what it does NOT:
  MEASURED, same quality as the Claude reader:
    - per-step wall-clock timestamps (protobuf Timestamp, second precision), so
      activity minutes and block clustering are exact
    - user-turn timestamps (the step type that carries a typed prompt), so
      attention_h is real for this runtime too
  DEGRADED, and labelled as such in `runtime_caveats`:
    - NO model attribution. The conversation store records no model id anywhere,
      so an Antigravity hour cannot be split by model the way a Claude one can.
    - Interactive-vs-automation is INFERRED, not recorded. Claude Code stamps a
      real `entrypoint` field; Antigravity has no equivalent, and the summary
      db's `last_user_input_time` is unpopulated in every row on this machine.
      We fall back to counting user turns: a conversation with a single user turn
      is a one-shot call (this is exactly the shape agy-bridge cron calls take)
      and is EXCLUDED as automation. Two or more turns means a human was in the
      loop and it counts. This deliberately errs toward undercounting, because
      an inflated productivity number is worse than a missing one.
    - Lane (work/you/other) comes from the optional summary db's workspace
      uri. That db lags reality by days, so unmatched conversations land in
      'other' rather than being guessed into a client lane.
    - The legacy IDE store (~/.gemini/antigravity/conversations/*.pb) is raw
      protobuf with no step table. It is NOT parsed; it is only counted and
      reported, because a timestamp scan over it returns noise, not minutes.

State:  journal/state/work_hours.json        (served at /api/work-hours)
Cache:  journal/state/work_hours_cache.json  (per-file parse cache, incremental)

CLI:
  work_hours.py sweep [--backfill N] [--date YYYY-MM-DD] [--no-calendar]
  work_hours.py show  [--date YYYY-MM-DD]
"""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

WIB = timezone(timedelta(hours=7))
UTC = timezone.utc
BASE_DIR = Path(__file__).resolve().parents[4]
CLAUDE_PROJECTS = Path.home() / '.claude' / 'projects'
STATE_PATH = BASE_DIR / 'journal' / 'state' / 'work_hours.json'
CACHE_PATH = BASE_DIR / 'journal' / 'state' / 'work_hours_cache.json'
FATHOM_REGISTRY = BASE_DIR / 'journal' / 'fathom_registry.json'
GCAL = BASE_DIR / '.agent' / 'skills' / 'google-calendar-connector' / 'gcal_manager.py'

BOUNDARY_HOUR = 4          # workday = 04:00 WIB -> 04:00 WIB next day
GAP_MIN = 15               # gap that splits an activity block
ATTENTION_GAP_MIN = 10     # gap that splits a hands-on (human-typed) block
ATTENTION_TAIL_MIN = 3     # reading/acting tail after the last keystroke of a block
AUTOMATED_ENTRYPOINTS = {'sdk-cli'}
KEEP_DAYS = 60             # days of history kept in state
CACHE_KEEP_DAYS = 21       # parse-cache entries pruned past this file mtime age
# 1 AI-stream hour ≈ this many hours of manual solo work. Research-calibrated
# default (deep-research 16 Jul 2026, see ../research_ai_speed_factor.md):
# blended x2-2.5 is conservative-defensible for the owner's PM mix, x3 is the
# optimistic edge (drafting x2.5-3, research x2-3, coding x1-2). Still an
# estimate, not a measurement, so override it with --ai-speed or WORK_HOURS_AI_SPEED.
AI_SPEED_DEFAULT = float(os.environ.get('WORK_HOURS_AI_SPEED', '2.5'))

def _harness_extra(key, default=None):
    """Read one key out of .agent/harness.json's `extra` dict.

    harness_config is optional on purpose: this skill ships to public users who
    may not have run detection yet, and a missing config must degrade to the
    documented defaults rather than break the sweep."""
    try:
        sys.path.insert(0, str(BASE_DIR / '.agent' / 'scripts'))
        import harness_config
        # refresh=False: the sweep runs every 20 min from cron and must never
        # shell out to the detect scripts just to read two paths.
        cfg = harness_config.load(refresh=False)
        ex = cfg.get('extra')
        val = ex.get(key) if isinstance(ex, dict) else None
        return default if val in (None, '') else val
    except Exception:
        return default

def _path_list(env_var, extra_key, defaults):
    """Resolve a list of paths: env var wins, then harness.json, then defaults.

    Accepts os.pathsep- or comma-separated strings, or a JSON list in
    harness.json. Entries are expanded (~ and $VARS) but NOT required to exist;
    callers skip missing paths themselves."""
    raw = os.environ.get(env_var) or _harness_extra(extra_key)
    if not raw:
        return [Path(p) for p in defaults]
    if isinstance(raw, (list, tuple)):
        parts = list(raw)
    else:
        parts = re.split(r'[%s,]' % re.escape(os.pathsep), str(raw))
    out = [Path(os.path.expanduser(os.path.expandvars(p.strip())))
           for p in parts if str(p).strip()]
    return out or [Path(p) for p in defaults]

# Repos scanned for commit markers. BASE_DIR is always included; the rest are
# the owner's sibling repos, kept as defaults so this machine is unchanged, but
# overridable for anyone else via WORK_HOURS_GIT_REPOS or harness.json.
GIT_REPO_DEFAULTS = [
    str(Path.home() / 'antigravity-projects' / 'You'),
    str(Path.home() / 'antigravity-projects' / 'owner-arfi-website'),
]
GIT_REPOS = [BASE_DIR] + [p for p in _path_list(
    'WORK_HOURS_GIT_REPOS', 'work_hours_git_repos', GIT_REPO_DEFAULTS)
    if p != BASE_DIR]

# Antigravity conversation stores. The CLI store is SQLite (parsed); the IDE
# store is raw protobuf (counted and reported only, see the module docstring).
AGY_CONV_DEFAULTS = [str(Path.home() / '.gemini' / 'antigravity-cli' / 'conversations')]
AGY_CONV_DIRS = _path_list('WORK_HOURS_AGY_DIRS', 'work_hours_agy_dirs', AGY_CONV_DEFAULTS)
AGY_SUMMARY_DB = Path(os.environ.get(
    'WORK_HOURS_AGY_SUMMARY',
    str(Path.home() / '.gemini' / 'antigravity-cli' / 'conversation_summaries.db')))
AGY_LEGACY_DIR = Path(os.environ.get(
    'WORK_HOURS_AGY_LEGACY',
    str(Path.home() / '.gemini' / 'antigravity' / 'conversations')))
# step_type of a user-typed turn in the Antigravity step table. Verified by
# inspection: it is the step that carries the prompt text and appears exactly
# once in every one-shot agy-bridge call.
AGY_USER_STEP = 14
# Fewer than this many user turns -> one-shot call -> automation, not desk work.
# Overridable because it is a heuristic, not a recorded fact: a future
# Antigravity build could number its step types differently, and a user who
# genuinely works interactively should be able to correct us without a patch.
try:
    AGY_MIN_USER_TURNS = max(1, int(os.environ.get('WORK_HOURS_AGY_MIN_TURNS', '2')))
except ValueError:
    AGY_MIN_USER_TURNS = 2

LANES = [
    ('meetings', 'Meetings'),
    ('work', 'Work PM'),
    ('you', 'You'),
    ('other', 'Other AI'),
]

TS_RE = re.compile(rb'"timestamp"\s*:\s*"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})')
ENT_RE = re.compile(rb'"entrypoint"\s*:\s*"([^"]+)"')
# Markers only the interactive desktop app writes (typed prompt latch, mode
# switches, session titles). A headless `sdk-cli` cron run has none of them,
# so they are what separates an app session from automation now that BOTH
# carry entrypoint 'sdk-cli'.
UI_RE = re.compile(rb'"type"\s*:\s*"(?:atis-latch|last-prompt|ai-title|mode)"')
SCAN_V = 2   # bump to invalidate every cached parse (new fields below)
CMD_RE = re.compile(r'<command-name>(/[\w:-]+)</command-name>')
TAG_RE = re.compile(r'<[^>]{1,80}>')

_MINUTE_MEMO = {}

def minute_epoch(mstr):
    """'YYYY-MM-DDTHH:MM' (UTC) -> epoch minutes."""
    v = _MINUTE_MEMO.get(mstr)
    if v is None:
        dt = datetime.strptime(mstr, '%Y-%m-%dT%H:%M').replace(tzinfo=UTC)
        v = int(dt.timestamp()) // 60
        _MINUTE_MEMO[mstr] = v
    return v

def lane_for_slug(slug):
    s = slug.lower()
    if 'product-second-brain' in s or 'work' in s:
        return 'work'
    if 'antigravity-projects-you' in s or 'owner-arfi' in s:
        return 'you'
    return 'other'

def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return default

def save_json_atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(path) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(tmp, str(path))

def clean_label(text):
    """First human message -> short stream label, or None to keep looking."""
    t = (text or '').strip()
    if not t:
        return None
    if t.startswith('<local-command-stdout>'):
        return None
    m = CMD_RE.search(t)
    if m:
        return m.group(1)
    if t.startswith('<local-command-caveat>') or t.startswith('<command-'):
        return None
    if t.startswith('<'):
        t = ' '.join(TAG_RE.sub(' ', t).split())
        if not t:
            return None
    t = ' '.join(t.split())
    if len(t) > 90:
        t = t[:88] + '…'
    return t or None

def scan_file(path, cached):
    """Parse one transcript JSONL (incremental via byte offset).

    Returns cache entry:
      {mtime, size, offset, ent, title, first_human, act: [min...], hum: [min...]}
    """
    st = os.stat(path)
    if cached and cached.get('v') != SCAN_V:
        cached = None   # parsed by an older reader, missing fields -> full rescan
    if cached and cached.get('mtime') == st.st_mtime and cached.get('size') == st.st_size:
        return cached
    # incremental resume ONLY when the file grew (append-only pattern); a changed
    # mtime at the same/smaller size means a rewrite -> full rescan
    if (cached and st.st_size > cached.get('size', 0) and cached.get('offset')
            and cached['offset'] <= st.st_size):
        start = cached['offset']
        act = set(cached.get('act') or [])
        hum = set(cached.get('hum') or [])
        ent = cached.get('ent')
        title = cached.get('title')
        first_human = cached.get('first_human')
        ui = bool(cached.get('ui'))
    else:
        start = 0
        act, hum = set(), set()
        ent = title = first_human = None
        ui = False
    with open(path, 'rb') as f:
        f.seek(start)
        buf = f.read()
    last_nl = buf.rfind(b'\n')
    if last_nl == -1:
        offset, lines = start, []
    else:
        offset = start + last_nl + 1
        lines = buf[:last_nl].split(b'\n')
    for line in lines:
        if len(line) < 20:
            continue
        m = TS_RE.search(line[:600]) or TS_RE.search(line)
        if m:
            act.add(minute_epoch(m.group(1).decode()))
        if not ui and UI_RE.search(line[:200]):
            ui = True
        if ent is None:
            em = ENT_RE.search(line)
            if em:
                ent = em.group(1).decode()
        if title is None and b'"type":"ai-title"' in line[:120]:
            try:
                title = (json.loads(line).get('aiTitle') or '').strip()[:90] or None
            except Exception:
                pass
        if (first_human is None
                and (b'"type":"user"' in line or b'"type": "user"' in line)
                and b'sourceToolAssistantUUID' not in line
                and b'"isMeta":true' not in line):
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get('type') != 'user' or d.get('isMeta') or d.get('isSidechain'):
                continue
            msg = d.get('message') or {}
            c = msg.get('content')
            text = None
            if isinstance(c, str):
                text = c
            elif isinstance(c, list):
                kinds = {b.get('type') for b in c if isinstance(b, dict)}
                if kinds and kinds <= {'text', 'image'}:
                    text = ' '.join(b.get('text', '') for b in c
                                    if isinstance(b, dict) and b.get('type') == 'text')
            if text is not None:
                if m:
                    hum.add(minute_epoch(m.group(1).decode()))
                first_human = clean_label(text)  # None -> keep looking on later lines
        elif (b'sourceToolAssistantUUID' not in line
                and (b'"type":"user"' in line or b'"type": "user"' in line)
                and b'"isMeta":true' not in line and b'"isSidechain":true' not in line):
            # human-typed lines after the first: cheap check, full parse only on candidates
            if b'"content":"' in line or b'"content": "' in line or b'"content":[{"type":"text"' in line:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get('type') != 'user' or d.get('isMeta') or d.get('isSidechain'):
                    continue
                c = (d.get('message') or {}).get('content')
                ok = isinstance(c, str)
                if not ok and isinstance(c, list):
                    kinds = {b.get('type') for b in c if isinstance(b, dict)}
                    ok = bool(kinds) and kinds <= {'text', 'image'}
                if ok and m:
                    hum.add(minute_epoch(m.group(1).decode()))
    return {'v': SCAN_V, 'mtime': st.st_mtime, 'size': st.st_size, 'offset': offset,
            'ent': ent, 'title': title, 'first_human': first_human, 'ui': ui,
            'act': sorted(act), 'hum': sorted(hum)}

def is_automated(entry):
    if entry.get('runtime') == 'antigravity':
        # Antigravity records no entrypoint, so this is INFERRED from user-turn
        # count, not read off the session. See the module docstring: one-shot
        # conversations are the exact shape of an agy-bridge cron call, and
        # excluding them undercounts rather than inflating.
        return (entry.get('turns') or 0) < AGY_MIN_USER_TURNS
    ent = entry.get('ent')
    if ent in AUTOMATED_ENTRYPOINTS:
        # 'sdk-cli' used to mean cron only. The AI Second Brain desktop app
        # drives Claude Code through the same SDK entrypoint, so every
        # interactive session on macOS is stamped 'sdk-cli' too, and the old
        # rule silently dropped all of them (0 sessions, leverage 1.0x, from
        # 9 Aug 2026). Two signals rescue an app session: the interactive UI
        # markers, and more than one human-typed minute. A cron run is a
        # single injected prompt with no UI records, so it stays excluded.
        return not (entry.get('ui') and len(entry.get('hum') or ()) >= 2)
    if ent:
        return False
    label = entry.get('label') or ''
    return label.startswith('Work in /home')

def collect_sessions(window_start_min, cache):
    """Scan all Claude project dirs; returns {sid: session-entry}."""
    sessions = {}
    if not CLAUDE_PROJECTS.is_dir():
        return sessions
    wstart_s = window_start_min * 60
    for proj in sorted(CLAUDE_PROJECTS.iterdir()):
        if not proj.is_dir() or proj.name.startswith('-tmp-'):
            continue
        lane = lane_for_slug(proj.name)
        for f in proj.glob('*.jsonl'):
            try:
                if f.stat().st_mtime < wstart_s:
                    continue
            except OSError:
                continue
            key = str(f)
            cached = scan_file(f, cache.get(key))
            cache[key] = cached
            sid = f.stem
            e = sessions.setdefault(sid, {'lane': lane, 'slug': proj.name, 'ent': None,
                                          'label': None, 'act': set(), 'hum': set(),
                                          'ui': False, 'runtime': 'claude-code'})
            e['ent'] = cached.get('ent') or e['ent']
            e['ui'] = e.get('ui') or bool(cached.get('ui'))
            e['label'] = cached.get('title') or cached.get('first_human') or e['label']
            e['act'].update(cached.get('act') or [])
            e['hum'].update(cached.get('hum') or [])
        # subagent transcripts live under <session-uuid>/**; they inherit the parent
        for sub in proj.iterdir():
            if not sub.is_dir() or sub.name in ('memory',):
                continue
            sid = sub.name
            parent = sessions.get(sid)
            if parent is None:
                continue  # parent had no in-window events -> subagents can't either
            for f in sub.rglob('*.jsonl'):
                try:
                    if f.stat().st_mtime < wstart_s:
                        continue
                except OSError:
                    continue
                key = str(f)
                cached = scan_file(f, cache.get(key))
                cache[key] = cached
                parent['act'].update(cached.get('act') or [])
    return sessions

# ---------- Antigravity reader ----------
# The store is one SQLite db per conversation, with a `steps` table whose
# `metadata` blob is protobuf. We do NOT link a generated protobuf schema (it is
# a vendor-internal format that changes without notice); we scan the small
# metadata blob for embedded Timestamp seconds, which is the one field whose
# wire encoding is stable and self-validating via a plausible-date range check.

def _varint(buf, i):
    """Decode a protobuf varint at buf[i:]; returns (value, next_index)."""
    val = shift = 0
    while i < len(buf):
        b = buf[i]
        val |= (b & 0x7F) << shift
        i += 1
        shift += 7
        if not b & 0x80:
            return val, i
        if shift > 63:
            break
    return None, i

def _blob_stamps(blob, lo, hi):
    """Epoch seconds embedded in a protobuf blob, bounded to a plausible range.

    A protobuf Timestamp is `field 1 = seconds`, tag byte 0x08. Scanning for
    that tag over a ~200 byte metadata blob and rejecting anything outside
    [lo, hi] is what keeps stray varints (token counts, indexes, nanos) out."""
    out = set()
    if not blob:
        return out
    for i in range(len(blob) - 1):
        if blob[i] == 0x08:
            val, _ = _varint(blob, i + 1)
            if val is not None and lo < val < hi:
                out.add(val)
    return out

def agy_summary_index():
    """{conversation_id: {'ws': workspace_uri, 'label': preview}} from the
    optional summary db.

    Enrichment only. The db lags the live conversation files by days, so a miss
    is normal and must not cost us the conversation itself."""
    idx = {}
    if not AGY_SUMMARY_DB.is_file():
        return idx
    con = _sqlite_ro(AGY_SUMMARY_DB)
    if con is None:
        return idx
    try:
        rows = con.execute('select conversation_id, workspace_uris, preview '
                           'from conversation_summaries')
        for cid, ws, preview in rows:
            idx[cid] = {'ws': ws or '', 'label': (preview or '').strip()}
    except sqlite3.Error:
        pass
    finally:
        con.close()
    return idx

def _sqlite_ro(path):
    """Open a SQLite file strictly read-only, or return None.

    mode=ro matters for correctness, not just hygiene: these databases belong to
    a possibly-running Antigravity process and carry live WAL files. Opening
    read-write would let us checkpoint another process's WAL mid-write. A file
    we cannot open read-only is skipped and reported, never copied or forced."""
    try:
        con = sqlite3.connect('file:%s?mode=ro' % path, uri=True, timeout=2)
        con.execute('pragma query_only = 1')
        return con
    except sqlite3.Error:
        return None

def scan_agy_db(path, cached):
    """Parse one Antigravity conversation db.

    Returns a cache entry {mtime, size, kind, turns, act, hum} or None when the
    file cannot be read. Unlike the Claude JSONL reader there is no byte-offset
    resume: SQLite is not append-parseable, so a changed file is fully rescanned.
    Conversations are small (tens to a few thousand steps) so this is cheap."""
    st = os.stat(path)
    if (cached and cached.get('kind') == 'agy'
            and cached.get('mtime') == st.st_mtime and cached.get('size') == st.st_size):
        return cached
    con = _sqlite_ro(path)
    if con is None:
        return None
    now = int(datetime.now(UTC).timestamp())
    lo, hi = now - 5 * 365 * 86400, now + 86400
    act, hum = set(), set()
    turns = 0
    try:
        for step_type, meta in con.execute('select step_type, metadata from steps'):
            stamps = _blob_stamps(meta, lo, hi)
            if not stamps:
                continue
            act.update(s // 60 for s in stamps)
            if step_type == AGY_USER_STEP:
                turns += 1
                # the FIRST stamp on a user step is when the turn was submitted;
                # later ones are completion times and are not keystrokes
                hum.add(min(stamps) // 60)
    except sqlite3.Error:
        con.close()
        return None
    con.close()
    return {'mtime': st.st_mtime, 'size': st.st_size, 'kind': 'agy', 'turns': turns,
            'act': sorted(act), 'hum': sorted(hum)}

def collect_agy_sessions(window_start_min, cache, stats):
    """Scan the Antigravity conversation stores; returns {sid: session-entry}.

    `stats` is mutated with counts the Methodology card needs: how many
    conversations were read, skipped as one-shot automation, or unreadable."""
    sessions = {}
    for k in ('seen', 'unreadable', 'legacy_pb', 'one_shot', 'multi_turn'):
        stats.setdefault(k, 0)
    stats.setdefault('dirs', [])
    summary = agy_summary_index()
    wstart_s = window_start_min * 60
    for conv_dir in AGY_CONV_DIRS:
        if not conv_dir.is_dir():
            continue
        stats['dirs'].append(str(conv_dir))
        for f in sorted(conv_dir.glob('*.db')):
            try:
                if f.stat().st_mtime < wstart_s:
                    continue
            except OSError:
                continue
            key = str(f)
            cached = scan_agy_db(f, cache.get(key))
            if cached is None:
                stats['unreadable'] += 1
                continue
            cache[key] = cached
            stats['seen'] += 1
            if (cached.get('turns') or 0) >= AGY_MIN_USER_TURNS:
                stats['multi_turn'] += 1
            else:
                stats['one_shot'] += 1
            cid = f.stem
            meta = summary.get(cid) or {}
            # workspace uris are slash-separated; lane_for_slug expects the
            # dash-separated shape Claude uses for its project dirs
            ws = meta.get('ws') or ''
            lane = lane_for_slug(re.sub(r'[^a-z0-9]+', '-', ws.lower())) if ws else 'other'
            label = meta.get('label') or ''
            label = clean_label(label) or 'Antigravity conversation'
            sessions['agy:' + cid] = {
                'lane': lane, 'slug': meta.get('ws') or '', 'ent': None,
                'label': label, 'runtime': 'antigravity',
                'turns': cached.get('turns') or 0,
                'act': set(cached.get('act') or []),
                'hum': set(cached.get('hum') or []),
            }
    # legacy IDE store: counted so the UI can say it exists, never parsed
    try:
        stats['legacy_pb'] = len(list(AGY_LEGACY_DIR.glob('*.pb')))
    except OSError:
        stats['legacy_pb'] = 0
    return sessions

def blocks_from_minutes(mins, gap):
    """Sorted epoch-minute ints -> [(start_min, end_min_exclusive), ...]."""
    out = []
    mins = sorted(mins)
    if not mins:
        return out
    s = p = mins[0]
    for m in mins[1:]:
        if m - p > gap:
            out.append((s, p + 1))
            s = m
        p = m
    out.append((s, p + 1))
    return out

def parse_duration_min(s):
    if not s:
        return 0
    s = str(s)
    h = re.search(r'(\d+)\s*h', s)
    m = re.search(r'(\d+)\s*min', s)
    tot = (int(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0)
    if tot == 0:
        n = re.search(r'(\d+)', s)
        tot = int(n.group(1)) if n else 0
    return tot

def norm_title(t):
    return re.sub(r'[^a-z0-9]+', ' ', (t or '').lower()).strip()

def fetch_gcal_days(days_back):
    """One gcal_manager call -> {YYYY-MM-DD (WIB): [event, ...]}. Raises on failure."""
    cmd = [sys.executable, str(GCAL), 'list', '--profile', 'work', '--json',
           '--days-back', str(days_back + 1), '--days-forward', '1']
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=90, cwd=str(BASE_DIR))
    out = r.stdout
    i = out.find('[')
    if r.returncode != 0 or i == -1:
        raise RuntimeError(f'gcal_manager failed rc={r.returncode}: {out[:200]} {r.stderr[:200]}')
    events = json.loads(out[i:])
    by_day = {}
    for ev in events:
        start = ev.get('start') or ''
        end = ev.get('end') or ''
        if 'T' not in start or 'T' not in end:
            continue  # all-day
        try:
            sdt = datetime.fromisoformat(start).astimezone(WIB)
            edt = datetime.fromisoformat(end).astimezone(WIB)
        except ValueError:
            continue
        att = ev.get('attendees') or []
        mine = next((a for a in att if a.get('self')), None)
        if mine is None:
            mine = next((a for a in att if 'owner' in (a.get('email') or '').lower()), None)
        resp = (mine or {}).get('responseStatus') or 'none'
        if resp == 'declined':
            continue
        if len(att) < 2 and not ev.get('hangoutLink'):
            continue  # solo focus block, not a meeting (desk work is tracked via sessions)
        # bucket by WORKDAY (04:00 boundary), not calendar date: a 02:00 WIB call
        # belongs to the previous day's workday or it vanishes from both days
        workday = (sdt - timedelta(hours=BOUNDARY_HOUR)).strftime('%Y-%m-%d')
        by_day.setdefault(workday, []).append({
            's': sdt.isoformat(timespec='minutes'),
            'e': edt.isoformat(timespec='minutes'),
            'title': ev.get('summary') or '(untitled)',
            'resp': resp,
        })
    return by_day

def registry_meetings_for_window(wstart_min, wend_min):
    """fathom_registry entries whose recording interval falls in the day window."""
    reg = load_json(FATHOM_REGISTRY, {})
    out = []
    items = reg.values() if isinstance(reg, dict) else reg
    for rec in items:
        if not isinstance(rec, dict):
            continue
        start_utc = rec.get('start_utc')
        smin = None
        if start_utc:
            try:
                sdt = datetime.fromisoformat(start_utc.replace('Z', '+00:00'))
                smin = int(sdt.timestamp()) // 60
            except ValueError:
                smin = None
        if smin is None:
            d, t = rec.get('date_wib'), rec.get('time_wib')
            if not d or not t:
                continue
            try:
                sdt = datetime.strptime(f'{d} {t}', '%Y-%m-%d %H:%M').replace(tzinfo=WIB)
                smin = int(sdt.timestamp()) // 60
            except ValueError:
                continue
        dur = max(parse_duration_min(rec.get('duration')), 1)
        if smin >= wend_min or smin + dur <= wstart_min:
            continue
        out.append({'smin': smin, 'emin': smin + dur,
                    'title': rec.get('matched_meeting') or rec.get('title') or 'Meeting',
                    'rec_id': rec.get('recording_id')})
    return out

def build_meetings(day, wstart_min, wend_min, gcal_events):
    """Merge calendar events with recordings -> meeting streams.

    Calendar gives scheduled bounds; a recording overlapping an event marks it
    'recorded' (attendance verified) and can extend its end. Recording-only
    entries count as-is. Calendar-only, non-declined events count as 'scheduled'.
    """
    meetings = []
    recs = registry_meetings_for_window(wstart_min, wend_min)
    used_recs = set()
    for ev in gcal_events or []:
        try:
            smin = int(datetime.fromisoformat(ev['s']).timestamp()) // 60
            emin = int(datetime.fromisoformat(ev['e']).timestamp()) // 60
        except (ValueError, KeyError):
            continue
        if smin >= wend_min or emin <= wstart_min:
            continue
        source = 'scheduled'
        s2, e2 = smin, emin
        for i, r in enumerate(recs):
            if i in used_recs:
                continue
            overlap = min(emin, r['emin']) - max(smin, r['smin'])
            shorter = min(emin - smin, r['emin'] - r['smin'])
            title_match = norm_title(ev.get('title')) == norm_title(r.get('title'))
            # a recording verifies THIS event only if it substantially overlaps it
            # or carries the same title near the same start, because a mere time brush
            # from an unrelated recording must not upgrade/extend the wrong event
            if (shorter > 0 and overlap >= 0.5 * shorter) or (title_match and abs(r['smin'] - smin) <= 45):
                used_recs.add(i)
                source = 'recorded'
                e2 = max(e2, r['emin'])
        meetings.append({'smin': max(s2, wstart_min), 'emin': min(e2, wend_min),
                         'title': ev.get('title') or 'Meeting', 'source': source})
    for i, r in enumerate(recs):
        if i in used_recs:
            continue
        meetings.append({'smin': max(r['smin'], wstart_min), 'emin': min(r['emin'], wend_min),
                         'title': r['title'], 'source': 'recorded'})
    # merge on TIME OVERLAP regardless of title: the owner is one person, so two meeting
    # entries overlapping in wall-clock time can never contribute duration twice
    # (double-booked calendar, or one recording spanning back-to-back events).
    # Strictly adjacent meetings (emin == next smin) stay separate.
    meetings.sort(key=lambda m: (m['smin'], m['emin']))
    merged = []
    for m in meetings:
        prev = merged[-1] if merged else None
        if prev and m['smin'] < prev['emin']:
            prev['emin'] = max(prev['emin'], m['emin'])
            if (norm_title(m['title']) != norm_title(prev['title'])
                    and m['title'] not in prev['title'] and len(prev['title']) < 120):
                prev['title'] += ' + ' + m['title']
            if m['source'] == 'recorded':
                prev['source'] = 'recorded'
        else:
            merged.append(dict(m))
    return [m for m in merged if m['emin'] > m['smin']]

def commits_for_window(wstart_dt, wend_dt):
    out = []
    for repo in GIT_REPOS:
        if not (repo / '.git').exists():
            continue
        try:
            r = subprocess.run(
                ['git', '-C', str(repo), 'log', f'--since={wstart_dt.isoformat()}',
                 f'--until={wend_dt.isoformat()}', '--pretty=%at%x09%s'],
                capture_output=True, text=True, timeout=20)
            for line in r.stdout.splitlines():
                ts, _, msg = line.partition('\t')
                if not ts.strip().isdigit():
                    continue
                if msg.startswith('chore: automated'):
                    continue
                t = datetime.fromtimestamp(int(ts), WIB)
                out.append({'t': t.strftime('%Y-%m-%dT%H:%M'),
                            'min': int(ts) // 60,
                            'repo': repo.name, 'msg': msg[:110]})
        except Exception:
            continue
    return sorted(out, key=lambda c: c['t'])

def day_window(day_str):
    """'YYYY-MM-DD' -> (wstart_min, wend_min, wstart_dt) for the 04:00 boundary."""
    d = datetime.strptime(day_str, '%Y-%m-%d').replace(tzinfo=WIB)
    start = d.replace(hour=BOUNDARY_HOUR)
    end = start + timedelta(days=1)
    return int(start.timestamp()) // 60, int(end.timestamp()) // 60, start

def fmt_min(m, day_midnight_min):
    """Epoch minute -> minutes since the day's 00:00 WIB (can exceed 1440)."""
    return m - day_midnight_min

def hours(n_min):
    return round(n_min / 60, 1)

def assemble_day(day_str, sessions, gcal_events, with_commits=True, ai_speed=AI_SPEED_DEFAULT):
    wstart, wend, wstart_dt = day_window(day_str)
    day_midnight = wstart - BOUNDARY_HOUR * 60
    # scheduled meetings that haven't happened yet must not count as worked time
    now_min = int(datetime.now(WIB).timestamp()) // 60
    meeting_end_cap = min(wend, now_min) if now_min < wend else wend

    streams = []
    union_min = set()
    attention_min = set()
    automated = 0
    lane_min = {k: 0 for k, _ in LANES}
    # per-runtime tally so the Hours tab can name its sources instead of
    # presenting one blended number of unknown provenance
    rt_min = {}

    def rt_bucket(name):
        return rt_min.setdefault(name, {'streams': 0, 'minutes': 0, 'automated_runs': 0})

    meetings = build_meetings(day_str, wstart, meeting_end_cap, gcal_events)
    for i, m in enumerate(meetings):
        dur = m['emin'] - m['smin']
        rng = range(m['smin'], m['emin'])
        union_min.update(rng)
        attention_min.update(rng)
        lane_min['meetings'] += dur
        b = rt_bucket('meetings')
        b['streams'] += 1
        b['minutes'] += dur
        streams.append({
            'id': f'meet-{day_str}-{i}', 'lane': 'meetings', 'kind': 'meeting',
            'runtime': 'meetings',
            'label': m['title'], 'source': m['source'], 'minutes': dur,
            'blocks': [[fmt_min(m['smin'], day_midnight), fmt_min(m['emin'], day_midnight)]],
        })

    for sid, e in sorted(sessions.items()):
        rt = e.get('runtime') or 'claude-code'
        act = {m for m in e['act'] if wstart <= m < wend}
        if not act:
            continue
        if is_automated(e):
            automated += 1
            rt_bucket(rt)['automated_runs'] += 1
            continue
        blocks = blocks_from_minutes(act, GAP_MIN)
        dur = sum(b[1] - b[0] for b in blocks)
        for b in blocks:
            union_min.update(range(b[0], b[1]))
        hum = {m for m in e['hum'] if wstart <= m < wend}
        for b in blocks_from_minutes(hum, ATTENTION_GAP_MIN):
            attention_min.update(range(b[0], b[1] + ATTENTION_TAIL_MIN))
        lane_min[e['lane']] += dur
        b = rt_bucket(rt)
        b['streams'] += 1
        b['minutes'] += dur
        if rt == 'antigravity':
            sid_short, default_label = 'agy-' + sid[4:12], 'Antigravity conversation'
        else:
            sid_short, default_label = 'sess-' + sid[:8], 'Claude session'
        streams.append({
            'id': sid_short, 'lane': e['lane'], 'kind': 'ai', 'runtime': rt,
            'label': e['label'] or default_label, 'minutes': dur,
            'human_msgs': len(hum),
            'blocks': [[fmt_min(b[0], day_midnight), fmt_min(b[1], day_midnight)] for b in blocks],
        })

    if not streams:
        return None

    starts = [b[0] for s in streams for b in s['blocks']]
    ends = [b[1] for s in streams for b in s['blocks']]
    actual = len(union_min)
    effective = sum(s['minutes'] for s in streams)
    # hands-on floor: bounded by real logged activity so attention <= actual always
    attention = len(attention_min & union_min) if attention_min else 0

    def clock(m):
        return f'{(m // 60) % 24:02d}:{m % 60:02d}'

    day = {
        'date': day_str,
        'weekday': wstart_dt.strftime('%a'),
        'start_min': min(starts), 'end_min': max(ends),
        'start': clock(min(starts)), 'end': clock(max(ends)),
        'wall_h': hours(max(ends) - min(starts)),
        'actual_h': hours(actual),
        'effective_h': hours(effective),
        'attention_h': hours(attention),
        'leverage': round(effective / actual, 2) if actual else 0,
        # human-equivalent output: meetings 1:1 + AI hours x speed factor. The
        # factor is an explicit assumption (AI works faster than manual), NOT a
        # measurement, and the UI labels it as such.
        'human_equiv_h': hours(lane_min['meetings']
                               + sum(v for k, v in lane_min.items() if k != 'meetings') * ai_speed),
        'output_x': (round((lane_min['meetings']
                            + sum(v for k, v in lane_min.items() if k != 'meetings') * ai_speed)
                           / actual, 2) if actual else 0),
        'ai_speed': ai_speed,
        'meeting_h': hours(lane_min['meetings']),
        'ai_h': hours(sum(v for k, v in lane_min.items() if k != 'meetings')),
        'lane_hours': {k: hours(v) for k, v in lane_min.items()},
        'sessions': sum(1 for s in streams if s['kind'] == 'ai'),
        'meetings_count': len(meetings),
        'automated_runs': automated,
        # which runtimes produced this day's hours. Present so the Methodology
        # card can name them; a day with no Antigravity activity simply has no
        # 'antigravity' key rather than a misleading zero.
        'runtimes': {k: {'streams': v['streams'], 'hours': hours(v['minutes']),
                         'minutes': v['minutes'], 'automated_runs': v['automated_runs']}
                     for k, v in sorted(rt_min.items())},
        'streams': sorted(streams, key=lambda s: s['blocks'][0][0]),
    }
    if with_commits:
        wend_dt = wstart_dt + timedelta(days=1)
        day['commits'] = [
            {'t': c['t'], 'min': fmt_min(c['min'], day_midnight), 'repo': c['repo'], 'msg': c['msg']}
            for c in commits_for_window(wstart_dt, wend_dt)
        ]
    return day

def today_workday():
    now = datetime.now(WIB) - timedelta(hours=BOUNDARY_HOUR)
    return now.strftime('%Y-%m-%d')

def describe_sources(agy_stats):
    """Provenance for the Hours tab's 'Methodology & sources' card.

    Every runtime declares separately what it MEASURED and what it only
    INFERRED, so the UI is never forced to render a degraded number as though it
    were a measured one. Same discipline as the AI-speed factor, which the card
    already labels 'assumed'."""
    claude_present = CLAUDE_PROJECTS.is_dir()
    agy_dirs = agy_stats.get('dirs') or []
    agy_present = bool(agy_dirs) and agy_stats.get('seen', 0) > 0

    agy_caveats = []
    if agy_present:
        agy_caveats.append(
            'interactive vs automation is inferred from user-turn count '
            '(< %d turns = one-shot call, excluded), not from a recorded '
            'entrypoint flag as on Claude Code' % AGY_MIN_USER_TURNS)
        agy_caveats.append(
            'no model attribution: the Antigravity conversation store records '
            'no model id, so these hours cannot be split by model')
        if not AGY_SUMMARY_DB.is_file():
            agy_caveats.append(
                'no conversation summary db, so every conversation lands in the '
                '"other" lane and carries a generic label')
        if not agy_stats.get('multi_turn'):
            # The honest reading of "we saw work but counted none of it": either
            # this machine really only runs headless one-shot calls (true on
            # the owner's), or the user-turn heuristic does not fit this Antigravity
            # build. Say so instead of rendering a confident zero.
            agy_caveats.append(
                'all %d conversation(s) looked like one-shot calls, so zero '
                'Antigravity hours were counted. If you use Antigravity '
                'interactively and expected hours here, the user-turn heuristic '
                'may not match your build: set WORK_HOURS_AGY_MIN_TURNS=1 to '
                'count every conversation' % agy_stats.get('seen', 0))
    if agy_stats.get('unreadable'):
        agy_caveats.append('%d conversation file(s) could not be opened read-only '
                           'and were skipped' % agy_stats['unreadable'])
    if agy_stats.get('legacy_pb'):
        agy_caveats.append('%d legacy protobuf conversation(s) in %s are not '
                           'parseable and are excluded entirely'
                           % (agy_stats['legacy_pb'], AGY_LEGACY_DIR))
    if not agy_dirs:
        agy_caveats.append('no Antigravity conversation store found; '
                           'no Antigravity hours counted')
    elif not agy_stats.get('seen') and not agy_stats.get('unreadable'):
        # dir exists but held zero .db files in this window: a silent zero here
        # would look like a clean "no Antigravity work happened" reading rather
        # than "we found nothing to read", so say so explicitly.
        agy_caveats.append(
            'Antigravity conversation store at %s exists but contains no '
            'conversation database files; no Antigravity hours counted'
            % ', '.join(agy_dirs))

    return {
        'claude-code': {
            'present': claude_present,
            'paths': [str(CLAUDE_PROJECTS)],
            'measured': ['activity timestamps', 'human-typed message timestamps',
                         'entrypoint (interactive vs sdk automation)'],
            'inferred': [],
            'unavailable': [],
            'caveats': ([] if claude_present else
                        ['no %s on this machine; no Claude hours counted'
                         % CLAUDE_PROJECTS]),
        },
        'antigravity': {
            'present': agy_present,
            'paths': agy_dirs,
            'conversations_scanned': agy_stats.get('seen', 0),
            'one_shot_excluded': agy_stats.get('one_shot', 0),
            'interactive_counted': agy_stats.get('multi_turn', 0),
            'measured': ['activity timestamps', 'user-turn timestamps'],
            'inferred': ['interactive vs automation', 'lane / client attribution'],
            'unavailable': ['model attribution'],
            'caveats': agy_caveats,
        },
    }

def cmd_sweep(args):
    t0 = datetime.now(WIB)
    if args.date:
        days = [args.date]
    else:
        base = datetime.strptime(today_workday(), '%Y-%m-%d')
        days = [(base - timedelta(days=i)).strftime('%Y-%m-%d')
                for i in range(max(args.backfill, 1))]
    days.sort()

    state = load_json(STATE_PATH, {})
    state.setdefault('days', {})
    state.setdefault('gcal_cache', {})
    cache = load_json(CACHE_PATH, {})

    if not args.no_calendar:
        try:
            back = (datetime.now(WIB).date() - datetime.strptime(days[0], '%Y-%m-%d').date()).days
            fetched = fetch_gcal_days(back)
            blanked = []
            for d in days:
                got = fetched.get(d, [])
                had = state['gcal_cache'].get(d) or []
                # A day that HAD events and comes back with none is a fetch
                # defect far more often than a cleared calendar: the API pages
                # at 250 events, so a wide window used to return the recent
                # days empty and this line cached the emptiness over real data
                # (9 Aug - 3 Sep 2026). Keep what we had and say so.
                if had and not got:
                    blanked.append(d)
                    continue
                state['gcal_cache'][d] = got
            state['calendar_ok'] = True
            state.pop('calendar_error', None)
            if blanked:
                state['calendar_blanked_days'] = blanked
                print('[work-hours] WARNING: calendar returned 0 events for %d day(s) that '
                      'had some (%s); kept the cached events. Check gcal_manager paging.'
                      % (len(blanked), ', '.join(blanked[:5])), file=sys.stderr)
            else:
                state.pop('calendar_blanked_days', None)
        except Exception as ex:
            state['calendar_ok'] = False
            state['calendar_error'] = str(ex)[:200]
            print(f'[work-hours] calendar fetch failed, using cache: {ex}', file=sys.stderr)

    wstart_min, _, _ = day_window(days[0])
    sessions = collect_sessions(wstart_min, cache)
    agy_stats = {'dirs': [], 'seen': 0, 'unreadable': 0, 'legacy_pb': 0,
                 'one_shot': 0, 'multi_turn': 0}
    if not args.no_antigravity:
        sessions.update(collect_agy_sessions(wstart_min, cache, agy_stats))

    for d in days:
        day = assemble_day(d, sessions, state['gcal_cache'].get(d), ai_speed=args.ai_speed)
        if day:
            state['days'][d] = day
        elif d in state['days']:
            pass  # keep previously computed data rather than wiping
        if not args.quiet:
            if day:
                print(f"{d} {day['weekday']}: {day['start']}-{day['end']} "
                      f"actual {day['actual_h']}h · effective {day['effective_h']}h "
                      f"· leverage {day['leverage']}x · {day['sessions']} sessions "
                      f"+ {day['meetings_count']} meetings")
            else:
                print(f'{d}: no activity found')

    # Sanity check: transcripts exist for the window but every one of them was
    # classified as automation. That is what the 'sdk-cli' misread looked like
    # for three weeks, and it renders as a plausible chart (leverage 1.0x)
    # rather than an error, so it has to be asserted rather than eyeballed.
    counted = sum(state['days'][d].get('sessions', 0) for d in days if d in state['days'])
    interactive = sum(1 for e in sessions.values() if not is_automated(e))
    warn = None
    if sessions and interactive == 0:
        warn = ('%d session transcript(s) in the window, none counted as interactive '
                '(every one classified as automation). Check AUTOMATED_ENTRYPOINTS / '
                'is_automated against the entrypoint your Claude client writes.'
                % len(sessions))
    elif sessions and counted == 0:
        warn = ('%d session transcript(s) and %d interactive session(s) in the window, '
                'but 0 landed on any day.' % (len(sessions), interactive))
    if warn:
        state['sessions_warning'] = warn
        print('[work-hours] WARNING: ' + warn, file=sys.stderr)
    else:
        state.pop('sessions_warning', None)

    # prune
    keep = sorted(state['days'])[-KEEP_DAYS:]
    state['days'] = {k: state['days'][k] for k in keep}
    state['gcal_cache'] = {k: v for k, v in state['gcal_cache'].items() if k in keep}
    cutoff = t0.timestamp() - CACHE_KEEP_DAYS * 86400
    cache = {k: v for k, v in cache.items() if v.get('mtime', 0) >= cutoff}

    state['last_sweep_wib'] = t0.isoformat(timespec='seconds')
    state['boundary_hour'] = BOUNDARY_HOUR
    state['ai_speed_factor'] = args.ai_speed
    state['sources'] = describe_sources(agy_stats)
    state['runtimes_counted'] = sorted(k for k, v in state['sources'].items()
                                       if v.get('present'))
    state['sweep_seconds'] = round((datetime.now(WIB) - t0).total_seconds(), 1)
    save_json_atomic(CACHE_PATH, cache)
    save_json_atomic(STATE_PATH, state)
    if not args.quiet:
        print(f"swept {len(days)} day(s) in {state['sweep_seconds']}s -> {STATE_PATH}")

def cmd_show(args):
    state = load_json(STATE_PATH, {})
    d = args.date or today_workday()
    day = (state.get('days') or {}).get(d)
    if not day:
        print(f'no data for {d}, run: work_hours.py sweep --date {d}')
        return
    print(f"== {d} ({day['weekday']}) ==")
    print(f"span {day['start']} - {day['end']} ({day['wall_h']}h wall)")
    print(f"actual {day['actual_h']}h | attention {day['attention_h']}h | "
          f"effective {day['effective_h']}h | leverage {day['leverage']}x")
    if day.get('human_equiv_h') is not None:
        print(f"human-equiv {day['human_equiv_h']}h (AI x{day.get('ai_speed')}) | "
              f"productivity {day['output_x']}x vs manual solo")
    print(f"meetings {day['meeting_h']}h ({day['meetings_count']}) | ai {day['ai_h']}h "
          f"({day['sessions']} sessions) | automated runs {day['automated_runs']}")
    rts = day.get('runtimes') or {}
    if rts:
        print('runtimes: ' + ' | '.join(
            f"{k} {v['hours']}h ({v['streams']} streams, {v['automated_runs']} automated)"
            for k, v in sorted(rts.items())))
    for s in day['streams']:
        b0 = s['blocks'][0][0]
        hh, mm = (b0 // 60) % 24, b0 % 60
        rt = (s.get('runtime') or '')[:3]
        print(f"  [{s['lane']:<8}] {rt:<3} {hh:02d}:{mm:02d} {s['minutes']:>4}m  {s['label'][:66]}")
    # degradations belong on screen, not only in the JSON the dashboard reads
    for name, info in sorted((state.get('sources') or {}).items()):
        for c in info.get('caveats') or []:
            print(f"  ! {name}: {c}")

def main():
    ap = argparse.ArgumentParser(description="Reconstruct the owner's working hours from digital traces")
    sub = ap.add_subparsers(dest='command', required=True)
    sw = sub.add_parser('sweep', help='harvest + recompute day stats')
    sw.add_argument('--backfill', type=int, default=1, help='N days back from today (default 1 = today)')
    sw.add_argument('--date', help='sweep one specific day YYYY-MM-DD')
    sw.add_argument('--no-calendar', action='store_true', help='skip Google Calendar fetch')
    sw.add_argument('--no-antigravity', action='store_true',
                    help='skip the Antigravity conversation reader')
    sw.add_argument('--ai-speed', type=float, default=AI_SPEED_DEFAULT,
                    help='assumed manual-hours per AI-stream hour (default %(default)s)')
    sw.add_argument('--quiet', action='store_true')
    sh = sub.add_parser('show', help='print one day summary from state')
    sh.add_argument('--date')
    args = ap.parse_args()
    if args.command == 'sweep':
        cmd_sweep(args)
    else:
        cmd_show(args)

if __name__ == '__main__':
    main()
