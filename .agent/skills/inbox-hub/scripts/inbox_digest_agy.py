#!/usr/bin/env python3
"""Generate substantive inbox reply drafts via agy-bridge (GLM), not a Claude
headless run over the whole inbox.

Replaces the inbox-digest Claude ai-task (dashboard/server.py kind='inbox-digest',
sonnet). That run read the FULL journal/state/inbox.json (~810KB / 200k tokens)
plus unbounded greps every 30-min cron cycle just to upgrade ~8 items, costing
~1.7M input tokens and ~$0.91 per run (~157 runs/week). See
[[reference_agy_bridge]] and the owner's standing rule: bulk draft work over
already-known context belongs on a cheap non-Claude model, exactly like
enrich_cards_agy.py for pre-meeting cards.

Division of labour (same as enrich_cards_agy.py, and it matters):
  - PYTHON gathers. It has the repo, the ledgers, and the inbox state. It filters
    to the <=8 items that actually need a draft, resolves UIDs to real names, and
    hunts real doc paths. Deterministic, no guessing.
  - GLM writes. No tools, no ground truth beyond this prompt.
  - A LIGHT Claude review pass (still in the ai-task, but reading only THIS
    script's printed output, not the inbox) spot-checks facts and links tickets.

Telemetry: every GLM call is tagged --label ai-inbox-digest so token_efficiency
buckets the offloaded tokens against the same ai-inbox-digest hotspot that the
Claude review run lands in, giving an honest per-pipeline offload %.

Usage:
  inbox_digest_agy.py                 # draft up to 8 items needing upgrade, persist
  inbox_digest_agy.py --dry-run       # print drafts, write nothing (safe test)
  inbox_digest_agy.py --limit 8       # cap items (default 8)
  inbox_digest_agy.py --regenerate    # also re-draft items already at 'claude' tier
  inbox_digest_agy.py                 # Gemini via agy (z.ai/GLM retired 2026-07-27)
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
INBOX_STATE = os.path.join(BASE_DIR, 'journal', 'state', 'inbox.json')
NAMES_PATH = os.path.join(BASE_DIR, 'journal', 'state', 'slack_user_names.json')
AGY_BRIDGE = os.path.join(BASE_DIR, '.agent', 'skills', 'agy-bridge', 'run.py')
INBOX_CLI = os.path.join(BASE_DIR, '.agent', 'skills', 'inbox-hub', 'scripts', 'inbox_sweep.py')

sys.path.insert(0, os.path.join(BASE_DIR, '.agent', 'scripts'))
from brian_voice import voice_block  # noqa: E402

# generic words that make a term useless for grepping repo context
_STOP = {'the', 'and', 'for', 'with', 'please', 'this', 'that', 'from', 'your',
         'owner', 'you', 'confirm', 'review', 'update', 'proceed', 'above',
         'request', 'ensure', 'support', 'need', 'once', 'team', 'plan'}
_UID_RE = re.compile(r'\bU[A-Z0-9]{7,}\b')

def load_names():
    try:
        with open(NAMES_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def resolve_uids(text, names):
    """Replace bare/@-prefixed Slack UIDs with real names; leave unknown ones as
    a neutral '@someone' rather than a raw id GLM would echo verbatim."""
    def sub(m):
        uid = m.group(0)
        return names.get(uid, '@someone')
    return _UID_RE.sub(sub, text or '')

def _terms(item):
    src = f"{item.get('title') or ''} {item.get('text') or ''}"
    words = re.findall(r'[A-Za-z0-9]{4,}', src)
    seen, out = set(), []
    for w in words:
        lw = w.lower()
        if lw in _STOP or lw in seen:
            continue
        seen.add(lw)
        out.append(w)
    return out[:3]

def hunt_docs(item):
    """Real, existing Work docs matching this item's distinctive terms. Only
    verified paths reach GLM, so it cannot invent a plausible dead link."""
    found = []
    for term in _terms(item):
        try:
            out = subprocess.run(
                ['grep', '-ril', '--include=*.md', term,
                 os.path.join(BASE_DIR, 'Clients', 'Work')],
                capture_output=True, text=True, timeout=20)
            for p in out.stdout.splitlines()[:3]:
                rel = os.path.relpath(p, BASE_DIR)
                if rel not in found and '/meetings/transcripts/' not in rel:
                    found.append(rel)
        except Exception:
            pass
    return found[:6]

def build_conversation(item, names):
    """Compact, name-resolved transcript of the item. This is the only per-item
    context GLM gets besides the doc list, and it is a few KB, not 200k tokens."""
    lines = []
    for m in (item.get('messages') or [])[-8:]:
        who = names.get(m.get('from') or '', None) or resolve_uids(m.get('from') or '', names)
        txt = resolve_uids((m.get('text') or '').strip(), names)
        if txt:
            lines.append(f"{who}: {txt}")
    if not lines:
        who = item.get('from') or resolve_uids(item.get('from_id') or '', names)
        lines.append(f"{who}: {resolve_uids(item.get('text') or item.get('title') or '', names)}")
    return '\n'.join(lines)[:4000]

def build_prompt(item, convo, docs):
    doc_block = '\n'.join(f'- {d}' for d in docs) or '- (none found; do not invent any)'
    channel = item.get('channel') or '?'
    return f"""You draft a reply for Your Name, a product leader (Product Director) at Work, Saudi Arabia.
This is one conversation from his inbox. Write the reply he should send.

CHANNEL: {channel}

CONVERSATION (most recent last, names already resolved, treat as ground truth):
{convo}

VERIFIED REPO DOCS you MAY reference by name (these exist; do NOT invent others):
{doc_block}

{voice_block()}

Write ONLY the reply text the owner will send. The voice model above is measured from his own
messages; follow it over any instinct about what a professional reply looks like. Rules:
- SOLVE the ask: answer the actual question when the conversation or a listed doc lets you.
  When you cannot answer it here, commit to ONE concrete next step, who the owner will check with
  and by when, never a contentless acknowledgement.
- Use real names only. Never invent a person, ticket id, URL, doc, date, or number.
- Never use an em-dash. Use a comma, a full stop, or rephrase.
- No preamble, no greeting line unless natural, no sign-off block. Just the reply body.
"""

# Agentic backends (Gemini via agy) sometimes read a "draft this reply" prompt as
# a task to EXECUTE and narrate tool use ("I will list the contents of...") instead
# of answering. That is unsendable and must never overwrite a real draft, so we
# reject it and fail closed.
#
# GLM (zai) did not do this, which is why it used to be pinned here. z.ai was
# retired 2026-07-27 (subscription no longer active), so this task now runs on
# Gemini via the normal agy-bridge chain and this guard is load-bearing rather
# than belt-and-braces. Expect a higher reject rate: a rejected draft fails
# closed and leaves the previous draft untouched, so the failure mode is a
# missing draft, never a bad one. If rejects get noisy, harden the prompt's
# "answer, do not act" framing rather than weakening this regex.
_NARRATION_RE = re.compile(
    r'^\s*I (?:am going to|will|should|need to|shall)\b.*\b'
    r'(list|search|check|read|look|explore|inspect|find|navigate|run|execute|open)\b',
    re.IGNORECASE | re.MULTILINE)

def call_agy(prompt, allow_chain=False):
    """(text, note) on success, (None, reason) on failure. Fails closed so a
    broken bridge leaves the existing draft untouched, never a half-written one.
    Runs agy-bridge's normal chain (Gemini-first since the 2026-07-27 z.ai
    retirement); allow_chain is retained for call-site compatibility."""
    tmp = os.path.join(tempfile.gettempdir(), f'ibxd_{os.getpid()}_{int(time.time()*1000)}.txt')
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(prompt)
        cmd = [sys.executable, AGY_BRIDGE, '--task', 'draft', '--label', 'ai-inbox-digest',
               '--prompt-file', tmp]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
        if out.returncode == 3:
            return None, 'fallback_to_claude sentinel'
        if out.returncode != 0:
            return None, f'agy-bridge rc={out.returncode}: {out.stderr.strip()[:160]}'
        body = out.stdout.strip()
        if not body:
            return None, 'empty output'
        if _NARRATION_RE.search(body):
            return None, 'rejected: model narrated tool use instead of drafting a reply'
        if '—' in body or '--' in body:
            body = body.replace('—', ', ').replace('--', '-')
        note = ''
        for line in out.stderr.splitlines():
            if 'answered by' in line:
                note = line.strip()
        return body, note
    except Exception as e:
        return None, f'{type(e).__name__}: {e}'
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

def persist_draft(item_id, text):
    tmp = os.path.join(tempfile.gettempdir(), f'ibxd_set_{os.getpid()}_{int(time.time()*1000)}.txt')
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(text)
    try:
        out = subprocess.run(
            [sys.executable, INBOX_CLI, 'set-draft', item_id, '--file', tmp, '--source', 'claude'],
            capture_output=True, text=True, timeout=30)
        return out.returncode == 0, (out.stdout or out.stderr).strip()
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

# Hard-fact signals in a conversation. When the material a reply is built on
# involves tickets, docs, links, numbers, or dates, a GLM draft is most likely to
# invent a specific-but-wrong fact, and that is exactly the class haiku is weakest
# at catching. Those runs escalate the review to sonnet; everything else stays on
# haiku. See [[reference_agy_bridge]] and the token_efficiency changelog.
_HARDFACT_RE = re.compile(
    r'\b(?:COM|WAIT|MPS|MBA|MSA|STOR|MSP|DEC|OMS|COM|T)-\d+\b'   # Work ticket ids
    r'|https?://|docs\.google\.com|\.md\b|\.pdf\b'                # docs / links
    r'|\b\d+\s?(?:days?|day|weeks?|hours?|%|SAR|USD|AED)\b'       # numeric facts
    r'|\b\d{4}-\d{2}-\d{2}\b'                                      # ISO dates
    r'|\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)',
    re.IGNORECASE)

def needs_sonnet_review(items, names):
    """True when any selected item's conversation cites a hard fact, so the review
    should escalate from haiku to sonnet. Best-effort proxy decided BEFORE drafting
    (from the source material, which the reply will lean on), so the server can pick
    the review model at spawn time without waiting for generation."""
    for it in items:
        blob = f"{it.get('title') or ''} {it.get('text') or ''} " + \
               ' '.join((m.get('text') or '') for m in (it.get('messages') or []))
        if _HARDFACT_RE.search(resolve_uids(blob, names)):
            return True
    return False

def select_items(state, limit, regenerate):
    tiers = {None, 'glm'} if not regenerate else {None, 'glm', 'claude'}
    todo = [it for it in state['items'].values()
            if it.get('status') == 'open' and it.get('triage') == 'reply'
            and it.get('draft_source') in tiers]
    todo.sort(key=lambda i: (-int(bool(i.get('priority_hi'))), -(i.get('ts') or 0)))
    return todo[:limit]

HEARTBEAT = os.path.join(BASE_DIR, '.agent', 'scripts', 'heartbeat.py')

def _is_outage_reason(note):
    """A failure note that points at the GLM/agy backend being unreachable or
    unable, as opposed to one bad item. Used to decide fail-fast + outage sentinel."""
    n = (note or '').lower()
    return any(k in n for k in ('sentinel', 'rc=', 'timeout', 'timedout', 'connection',
                                'refused', 'auth', 'empty output', 'unavailable', '502', '503', '429'))

def alarm(summary):
    """Fire a heartbeat FAIL so a GLM outage is visible on the dashboard, never silent."""
    try:
        subprocess.run([sys.executable, HEARTBEAT, '--job', 'inbox-digest',
                        '--status', 'fail', '--summary', summary],
                       capture_output=True, text=True, timeout=15)
    except Exception:
        pass

def main():
    p = argparse.ArgumentParser(description='Draft inbox replies via agy-bridge/GLM (offloads the inbox-digest Claude run)')
    p.add_argument('--dry-run', action='store_true', help='print drafts, write nothing')
    p.add_argument('--limit', type=int, default=8)
    p.add_argument('--regenerate', action='store_true', help='also re-draft items already at claude tier')
    p.add_argument('--allow-chain', action='store_true',
                   help='use agy full routing chain instead of pinning GLM (may hit agentic backends)')
    p.add_argument('--review-tier', action='store_true',
                   help='print the review model (sonnet if the batch cites hard facts, else haiku) '
                        'and exit; draft nothing. Used by the dashboard to pick the review model at spawn.')
    args = p.parse_args()

    with open(INBOX_STATE, encoding='utf-8') as f:
        state = json.load(f)
    names = load_names()
    todo = select_items(state, args.limit, args.regenerate)

    if args.review_tier:
        # fail-open to haiku (the cheap default) if there is nothing to draft
        print('sonnet' if (todo and needs_sonnet_review(todo, names)) else 'haiku')
        return 0
    if not todo:
        print('inbox-digest: nothing to draft (no open reply items need an upgrade)')
        return 0

    print(f'inbox-digest: drafting {len(todo)} item(s) via GLM'
          + (' [DRY RUN]' if args.dry_run else ''))
    counts = {'ok': 0, 'failed': 0}
    outage_notes = []
    for it in todo:
        convo = build_conversation(it, names)
        docs = hunt_docs(it)
        body, note = call_agy(build_prompt(it, convo, docs), allow_chain=args.allow_chain)
        title = (it.get('title') or it.get('text') or '')[:60]
        if body is None:
            counts['failed'] += 1
            if _is_outage_reason(note):
                outage_notes.append(note)
            print(f"  ! {it['id']} ({title}): {note}")
            # Fail-fast: two failures with no success yet means the backend is very
            # likely down. Stop hammering a dead GLM through all 8 items.
            if counts['ok'] == 0 and counts['failed'] >= 2:
                print('  ! two failures and no success, treating GLM as unavailable; stopping early')
                break
            continue
        if args.dry_run:
            counts['ok'] += 1
            print(f"\n{'='*70}\n{it['id']}  [{note}]\n  {title}\n{'-'*70}\n{body}\n")
            continue
        ok, msg = persist_draft(it['id'], body)
        counts['ok' if ok else 'failed'] += 1
        icon = '✓' if ok else '!'
        first = body.split('\n', 1)[0][:90]
        print(f"  {icon} {it['id']}: {first}")

    print(f"\ninbox-digest: ok={counts['ok']}, failed={counts['failed']}")

    # OUTAGE GUARD: GLM produced nothing for the whole batch. Emit a machine-readable
    # sentinel so the dashboard review pass falls back to drafting these items in
    # Claude (the degraded path, correctness over cost), and alarm it so it is not
    # silent. Not fired in dry-run (that is a test, not a cron run).
    if counts['ok'] == 0 and counts['failed'] > 0 and not args.dry_run:
        reason = outage_notes[0] if outage_notes else 'all items failed to draft'
        print(f"GLM_OUTAGE: {reason} — {counts['failed']} item(s) failed, 0 drafted. "
              f"Review pass should draft these items itself as a fallback.")
        alarm(f"GLM/agy unavailable for inbox-digest: {reason}; fell back to Claude drafting")
        return 2
    return 1 if counts['failed'] and not counts['ok'] else 0

if __name__ == '__main__':
    sys.exit(main())
