#!/usr/bin/env python3
"""
commitment_ledger.py - Outbound commitments ledger: things the owner said he'd do,
tracked from "I'll ..." until actually delivered. Clones the mention_ledger.py
3-layer pattern (see .agent/skills/slack-tracker/scripts/mention_ledger.py):

  Layer 1 (this script, pure Python, cron 3x/day): collect candidates + Fathom
           action items assigned to the owner (mechanical, high confidence) +
           mechanical thread auto-close.
  Layer 2 (GLM via agy-bridge --task harvest, `extract`): turn cheap-filtered
           Slack "I'll ..." message candidates into structured commitments.
           GLM only extracts/classifies - it never decides open/closed.
  Layer 3 (Claude, morning/evening update): `report` emits briefing-ready
           markdown, embedded verbatim.

Sources:
  1. Fathom - action items assigned to the owner in recently-synced meetings
     (journal/fathom_registry.json) -> high-confidence items, no LLM needed.
  2. Slack sent messages - search.messages from:<@BRIAN_ID> since watermark,
     cheap regex filter for commitment language -> pending_candidates (NOT
     turned into items during sweep; `extract` does that via agy-bridge).
  3. Auto-close - conversations.replies on a candidate's thread: the owner later
     posts a completion word or a Drive/Docs link -> closed_by: auto_thread.

State: journal/state/commitments.json
Auth: SLACK_USER_TOKEN from .agent/skills/slack-connector/token.env (xoxp).

Subcommands: sweep (cron default) / extract / add / close / drop / link / unlink / report
"""

import argparse
import difflib
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
STATE_PATH = os.path.join(BASE_DIR, 'journal', 'state', 'commitments.json')
TOKEN_ENV = os.path.join(BASE_DIR, '.agent', 'skills', 'slack-connector', 'token.env')
AGY_BRIDGE = os.path.join(BASE_DIR, '.agent', 'skills', 'agy-bridge', 'run.py')
FATHOM_CLIENT = os.path.join(BASE_DIR, '.agent', 'skills', 'fathom-connector', 'scripts', 'fathom_client.py')
FATHOM_REGISTRY = os.path.join(BASE_DIR, 'journal', 'fathom_registry.json')
HEARTBEAT = os.path.join(BASE_DIR, '.agent', 'scripts', 'heartbeat.py')

# Every record carries a work-tree node (CLAUDE.md, "Every Ticket Belongs To A
# Work-Tree Node"). resolve_or_die refuses an unknown id and prints the closest
# matches, so a typo can never quietly file a record under nothing.
sys.path.insert(0, os.path.join(BASE_DIR, '.agent', 'scripts'))
from work_tree import UNFILED, UnknownNode, resolve_or_die  # noqa: E402
PEOPLE_PATH = os.path.join(BASE_DIR, 'journal', 'state', 'people.json')

# Local meeting artifacts (verified against meeting-recorder/watcher.py constants
# 2026-07-11, do not guess): TRANSCRIPTS_DIR = raw local/vexa transcripts,
# MOM_DIR = where the watcher drops MOM drafts (also where /mom saves finals).
TRANSCRIPTS_DIR = os.path.join(BASE_DIR, 'Clients', 'Work', 'meetings', 'transcripts')
MOM_DIR = os.path.join(BASE_DIR, 'Clients', 'Work', 'meetings')
CLIENTS_MEETINGS_GLOB = os.path.join(BASE_DIR, 'Clients', '*', 'meetings', '*.md')

# Owner identity: override via env for your own setup (comma-separated tokens).
BRIAN_ID_DEFAULT = os.environ.get('OWNER_SLACK_ID', '<SLACK_ID>')   # verified via auth.test 2026-07-09
BRIAN_EMAIL = os.environ.get('OWNER_EMAIL', 'you@example.com')
BRIAN_NAME_TOKENS = tuple(
    t.strip().lower() for t in
    os.environ.get('OWNER_NAME_TOKENS', 'owner arfi,you').split(',')
    if t.strip()
)   # substring match, case-insensitive

FIRST_RUN_SLACK_LOOKBACK_DAYS = 3          # per spec: bounded first-run lookback
FIRST_RUN_FATHOM_LOOKBACK_DAYS = 3
FIRST_RUN_LOCAL_LOOKBACK_DAYS = 3
CLOSED_RETENTION_DAYS = 14                 # prune done/dropped after this
API_PAUSE = 0.15                           # pacing between Slack calls
FATHOM_LIST_LIMIT = 20                     # bounded: fathom --action list --full page size
MIN_CAPTURE_WORDS = 4                      # noise guard: drop sub-4-word captures

WIB_OFFSET_HOURS = 7

# Cheap mechanical filter for "this Slack message probably makes a commitment".
# Deliberately permissive (extract's LLM pass does the real judgment) but bounded
# so pending_candidates doesn't fill up with noise every sweep.
COMMIT_RE = re.compile(
    r"\b(i'?ll|i will|i'?m going to|i am going to|will send|will share|will follow up|"
    r"will get back|will update|will check|will look into|will circle back|"
    r"let me (send|share|check|get|pull|follow up|dig)|i owe you|i can (send|share|get))\b",
    re.IGNORECASE,
)

# Mechanical auto-close signal: the owner's own later message in the thread contains
# a completion word, or a Drive/Docs link (the deliverable itself).
CLOSE_WORD_RE = re.compile(
    r"\b(done|sent|shared|submitted|delivered|resolved|completed|finished|"
    r"posted|uploaded|updated the doc|here you go|attached)\b", re.IGNORECASE,
)
DRIVE_LINK_RE = re.compile(r"(docs\.google\.com|drive\.google\.com)")

# Mechanical spoken-cue detector for local transcripts/MOM drafts: the owner says one
# of these phrases mid-meeting to flag "this is my action item" -> capture
# everything after the cue to end of line. re.MULTILINE so `$` anchors per line
# when run against a whole file's text via finditer (not just end of string).
CUE_RE = re.compile(
    r"(?:ini\s+action\s+item\s+(?:gw|gue|saya)|action\s+item\s+(?:gw|gue|saya)\b|"
    r"note[:,]?\s*action\s+item|my\s+action\s+item)\s*(.*)$",
    re.IGNORECASE | re.MULTILINE,
)

# MOM "Action Items" section detector (mechanical, heading-level aware so a
# nested subheading like "### Work Team" doesn't prematurely end the section -
# only a heading at the SAME or SHALLOWER level than the Action Items heading
# closes it). Matches "## Action Items", "## 3. Action Items", "## ✅ Action
# Items", etc. - decorations before the words are stripped, not matched literally.
HEADING_RE = re.compile(r'^(#{1,6})\s+(.*)$')
ACTION_ITEMS_TEXT_RE = re.compile(r'^[\d.\s]*[^\w]*action\s+items?\b', re.IGNORECASE)
BRIAN_OWNER_RE = re.compile(r'\bBrian\b', re.IGNORECASE)
# "the owner's prioritisation" names the owner as context, not as owner. A line whose ONLY
# mention of the owner is possessive must never become the owner's commitment.
BRIAN_POSSESSIVE_RE = re.compile(r"\bBrian(?:\s+\w+)?['’]s\b", re.IGNORECASE)
# Cells that are structurally never an owner: sequence numbers, dates, priorities,
# and the dependency phrasing MOM templates put in the Due column.
_NON_OWNER_CELL_RE = re.compile(
    r'^(?:\d+|high|medium|low|tbd|n/?a|-+|eod|done|pending.*|with item.*|feeds item.*|'
    r'before .*|after .*|\d{4}-\d{2}-\d{2}.*|.*\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b.*)$',
    re.IGNORECASE)
# You personal-brand work. Work's ledger must never carry it, and the reverse
# separation is enforced in the You repo. Added 28 Jul 2026 after the Dibimbing
# PLN training MOM produced three Work commitments for a You teaching job.
# Deliberately excludes "you": that is the owner's own Slack display name ("the owner
# (You)") and fires on any Work MOM that merely mentions him. Same reason
# "linkedin", "podcast" and "second brain" are out. Only unambiguous project names
# belong here.
YOU_RE = re.compile(
    r'\b(?:goakal|bukal\s*nikah|taaruf|lalu\s*nikah|TLN|suami\s*qawwam|AI\s*Circle|'
    r'dibimbing|repliz|hyperframe)\b',
    re.IGNORECASE)

def _cell_names_brian(cell):
    c = (cell or '').strip().lower()
    return any(tok in c for tok in BRIAN_NAME_TOKENS) or c in ('owner', 'owner arfi you')

def mom_owner_cells(raw_line):
    """Split a MOM action-item row into its fields and return the cells that could
    plausibly be an owner. MOM templates emit `N | task | Owner | Due | Priority`
    (pipes) or the same shape flattened with ' - ' separators. Returns [] when the
    line has no field structure, in which case the caller falls back to a scan."""
    if '|' in raw_line:
        cells = raw_line.strip().strip('|').split('|')
    else:
        cells = re.split(r'\s+-\s+', raw_line.strip())
    cells = [re.sub(r'\*\*|\[[ xX]\]', '', c).strip(' -\t') for c in cells]
    out = []
    for c in cells:
        if not c or len(c.split()) > 5:
            continue                      # long cell is the task text, not an owner
        if _NON_OWNER_CELL_RE.match(c):
            continue
        if not re.search(r'[A-Za-z]', c):
            continue
        out.append(c)
    return out

def line_owner_is_brian(raw_line):
    """True when the MOM row actually assigns the work to the owner.

    The old test was `\\bBrian\\b` anywhere on the line, which captured other
    people's action items whenever the task text happened to mention the owner. On
    28 Jul that put Teammate Sanka's Buy Box scoping task ('sequenced against
    the owner's prioritisation - Teammate Sanka') into the owner's outbound ledger."""
    cells = mom_owner_cells(raw_line)
    if cells:
        brian_cells = [c for c in cells if _cell_names_brian(c)]
        if brian_cells:
            return True
        # A structured row with a named owner who is not the owner is not the owner's,
        # even if the task text mentions him.
        named = [c for c in cells if resolve_person_slug(c) in _load_person_lookup().values()]
        if named:
            return False
    if BRIAN_POSSESSIVE_RE.search(raw_line) and not re.search(
            r'\bBrian\b(?!(?:\s+\w+)?[\'’]s)', raw_line, re.IGNORECASE):
        return False
    return bool(BRIAN_OWNER_RE.search(raw_line))

# --------------------------------------------------------- content dedupe --
#
# Why this exists: `processed_sources` only dedupes by SOURCE key
# (fathom:<call>:<ts>, local:<path>:<hash>, manual). The same real-world
# commitment reaching the ledger through two ingestion paths gets two different
# source keys, so both insert and neither is ever matched. That is how
# COM-0151 ("Create PRD for Storefront analytics", fathom) and COM-0165 ("Write
# the complete PRD for Storefront analytics ...", manual) both sat open on
# 17 Jul 2026. Dedupe has to happen on CONTENT, not on provenance.
#
# Two layers, because they run under different constraints:
#   1. create_item() - the single chokepoint every ingest path passes through.
#      Runs inside sweep loops, so it stays deterministic and offline, and only
#      auto-merges above DUP_AUTO where a false merge is implausible.
#   2. cmd_dedupe() - batched full scan. Pairs in the uncertain band
#      [DUP_BAND, DUP_AUTO) are adjudicated by GLM via agy-bridge in ONE call.
#
# Asymmetry that drives the thresholds: a missed duplicate is noise, a false
# merge silently destroys a real commitment. So the deterministic bar is high
# and anything arguable is escalated rather than guessed.

DUP_AUTO = 0.86     # >= this: same commitment, merge without asking
# Lowered 0.55 -> 0.45 on 28 Jul 2026. Every one of the four real cross-source
# duplicates found by hand that day scored 0.27 to 0.53, so they were never even
# offered to the adjudicator and `dedupe` reported "no duplicates found" against a
# ledger that had six. Only the explicit `dedupe` command uses this band (ingest
# still gates on DUP_AUTO), so the cost is one larger adjudication call, and the
# model still has to agree before anything merges.
DUP_BAND = 0.45     # [DUP_BAND, DUP_AUTO): uncertain, send to GLM to adjudicate

_DUP_STOP = {
    'the', 'a', 'an', 'to', 'for', 'of', 'in', 'on', 'at', 'by', 'with', 'and',
    'or', 'is', 'are', 'be', 'that', 'this', 'it', 'as', 'from', 'into', 'once',
    'already', 'complete', 'completely', 'full', 'new', 'then', 'so', 'can',
    'will', 'need', 'needs', 'needed', 'before', 'after', 'via', 'per', 'up',
}

# Different sources phrase the same commitment with different verbs ("Create PRD"
# vs "Write the complete PRD"). Collapse the common ones so the verb does not
# defeat the overlap score.
_VERB_CANON = {
    'write': 'author', 'writing': 'author', 'draft': 'author', 'drafting': 'author',
    'create': 'author', 'creating': 'author', 'produce': 'author', 'prepare': 'author',
    'add': 'author', 'build': 'author',
    'send': 'send', 'share': 'send', 'deliver': 'send', 'forward': 'send',
    'confirm': 'confirm', 'verify': 'confirm', 'validate': 'confirm', 'check': 'confirm',
    'ask': 'ask', 'chase': 'ask', 'follow': 'ask', 'ping': 'ask', 'nudge': 'ask',
    'schedule': 'schedule', 'book': 'schedule', 'set': 'schedule',
    'ticket': 'ticket', 'tickets': 'ticket', 'jira': 'ticket',
    'prds': 'prd', 'docs': 'doc', 'document': 'doc',
}

# Some ingest paths append a stringified owner dict to the commitment text
# ("... - {'name': ..., 'email': ...} - Pending"); it is noise that drags
# dedupe scores below DUP_AUTO, so strip it before tokenizing/diffing.
_LEAKED_OWNER_SUFFIX_RE = re.compile(
    r"\s*-\s*\{'name':.*?\}\s*(?:-\s*\w+\s*)?$")

def _strip_leaked_suffix(text):
    return _LEAKED_OWNER_SUFFIX_RE.sub('', text or '').strip()

# The same commitment arrives from Fathom in telegraphic shorthand ("Draft BRD w/
# 4-phase MVP") and from a MOM row in full prose ("Update the BRD with the four-phase
# MVP breakdown"). Token overlap alone scored those at 0.275 and the pair was never
# even offered to the adjudicator. Normalize the two dialects onto one before scoring.
_DUP_NUMWORDS = {
    'one': '1', 'two': '2', 'three': '3', 'four': '4', 'five': '5', 'six': '6',
    'seven': '7', 'eight': '8', 'nine': '9', 'ten': '10', 'twelve': '12',
}
_DUP_ABBREV = {
    'w': 'with', 'ops': 'operations', 'op': 'operations', 'mkt': 'marketing',
    'req': 'requirement', 'reqs': 'requirement', 'doc': 'document',
    'docs': 'document', 'est': 'estimate', 'ests': 'estimate', 'mtg': 'meeting',
    'acct': 'account', 'acc': 'account', 'info': 'information', 'poc': 'poc',
    'api': 'api', 'apis': 'api', 'ticket': 'ticket', 'tickets': 'ticket',
    'supplier': 'supplier', 'suppliers': 'supplier', 'merchant': 'supplier',
    'merchants': 'supplier', 'partnership': 'partnership', 'partnerships':
    'partnership', 'deliverable': 'deliverable', 'deliverables': 'deliverable',
}
# MOM rows carry scaffolding that is pure noise for similarity: a leading sequence
# number and a trailing "- Owner - Due - Priority" tail.
_MOM_LEAD_RE = re.compile(r'^\s*\d+\s*-\s*')
_MOM_TAIL_RE = re.compile(
    r'\s*-\s*(?:[A-Z][\w.]*(?:\s+[A-Z][\w.]*){0,3})\s*-\s*.{0,40}?$')

def _normalize_for_dup(text):
    s = _strip_leaked_suffix(text or '')
    s = _MOM_LEAD_RE.sub('', s)
    s = _MOM_TAIL_RE.sub('', s)
    s = s.replace('&', ' and ').replace('/', ' ').replace('+', ' and ')
    return s

def _dup_tokens(text):
    """Normalized content-word set used for duplicate scoring."""
    words = re.findall(r"[a-z0-9]+", _normalize_for_dup(text).lower())
    out = set()
    for w in words:
        w = _DUP_NUMWORDS.get(w, w)
        w = _DUP_ABBREV.get(w, w)
        w = _VERB_CANON.get(w, w)
        if w in _DUP_STOP or len(w) < 2:
            continue
        out.add(w)
    return out

def _dup_score(a_text, b_text):
    """0..1 similarity between two commitment texts.

    Uses containment (intersection over the SMALLER set) rather than Jaccard on
    purpose: sources differ wildly in verbosity, and a terse fathom line is
    routinely a strict subset of a hand-written one. Jaccard would score that
    pair low precisely when it is most certainly a duplicate. difflib is taken
    as a floor so near-identical strings still score high when tokenization is
    unhelpful (e.g. mostly identifiers).
    """
    ta, tb = _dup_tokens(a_text), _dup_tokens(b_text)
    if not ta or not tb:
        return 0.0
    containment = len(ta & tb) / min(len(ta), len(tb))
    seq = difflib.SequenceMatcher(
        None, _strip_leaked_suffix(a_text).lower(),
        _strip_leaked_suffix(b_text).lower()).ratio()
    return max(containment, seq)

def _compatible(a, b):
    """Cheap guard before scoring: two items with explicitly DIFFERENT named
    recipients are not the same commitment, however similar the text. An empty
    recipient is unknown, not a conflict, so it stays eligible.

    Also honors `not_dup_of`: once a human has un-merged a pair, that verdict is
    permanent. Without this the scorer would just re-merge it on the next run and
    silently overturn the correction.
    """
    if b.get('id') in (a.get('not_dup_of') or []):
        return False
    if a.get('id') in (b.get('not_dup_of') or []):
        return False
    sa, sb = (a.get('to_slug') or ''), (b.get('to_slug') or '')
    if sa and sb and sa != sb:
        return False
    return True

def _richness(it):
    """How much a record carries. The richer of a duplicate pair survives."""
    score = 0
    for field in ('to', 'due', 'project', 'permalink'):
        if it.get(field):
            score += 2
    if (it.get('source') or {}).get('ref'):
        score += 1
    if it.get('confidence') == 'high':
        score += 1
    if it.get('priority'):
        score += 1
    score += min(len(it.get('text') or ''), 300) / 300.0
    return score

def find_content_dup(state, text, to='', exclude_id=None, threshold=DUP_AUTO):
    """Return an existing OPEN item that is the same commitment as `text`, else None."""
    probe = {'to_slug': resolve_person_slug(to) if to else ''}
    best, best_score = None, 0.0
    for it in state['items'].values():
        if it.get('status') != 'open' or it.get('id') == exclude_id:
            continue
        if not _compatible(probe, it):
            continue
        s = _dup_score(text, it.get('text'))
        if s > best_score:
            best, best_score = it, s
    return best if best_score >= threshold else None

def merge_items(primary, secondary):
    """Fold `secondary` into `primary` and mark it dropped.

    Absorbs every field the primary is missing, so a merge never loses data -
    the terse fathom record usually owns the permalink the rich manual record
    lacks, which is exactly the COM-0151 / COM-0165 case.
    """
    for field in ('to', 'due', 'project', 'permalink', 'channel',
                  'channel_name', 'thread_ts'):
        if not primary.get(field) and secondary.get(field):
            primary[field] = secondary[field]
    if not primary.get('to_slug') and secondary.get('to_slug'):
        primary['to_slug'] = secondary['to_slug']
    if secondary.get('priority'):
        primary['priority'] = True
    if secondary.get('confidence') == 'high':
        primary['confidence'] = 'high'
    sref = (secondary.get('source') or {}).get('ref')
    stype = (secondary.get('source') or {}).get('type')
    primary.setdefault('merged_from', [])
    primary['merged_from'].append({
        'id': secondary['id'], 'source_type': stype, 'source_ref': sref,
        'text': secondary.get('text'),
    })
    primary.setdefault('notes', []).append(
        f"merged {secondary['id']} ({stype}) into this item")
    secondary.update(status='dropped', closed_at=time.time(), closed_by='dedupe',
                     merged_into=primary['id'])
    secondary.setdefault('notes', []).append(f"duplicate of {primary['id']}")
    return primary

ADJUDICATE_BATCH = 40   # one 162-pair call came back unparseable on 28 Jul 2026

def _glm_adjudicate(pairs):
    """Ask the bridge model which borderline pairs are the same commitment.

    Batched: a single call carrying every pair returns prose or a truncated array
    once the list gets long, and the parse failure is indistinguishable from a
    clean "no duplicates", which is exactly how a ledger with six duplicates
    reported none. Each batch failing is independently survivable.

    Returns a set of pair indices judged duplicate. Honors the agy-bridge
    fallback sentinel (exit 3): on fallback or ANY failure we return an empty
    set, i.e. keep both items. Failing open would merge on a broken model call,
    and a wrong merge destroys a commitment.
    """
    if not pairs:
        return set()
    if len(pairs) > ADJUDICATE_BATCH:
        confirmed = set()
        for start in range(0, len(pairs), ADJUDICATE_BATCH):
            chunk = pairs[start:start + ADJUDICATE_BATCH]
            print('    batch {}-{} of {} ...'.format(
                start, start + len(chunk) - 1, len(pairs)))
            for idx in _glm_adjudicate(chunk):
                confirmed.add(start + idx)
        return confirmed
    lines = []
    for i, (a, b) in enumerate(pairs):
        def _prov(x):
            src = (x.get('source') or {}).get('type') or '-'
            ref = ((x.get('source') or {}).get('ref') or '')[-60:]
            seen = x.get('first_seen') or 0
            day = time.strftime('%Y-%m-%d', time.localtime(seen)) if seen else '-'
            return f"source={src} captured={day} ref={ref}"
        lines.append(
            f"[{i}]\nA ({a['id']}): {a.get('text')}\n"
            f"    to={a.get('to') or '-'} project={a.get('project') or '-'} {_prov(a)}\n"
            f"B ({b['id']}): {b.get('text')}\n"
            f"    to={b.get('to') or '-'} project={b.get('project') or '-'} {_prov(b)}")
    prompt = (
        "You are deduplicating a product manager's commitment ledger. Each pair "
        "below was captured from different sources and MAY describe the same "
        "real-world commitment worded differently, or may be two genuinely "
        "distinct commitments.\n\n"
        "Answer DUPLICATE only if a person doing one has necessarily done the "
        "other. Two different PRDs, two different tickets, or the same artifact "
        "at different stages (draft vs review vs send) are DISTINCT.\n\n"
        "IMPORTANT, the most common real duplicate looks like this: the SAME "
        "meeting is captured twice, once as a terse Fathom line and once as a "
        "verbose MOM table row. Use the source and captured date shown on each "
        "record. When two records come from different source types but the same "
        "meeting or the same day, and they name the same deliverable, that is a "
        "DUPLICATE even though the wording shares few words. Telegraphic style "
        "('Draft BRD w/ 4-phase MVP + POC; send to Teammate') and full prose "
        "('Update the BRD with the four-phase MVP breakdown and per-phase "
        "deliverables') are the same commitment, not two.\n\n"
        "Do not treat a difference in verb alone (draft vs update vs produce) as "
        "evidence of two commitments when the artifact and the meeting match.\n\n"
        "When uncertain, answer DISTINCT. A wrong merge deletes real work.\n\n"
        + "\n\n".join(lines) +
        "\n\nReturn ONLY a JSON array of the indices that are DUPLICATE, "
        'e.g. [0,3]. No prose.')
    tmp = os.path.join(tempfile.gettempdir(), f'com_dedupe_{os.getpid()}.txt')
    try:
        with open(tmp, 'w') as f:
            f.write(prompt)
        out = subprocess.run(
            [sys.executable, AGY_BRIDGE, '--task', 'harvest', '--prompt-file', tmp],
            capture_output=True, text=True, timeout=180)
        if out.returncode == 3:
            print('  ! GLM returned fallback sentinel; keeping borderline pairs '
                  'unmerged (review manually)', file=sys.stderr)
            return set()
        if out.returncode != 0:
            print(f'  ! agy-bridge failed rc={out.returncode}; keeping borderline '
                  f'pairs unmerged', file=sys.stderr)
            return set()
        m = re.search(r'\[[\d,\s]*\]', out.stdout)
        if not m:
            print('  ! could not parse GLM verdict; keeping borderline pairs '
                  'unmerged', file=sys.stderr)
            return set()
        return {int(x) for x in json.loads(m.group(0))
                if isinstance(x, int) and 0 <= int(x) < len(pairs)}
    except Exception as e:
        print(f'  ! GLM adjudication error ({e}); keeping borderline pairs '
              f'unmerged', file=sys.stderr)
        return set()
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

# ------------------------------------------------------------------- state --

def default_state():
    return {
        'next_seq': 1,
        'items': {},
        'slack_search_watermark': 0.0,
        'local_watermark': 0.0,
        'processed_fathom_ids': [],
        'processed_sources': {},        # dedupe map: source-key -> True
        'pending_candidates': [],
        'user_names': {},
        'last_sweep': None,
    }

def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            state = json.load(f)
        for k, v in default_state().items():
            state.setdefault(k, v)
        return state
    return default_state()

def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f, indent=1, ensure_ascii=False)
    os.replace(tmp, STATE_PATH)

def slugify(name):
    s = re.sub(r'[^a-z0-9]+', '-', (name or '').strip().lower()).strip('-')
    return s or 'unknown'

_PERSON_LOOKUP = None   # cache: {lowercase name/alias/first-name -> canonical slug}

def _load_person_lookup():
    """journal/state/people.json is the canonical roster (owned by the
    stakeholders component). Build a case-insensitive lookup of full name,
    every alias, and unambiguous first names (first token of `name`, only
    when it maps to exactly one roster slug). Cached per process; graceful
    (empty lookup) if people.json is missing or unparseable."""
    global _PERSON_LOOKUP
    if _PERSON_LOOKUP is not None:
        return _PERSON_LOOKUP
    lookup = {}
    people = {}
    if os.path.exists(PEOPLE_PATH):
        try:
            with open(PEOPLE_PATH, encoding='utf-8') as f:
                data = json.load(f)
            people = data.get('people', {}) if isinstance(data, dict) else {}
        except Exception:
            people = {}
    first_name_owners = {}
    for slug, person in people.items():
        name = (person.get('name') or '').strip()
        if name:
            lookup.setdefault(name.lower(), slug)
            first = name.split()[0].lower()
            first_name_owners.setdefault(first, set()).add(slug)
        for alias in person.get('aliases') or []:
            alias = (alias or '').strip()
            if alias:
                lookup.setdefault(alias.lower(), slug)
    for first, slugs in first_name_owners.items():
        if len(slugs) == 1:
            lookup.setdefault(first, next(iter(slugs)))
    _PERSON_LOOKUP = lookup
    return lookup

def resolve_person_slug(name):
    """Resolve a captured name against the people.json roster (full name /
    alias / unambiguous first name, case-insensitive) -> canonical slug.
    Falls back to bare slugify() on a miss or a missing roster."""
    n = (name or '').strip()
    if not n:
        return slugify(name)
    return _load_person_lookup().get(n.lower()) or slugify(name)

def next_id(state):
    seq = state.get('next_seq', 1)
    state['next_seq'] = seq + 1
    return f'COM-{seq:04d}'

def wib_today():
    return (datetime.now(timezone.utc) + timedelta(hours=WIB_OFFSET_HOURS)).date()

# ---------------------------------------------------------------- Slack API --

def load_token():
    tok = os.environ.get('SLACK_USER_TOKEN')
    if not tok and os.path.exists(TOKEN_ENV):
        for line in open(TOKEN_ENV):
            line = line.strip()
            if line.startswith('SLACK_USER_TOKEN='):
                tok = line.split('=', 1)[1].strip().strip('"').strip("'")
    if not tok:
        sys.exit('FATAL: SLACK_USER_TOKEN not found (env or slack-connector/token.env)')
    return tok

def slack(method, token, params=None, retries=3):
    params = params or {}
    url = f'https://slack.com/api/{method}?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
    for attempt in range(retries):
        try:
            resp = json.load(urllib.request.urlopen(req, timeout=30))
        except Exception as e:
            if attempt == retries - 1:
                return {'ok': False, 'error': f'request_failed: {e}'}
            time.sleep(2 * (attempt + 1))
            continue
        if resp.get('ok'):
            return resp
        if resp.get('error') == 'ratelimited':
            time.sleep(int(resp.get('retry_after', 10)) if isinstance(resp.get('retry_after'), (int, str)) else 10)
            continue
        return resp
    return {'ok': False, 'error': 'retries_exhausted'}

def thread_ts_from_permalink(permalink):
    try:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(permalink).query)
        return q.get('thread_ts', [None])[0]
    except Exception:
        return None

def resolve_names(state, user_ids, token):
    cache = state.setdefault('user_names', {})
    missing = [u for u in user_ids if u and u.startswith('U') and u not in cache]
    for uid in missing:
        resp = slack('users.info', token, {'user': uid})
        prof = resp.get('user', {}) if resp.get('ok') else {}
        cache[uid] = prof.get('real_name') or prof.get('name') or uid
        time.sleep(API_PAUSE)
    if missing:
        save_state(state)
    return cache

# --------------------------------------------------------------- item ops --

def create_item(state, text, to='', channel=None, channel_name=None, thread_ts=None,
                 permalink='', due=None, project=None, source_type='manual',
                 source_ref='', confidence='medium', priority=None, notes=None,
                 dedupe=True, node=None, node_why=None):
    """Create a commitment, or fold it into the existing one it duplicates.

    Every ingestion path (fathom / slack / local MOM / manual / extract) funnels
    through here, so this is the one place a content-level guard actually holds.
    Only high-confidence (>= DUP_AUTO) matches merge silently; anything arguable
    is inserted and left for `dedupe` to adjudicate with the full picture.
    """
    if dedupe:
        existing = find_content_dup(state, text, to=to)
        if existing is not None:
            incoming = {
                'id': f'(ingest:{source_type})', 'text': text, 'to': to, 'due': due,
                'project': project, 'permalink': permalink, 'channel': channel,
                'channel_name': channel_name, 'thread_ts': thread_ts,
                'to_slug': resolve_person_slug(to) if to else '',
                'source': {'type': source_type, 'ref': source_ref or ''},
                'confidence': confidence, 'priority': bool(priority),
                'notes': notes or [],
            }
            # The richer text wins the record, but the merge absorbs both, so a
            # terse fathom line still donates its permalink to a verbose manual one.
            if _richness(incoming) > _richness(existing):
                existing['text'] = (text or '')[:600]
            merge_items(existing, incoming)
            # A real node always beats 'unfiled': a manual add naming its node
            # should file the record the sweep ingested blind.
            if node and existing.get('node', UNFILED) == UNFILED:
                existing['node'] = node
                existing['node_why'] = node_why
            return existing

    iid = next_id(state)
    if priority is None:
        priority = 'YourManager' in (to or '').lower()
    state['items'][iid] = {
        'id': iid, 'text': (text or '')[:600], 'to': to or '',
        'to_slug': resolve_person_slug(to) if to else '',
        'channel': channel, 'channel_name': channel_name,
        'thread_ts': thread_ts, 'permalink': permalink or '',
        'due': due, 'project': project,
        # Work-tree node. Unattended ingestion paths file as 'unfiled' with the
        # reason recorded, and surface in the morning update until triaged.
        'node': node or UNFILED,
        'node_why': node_why or (None if node else f'auto-ingest:{source_type}'),
        'source': {'type': source_type, 'ref': source_ref or ''},
        'status': 'open', 'confidence': confidence,
        'first_seen': time.time(), 'closed_at': None, 'closed_by': None,
        'priority': bool(priority), 'notes': notes or [],
    }
    return state['items'][iid]

# ------------------------------------------------------------- Fathom sweep --

def fathom_list_full(limit):
    try:
        out = subprocess.run(
            [sys.executable, FATHOM_CLIENT, '--action', 'list', '--full', '--limit', str(limit)],
            capture_output=True, text=True, timeout=170)
    except subprocess.TimeoutExpired:
        print('  ! fathom_client.py list timed out', file=sys.stderr)
        return []
    try:
        return json.loads(out.stdout)
    except Exception:
        print(f'  ! fathom_client.py list: unparseable output: {out.stdout[:200]}', file=sys.stderr)
        return []

def load_fathom_registry():
    if not os.path.exists(FATHOM_REGISTRY):
        return {}
    with open(FATHOM_REGISTRY) as f:
        return json.load(f)

def is_brian_assignee(assignee):
    if not isinstance(assignee, dict):
        return False
    email = (assignee.get('email') or '').strip().lower()
    name = (assignee.get('name') or '').strip().lower()
    if email == BRIAN_EMAIL:
        return True
    return any(tok in name for tok in BRIAN_NAME_TOKENS)

def sweep_fathom(state):
    """Registry entries not yet processed -> fathom_client.py --action list --full
    (NOT `get` - confirmed 404 on this deployment's recording IDs 2026-07-10;
    `list --full` DOES carry action_items per meeting, see integration_notes) ->
    action items assigned to the owner, not yet completed -> high-confidence items."""
    registry = load_fathom_registry()
    processed = set(state.get('processed_fathom_ids', []))
    cutoff = (wib_today() - timedelta(days=FIRST_RUN_FATHOM_LOOKBACK_DAYS)).isoformat()
    unprocessed_recent = [
        rid for rid, meta in registry.items()
        if rid not in processed and isinstance(meta, dict)
        and (meta.get('date_wib') or '') >= cutoff
    ]
    if not unprocessed_recent and processed:
        return 0   # nothing new since last sweep, don't burn a Fathom API call
    limit = max(10, min(FATHOM_LIST_LIMIT, len(unprocessed_recent) + 5)) if unprocessed_recent else 10
    meetings = fathom_list_full(limit)
    new_items = 0
    for m in meetings:
        rid = str(m.get('recording_id') or '')
        if not rid or rid not in registry or rid in processed:
            continue
        processed.add(rid)
        reg_meta = registry.get(rid, {})
        for idx, ai in enumerate(m.get('action_items') or []):
            if ai.get('completed'):
                continue
            if not is_brian_assignee(ai.get('assignee')):
                continue
            key = f'fathom:{rid}:{idx}'
            if key in state['processed_sources']:
                continue
            state['processed_sources'][key] = True
            create_item(
                state, text=ai.get('description', ''), to='',
                permalink=ai.get('recording_playback_url', ''),
                project=reg_meta.get('project'),
                source_type='fathom',
                source_ref=m.get('url') or reg_meta.get('fathom_url', ''),
                confidence='high',
            )
            new_items += 1
    state['processed_fathom_ids'] = sorted(processed)
    return new_items

# -------------------------------------------------------------- Slack sweep --

def sweep_slack_candidates(token, state, brian_id):
    """search.messages from:<@brian_id> since watermark -> cheap regex filter ->
    pending_candidates. Extraction into real items happens in `extract`, not here."""
    wm = float(state.get('slack_search_watermark') or 0)
    if wm == 0:
        wm = time.time() - FIRST_RUN_SLACK_LOOKBACK_DAYS * 86400
    newest = wm
    page, found = 1, 0
    while page <= 5:                       # bounded: a few pages max
        resp = slack('search.messages', token, {
            'query': f'from:<@{brian_id}>', 'sort': 'timestamp', 'sort_dir': 'desc',
            'count': 20, 'page': page})
        if not resp.get('ok'):
            print(f'  ! search.messages: {resp.get("error")}', file=sys.stderr)
            break
        matches = resp.get('messages', {}).get('matches', [])
        if not matches:
            break
        stop = False
        for m in matches:
            ts = float(m.get('ts', 0))
            if ts <= wm:
                stop = True
                break
            newest = max(newest, ts)
            text = m.get('text', '')
            if not COMMIT_RE.search(text):
                continue
            ch = m.get('channel', {})
            key = f"slack:{ch.get('id', '?')}:{m.get('ts')}"
            if key in state['processed_sources']:
                continue
            state['processed_sources'][key] = True
            permalink = m.get('permalink', '')
            state['pending_candidates'].append({
                'ts': m.get('ts'), 'channel': ch.get('id', '?'),
                'channel_name': ch.get('name', '?'), 'text': text[:500],
                'permalink': permalink,
                'thread_ts': thread_ts_from_permalink(permalink),
                'added_at': time.time(),
            })
            found += 1
        pages_total = resp.get('messages', {}).get('paging', {}).get('pages', 1)
        if stop or page >= pages_total:
            break
        page += 1
        time.sleep(API_PAUSE)
    state['slack_search_watermark'] = newest
    return found

# -------------------------------------------------------------- local sweep --

def local_meeting_files():
    """Every local artifact eligible for the cue/owner scan: TRANSCRIPTS_DIR
    (raw local/vexa transcripts), MOM_DIR (Work MOM drafts - same dir the
    watcher + /mom write to), and Clients/*/meetings/*.md (any client's meeting
    notes/MOM, current + future). MOM_DIR already matches the glob pattern;
    a set dedupes the overlap. .md only, per spec (the .txt raw whisper
    sidecars in TRANSCRIPTS_DIR are not scanned)."""
    paths = set()
    for d in (TRANSCRIPTS_DIR, MOM_DIR):
        if os.path.isdir(d):
            for fn in os.listdir(d):
                if fn.lower().endswith('.md'):
                    paths.add(os.path.join(d, fn))
    for p in glob.glob(CLIENTS_MEETINGS_GLOB):
        paths.add(p)
    return sorted(paths)

def _heading_text_is_action_items(text):
    return bool(ACTION_ITEMS_TEXT_RE.match(text.strip()))

def extract_mom_action_lines(text):
    """Within each 'Action Items' heading's section (bounded by the next
    heading at the SAME or SHALLOWER level - a deeper subheading like
    '### Work Team' stays inside the section), return every raw line that
    names the owner as owner (`\\bBrian\\b`, word-boundary - table row, checkbox
    bullet, or plain line, whatever format that MOM happens to use)."""
    lines = text.splitlines()
    n = len(lines)
    out = []
    i = 0
    while i < n:
        hm = HEADING_RE.match(lines[i])
        if hm and _heading_text_is_action_items(hm.group(2)):
            level = len(hm.group(1))
            j = i + 1
            while j < n:
                hm2 = HEADING_RE.match(lines[j])
                if hm2 and len(hm2.group(1)) <= level:
                    break
                j += 1
            for line in lines[i + 1:j]:
                if line_owner_is_brian(line):
                    out.append(line)
            i = j
        else:
            i += 1
    return out

def clean_mom_line(line):
    """Strip markdown table/checkbox/bold decoration from a MOM Action Items
    line so the captured commitment text reads as prose, not raw markdown."""
    s = line.strip()
    s = re.sub(r'^-\s*\[[ xX]\]\s*', '', s)     # checkbox bullet
    s = re.sub(r'^\|\s*', '', s)                 # leading table pipe
    s = re.sub(r'\s*\|\s*$', '', s)              # trailing table pipe
    s = s.replace('|', ' - ')                    # inner table pipes -> separator
    s = s.replace('**', '')                      # bold markers
    return re.sub(r'\s+', ' ', s).strip()

def sweep_local(state):
    """Mechanical, no-LLM: scan local transcripts + MOM drafts for (1) the owner's
    spoken action-item cues (CUE_RE) and (2) MOM 'Action Items' section rows
    that name the owner as owner. Only files touched since `local_watermark`
    (first run: 3-day lookback, matching the Fathom/Slack sources). Dedupe is
    per-line via a content hash so re-touching a file (e.g. a later /mom edit)
    only picks up genuinely new lines."""
    wm = float(state.get('local_watermark') or 0)
    if wm == 0:
        wm = time.time() - FIRST_RUN_LOCAL_LOOKBACK_DAYS * 86400
    newest = wm
    new_items = 0
    for path in local_meeting_files():
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if mtime <= wm:
            continue
        newest = max(newest, mtime)
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                text = f.read()
        except Exception:
            continue
        relpath = os.path.relpath(path, BASE_DIR)

        candidates = []   # (raw_line_for_hash, captured_text)
        for m in CUE_RE.finditer(text):
            captured = re.sub(r'^[:\-]+\s*', '', m.group(1).strip())
            if len(captured.split()) < MIN_CAPTURE_WORDS:
                continue
            line_start = text.rfind('\n', 0, m.start()) + 1
            line_end = text.find('\n', m.end())
            if line_end == -1:
                line_end = len(text)
            candidates.append((text[line_start:line_end], captured[:300]))

        for raw_line in extract_mom_action_lines(text):
            cleaned = clean_mom_line(raw_line)
            if len(cleaned.split()) < MIN_CAPTURE_WORDS:
                continue
            candidates.append((raw_line, cleaned[:300]))

        # You work never enters the Work ledger. Judge the whole file, not the
        # single line: a Dibimbing or AI Circle MOM produces action items whose own
        # wording ("submit presentation slides") looks perfectly like Work work.
        if YOU_RE.search(text[:4000]) or YOU_RE.search(relpath):
            print('  skip (You work, belongs in the You repo): {}'.format(relpath))
            for raw_line, _ in candidates:
                line_hash = hashlib.sha1(raw_line.encode('utf-8', 'replace')).hexdigest()[:12]
                state['processed_sources'][f'local:{relpath}:{line_hash}'] = True
            continue

        for raw_line, captured in candidates:
            line_hash = hashlib.sha1(raw_line.encode('utf-8', 'replace')).hexdigest()[:12]
            key = f'local:{relpath}:{line_hash}'
            if key in state['processed_sources']:
                continue
            state['processed_sources'][key] = True
            create_item(
                state, text=captured, to='',
                permalink=relpath, source_type='meeting-local',
                source_ref=relpath, confidence='high',
            )
            new_items += 1
    state['local_watermark'] = newest
    return new_items

# --------------------------------------------------------------- auto-close --

def sweep_auto_close(token, state):
    """Mechanical only: for open items with a channel+thread_ts, check if the owner
    posted a later message containing a completion word or a Drive/Docs link."""
    closed = 0
    for it in state['items'].values():
        if it['status'] != 'open' or not it.get('channel') or not it.get('thread_ts'):
            continue
        resp = slack('conversations.replies', token,
                     {'channel': it['channel'], 'ts': it['thread_ts'], 'limit': 100})
        time.sleep(API_PAUSE)
        if not resp.get('ok'):
            continue
        anchor_ts = float(it.get('ts') or it['thread_ts'])
        for msg in resp.get('messages', []):
            if msg.get('user') != BRIAN_ID_DEFAULT:
                continue
            if float(msg.get('ts', 0)) <= anchor_ts:
                continue
            text = msg.get('text', '')
            if CLOSE_WORD_RE.search(text) or DRIVE_LINK_RE.search(text):
                it.update(status='done', closed_at=time.time(), closed_by='auto_thread')
                closed += 1
                break
    return closed

def heartbeat(job, status, summary):
    try:
        subprocess.run([sys.executable, HEARTBEAT, '--job', job, '--status', status,
                        '--summary', summary], capture_output=True, text=True, timeout=15)
    except Exception as e:
        print(f'  ! heartbeat failed (non-fatal): {e}', file=sys.stderr)

def prune(state):
    cutoff = time.time() - CLOSED_RETENTION_DAYS * 86400
    dead = [iid for iid, it in state['items'].items()
            if it['status'] in ('done', 'dropped')
            and (it.get('closed_at') or it['first_seen']) < cutoff]
    for iid in dead:
        del state['items'][iid]
    # bound pending_candidates growth: drop anything extract should have consumed
    # long ago (>14d unconsumed = stale, likely superseded)
    cand_cutoff = time.time() - CLOSED_RETENTION_DAYS * 86400
    state['pending_candidates'] = [c for c in state['pending_candidates']
                                   if c.get('added_at', 0) >= cand_cutoff]
    return len(dead)

# -------------------------------------------------------------------- sweep --

def _wib_timestamp_header(label):
    wib = datetime.now(timezone.utc) + timedelta(hours=7)
    print(f'=== {wib.strftime("%Y-%m-%d %H:%M")} WIB {label} ===')

def cmd_sweep(args):
    _wib_timestamp_header('sweep')
    try:
        token = load_token()
        auth = slack('auth.test', token)
        brian_id = auth.get('user_id') or BRIAN_ID_DEFAULT
        state = load_state()
        t0 = time.time()
        n_fathom = sweep_fathom(state)
        n_cand = sweep_slack_candidates(token, state, brian_id)
        n_local = sweep_local(state)
        n_closed = sweep_auto_close(token, state)
        n_pruned = prune(state)
        state['last_sweep'] = time.time()
        save_state(state)
        open_items = [i for i in state['items'].values() if i['status'] == 'open']
        summary = (f'+{n_fathom} fathom, +{n_cand} slack candidates, +{n_local} local-meeting, '
                   f'{n_closed} auto-closed, {n_pruned} pruned -> {len(open_items)} OPEN')
        print(f'sweep done in {time.time()-t0:.0f}s: {summary}, '
              f'{len(state["pending_candidates"])} pending extraction')
        heartbeat('commitment-ledger', 'ok', summary)
    except SystemExit:
        raise
    except Exception as e:
        heartbeat('commitment-ledger', 'fail', str(e)[:280])
        raise

# ------------------------------------------------------------------ extract --

def cmd_extract(args):
    """Batch pending_candidates through GLM (agy-bridge harvest). GLM only
    extracts structure (to/due/is_commitment) - it never decides open/closed."""
    _wib_timestamp_header('extract')
    state = load_state()
    candidates = state.get('pending_candidates', [])
    if not candidates:
        print('no pending candidates, nothing to extract')
        return
    batch = candidates[:args.limit]
    prompt = (
        'You are a mechanical outbound-commitment extractor for the owner (Work PM). '
        'Each line below is a Slack message the owner SENT. For EACH line, output ONE JSON '
        'line: {"ts":"<ts>", "is_commitment": true|false, "to": "<name the owner is '
        'committing to, or empty>", "due": "<YYYY-MM-DD or null>", "text": "<short '
        'restatement of what the owner committed to do, max 20 words>"}. '
        'is_commitment=true ONLY if the owner is promising a future action to someone '
        '(e.g. "I\'ll send you the PRD tomorrow"). Questions, acknowledgments, and '
        'statements about the past are is_commitment=false. '
        'Resolve relative due words ("tomorrow", "next week") against the date the '
        'message was SENT (derive it from that line\'s ts), never against today. '
        'A due date must never fall in a past year or before the message date; '
        'if it would, output null instead of guessing. '
        'Output ONLY JSON lines, no prose.\n\n'
        + '\n'.join(json.dumps({'ts': c['ts'], 'channel': c['channel_name'],
                                'text': c['text']}, ensure_ascii=False) for c in batch)
    )
    tmp_dir = os.path.join(BASE_DIR, 'journal', 'state')
    prompt_path = os.path.join(tmp_dir, 'commitment_extract_prompt.txt')
    with open(prompt_path, 'w') as f:
        f.write(prompt)
    out = subprocess.run([sys.executable, AGY_BRIDGE, '--task', 'harvest',
                          '--prompt-file', prompt_path, '--timeout', '180'],
                         capture_output=True, text=True)
    if out.returncode == 3:
        print('FALLBACK_TO_CLAUDE: agy-bridge exhausted its chain; '
              f'{len(batch)} candidates left pending for Claude to extract manually.')
        print(out.stdout.strip())
        return   # rc 3 is NOT a failure - candidates stay pending, exit 0
    if out.returncode != 0 or not out.stdout.strip():
        print(f'extract failed (rc={out.returncode}); candidates kept for retry.\n'
              f'{out.stderr[:500]}', file=sys.stderr)
        sys.exit(1)
    created, by_ts = 0, {c['ts']: c for c in batch}
    consumed_ts = set()
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        ts = row.get('ts')
        cand = by_ts.get(ts)
        if not cand:
            continue
        consumed_ts.add(ts)
        if not row.get('is_commitment'):
            continue
        create_item(
            state, text=row.get('text') or cand['text'], to=row.get('to', '') or '',
            channel=cand['channel'], channel_name=cand['channel_name'],
            thread_ts=cand.get('thread_ts'), permalink=cand.get('permalink', ''),
            due=row.get('due') or None, source_type='slack',
            source_ref=cand.get('permalink', ''), confidence='medium',
        )
        created += 1
    # remove consumed candidates (matched by the LLM, whether or not they became
    # a commitment); anything the LLM silently dropped stays pending for next run
    state['pending_candidates'] = [c for c in state['pending_candidates']
                                    if c['ts'] not in consumed_ts]
    save_state(state)
    print(f'extract: {len(batch)} candidates -> {created} new commitments, '
          f'{len(consumed_ts)} triaged, {len(state["pending_candidates"])} still pending')

# ---------------------------------------------------------------------- CLI --

def cmd_add(args):
    try:
        node = resolve_or_die(args.node, allow_unfiled=bool(args.node_why))
    except UnknownNode as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)
    state = load_state()
    it = create_item(
        state, text=args.text, to=args.to or '', due=args.due, project=args.project,
        source_type='manual', source_ref=args.source or '', confidence='high',
        priority=args.priority, node=node, node_why=args.node_why,
    )
    save_state(state)
    print(f"added: {it['id']}  node={it.get('node')}")

def cmd_refile(args):
    """Move a record to another node. The triage pass runs on this."""
    try:
        node = resolve_or_die(args.node, allow_unfiled=bool(args.node_why))
    except UnknownNode as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)
    state = load_state()
    it = state['items'].get(args.item_id)
    if not it:
        print(f"no such commitment: {args.item_id}", file=sys.stderr)
        sys.exit(1)
    was = it.get('node', UNFILED)
    it['node'] = node
    it['node_why'] = args.node_why
    save_state(state)
    print(f"{args.item_id}: {was} -> {node}")

def cmd_dedupe(args):
    """Collapse duplicate OPEN commitments that predate the ingest-time guard.

    Dry-run by default: a merge is destructive and the ledger drives priorities,
    so nothing is written without --apply.
    """
    state = load_state()
    open_items = [it for it in state['items'].values() if it.get('status') == 'open']
    open_items.sort(key=lambda it: it['id'])

    auto, band = [], []
    for i, a in enumerate(open_items):
        for b in open_items[i + 1:]:
            if not _compatible(a, b):
                continue
            s = _dup_score(a.get('text'), b.get('text'))
            if s >= DUP_AUTO:
                auto.append((s, a, b))
            elif s >= DUP_BAND:
                band.append((s, a, b))

    confirmed = set()
    if band and not args.no_glm:
        print(f'  adjudicating {len(band)} borderline pair(s) via GLM ...')
        pairs = [(a, b) for _, a, b in band]
        confirmed = _glm_adjudicate(pairs)
        print(f'  GLM: {len(confirmed)} of {len(band)} judged duplicate')

    merges = [(s, a, b) for s, a, b in auto]
    merges += [(s, a, b) for i, (s, a, b) in enumerate(band) if i in confirmed]
    merges.sort(key=lambda t: -t[0])

    # A record already folded into another must not also absorb a third: that
    # would chain merges through a dropped item and lose the trail.
    done, applied = set(), []
    for s, a, b in merges:
        if a['id'] in done or b['id'] in done:
            continue
        primary, secondary = (a, b) if _richness(a) >= _richness(b) else (b, a)
        applied.append((s, primary, secondary))
        done.add(secondary['id'])

    if not applied:
        print('dedupe: no duplicates found')
        return

    verb = 'merging' if args.apply else 'would merge'
    print(f'\ndedupe: {verb} {len(applied)} duplicate pair(s)\n')
    for s, primary, secondary in applied:
        via = 'auto' if s >= DUP_AUTO else 'glm'
        print(f"  [{via} {s:.2f}] keep {primary['id']}  <-  drop {secondary['id']}")
        print(f"      keep: {(primary.get('text') or '')[:96]}")
        print(f"      drop: {(secondary.get('text') or '')[:96]}")
        if args.apply:
            merge_items(primary, secondary)

    if args.apply:
        save_state(state)
        still_open = sum(1 for it in state['items'].values() if it.get('status') == 'open')
        print(f'\napplied: {len(applied)} merged -> {still_open} open')
    else:
        print('\n(dry run; re-run with --apply to write)')

def cmd_close(args):
    state = load_state()
    it = state['items'].get(args.item_id)
    if not it:
        sys.exit(f'item not found: {args.item_id}')
    it.update(status='done', closed_at=time.time(), closed_by='manual')
    if args.note:
        it.setdefault('notes', []).append(args.note)
    save_state(state)
    print(f'closed: {args.item_id}')

def cmd_drop(args):
    state = load_state()
    it = state['items'].get(args.item_id)
    if not it:
        sys.exit(f'item not found: {args.item_id}')
    it.update(status='dropped', closed_at=time.time(), closed_by='manual')
    if args.note:
        it.setdefault('notes', []).append(args.note)
    save_state(state)
    print(f'dropped: {args.item_id}')

def cmd_reopen(args):
    """Undo a close/drop (mis-click safety on the dashboard): back to open,
    closure fields cleared so reports/SLA math treat it as never closed.

    Reopening a DEDUPED item also has to unwind the merge on both sides,
    otherwise the item comes back while the record that absorbed it still claims
    it as merged_from - and the next dedupe run would happily re-merge it.
    """
    state = load_state()
    it = state['items'].get(args.item_id)
    if not it:
        sys.exit(f'item not found: {args.item_id}')

    primary_id = it.get('merged_into')
    if primary_id:
        primary = state['items'].get(primary_id)
        if primary:
            primary['merged_from'] = [
                m for m in primary.get('merged_from', [])
                if m.get('id') != args.item_id]
            if not primary['merged_from']:
                primary.pop('merged_from', None)
            primary.setdefault('notes', []).append(
                f'{args.item_id} un-merged (reopened); no longer treated as a duplicate')
            # Record the verdict on BOTH sides so a later dedupe run cannot
            # simply re-merge the pair and overturn this correction.
            primary.setdefault('not_dup_of', [])
            if args.item_id not in primary['not_dup_of']:
                primary['not_dup_of'].append(args.item_id)
            it.setdefault('not_dup_of', [])
            if primary_id not in it['not_dup_of']:
                it['not_dup_of'].append(primary_id)
        it.pop('merged_into', None)

    it.update(status='open', closed_at=None, closed_by=None)
    if args.note:
        it.setdefault('notes', []).append(args.note)
    save_state(state)
    if primary_id:
        print(f'reopened: {args.item_id} (un-merged from {primary_id})')
    else:
        print(f'reopened: {args.item_id}')

def cmd_link(args):
    """Link a commitment to a tracker ticket (tickets.json T-id) so the dashboard
    can show which commitments already have a ticket ('Jadiin ticket' follow-up)."""
    state = load_state()
    it = state['items'].get(args.item_id)
    if not it:
        sys.exit(f'item not found: {args.item_id}')
    it['ticket_id'] = args.ticket
    save_state(state)
    print(f"linked: {args.item_id} -> {args.ticket}")

def cmd_unlink(args):
    state = load_state()
    it = state['items'].get(args.item_id)
    if not it:
        sys.exit(f'item not found: {args.item_id}')
    it['ticket_id'] = None
    save_state(state)
    print(f'unlinked: {args.item_id}')

def age_str(ts):
    h = (time.time() - float(ts)) / 3600
    return f'{h/24:.1f}d' if h >= 24 else f'{h:.0f}h'

def cmd_report(args):
    state = load_state()
    items = list(state['items'].values())
    if not args.all:
        items = [it for it in items if it['status'] == 'open']
    if not items:
        print('No open commitments. Ledger is clean.')
        return
    today = wib_today()

    def overdue(it):
        if not it.get('due') or it['status'] != 'open':
            return False
        try:
            return datetime.strptime(it['due'], '%Y-%m-%d').date() < today
        except Exception:
            return False

    def sort_key(it):
        if it['status'] != 'open':
            return (2, 0, -it.get('closed_at', 0))
        if overdue(it):
            return (0, 0, it['due'])
        if it.get('due'):
            return (1, 0, it['due'])
        return (1, 1, -it['first_seen'])

    items.sort(key=sort_key)
    print(f'## 📌 Outbound Commitments ({sum(1 for i in items if i["status"] == "open")} open)\n')
    for it in items:
        if it['status'] != 'open':
            continue
        flag = '🔥 ' if it['priority'] else ''
        od = ' ⚠️ OVERDUE' if overdue(it) else ''
        due_s = f" (due {it['due']})" if it.get('due') else ''
        to_s = f" -> {it['to']}" if it.get('to') else ''
        link = f" [ref]({it['permalink']})" if it.get('permalink') else ''
        conf = it['confidence']
        print(f"- {flag}**{it['text']}**{to_s}{due_s}{od} · {age_str(it['first_seen'])} old · "
              f"{conf} conf{link}  `{it['id']}` [{it['source']['type']}]")
    closed = [it for it in items if it['status'] != 'open']
    if args.all and closed:
        print(f'\n### Closed ({len(closed)})\n')
        for it in closed:
            print(f"- ~~{it['text']}~~ · {it['status']} by {it.get('closed_by', '?')}  `{it['id']}`")

# -------------------------------------------------------------------- main --

def main():
    p = argparse.ArgumentParser(description='Outbound commitments ledger')
    sub = p.add_subparsers(dest='cmd')

    sub.add_parser('sweep')

    ep = sub.add_parser('extract')
    ep.add_argument('--limit', type=int, default=25)

    ap = sub.add_parser('add')
    ap.add_argument('--text', required=True)
    ap.add_argument('--to', default='')
    ap.add_argument('--due', default=None)
    ap.add_argument('--project', default=None)
    ap.add_argument('--source', default=None)
    ap.add_argument('--priority', action='store_true', default=None)
    ap.add_argument('--node', required=True,
                    help='work-tree node id; find one with work_tree.py find <text>')
    ap.add_argument('--node-why', default=None,
                    help='only with --node unfiled, and only for unattended runs')

    rf = sub.add_parser('refile', help='move a record to a different work-tree node')
    rf.add_argument('item_id')
    rf.add_argument('--node', required=True)
    rf.add_argument('--node-why', default=None)

    cp = sub.add_parser('close')
    cp.add_argument('item_id')
    cp.add_argument('--note', default=None)

    dp = sub.add_parser('drop')
    dp.add_argument('item_id')
    dp.add_argument('--note', default=None)

    rop = sub.add_parser('reopen')
    rop.add_argument('item_id')
    rop.add_argument('--note', default=None)

    lp = sub.add_parser('link')
    lp.add_argument('item_id')
    lp.add_argument('--ticket', required=True)

    ulp = sub.add_parser('unlink')
    ulp.add_argument('item_id')

    rp = sub.add_parser('report')
    rp.add_argument('--all', action='store_true')

    ddp = sub.add_parser('dedupe', help='collapse duplicate open commitments')
    ddp.add_argument('--apply', action='store_true',
                     help='write the merges (default is a dry run)')
    ddp.add_argument('--no-glm', action='store_true',
                     help='deterministic matches only; skip GLM adjudication')

    args = p.parse_args()
    {'sweep': cmd_sweep, 'extract': cmd_extract, 'add': cmd_add,
     'close': cmd_close, 'drop': cmd_drop, 'reopen': cmd_reopen,
     'report': cmd_report, 'link': cmd_link, 'unlink': cmd_unlink,
     'dedupe': cmd_dedupe, 'refile': cmd_refile,
     }.get(args.cmd or 'sweep', cmd_sweep)(args)

READONLY_CMDS = {'report'}

if __name__ == '__main__':
    # Serialise mutating runs. load_state/save_state is a read-modify-write
    # cycle and os.replace only makes the write atomic, not the cycle. Two
    # overlapping runs otherwise silently drop whichever record was written
    # first. See .agent/scripts/ledger_lock.py for the incident this comes from.
    _cmd = next((a for a in sys.argv[1:] if not a.startswith('-')), None)
    if _cmd in READONLY_CMDS:
        main()
    else:
        sys.path.insert(0, os.path.join(BASE_DIR, '.agent', 'scripts'))
        from ledger_lock import hold_ledger_lock
        hold_ledger_lock('commitments')
        # Propagate on the way out: re-render the indexes and the follow-up
        # tracker, then commit + push, so the next session to read this repo
        # sees the change now rather than after the next daily run.
        # See .agent/scripts/ledger_sync.py.
        from ledger_sync import run_and_sync
        run_and_sync('commitments', main)
