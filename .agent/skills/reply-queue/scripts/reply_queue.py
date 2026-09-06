#!/usr/bin/env python3
"""
reply_queue.py - Auto-drafted Reply Queue: extend the Slack mention ledger with
ready-to-approve draft replies for OPEN items, so the owner can review/edit/approve
instead of writing every reply from scratch.

Design (Component 7, per the bangun-aja-semuanya-barengan-wiggly-noodle plan):
  Layer 1 (mention_ledger.py, READ-ONLY here): collects + tracks open/answered.
  Layer 2 (this script): batches open items -> agy-bridge --task harvest (GLM) to
    draft replies in the owner's voice. GLM never sends anything - drafts only.
  Layer 3 (SOP): morning/evening update embeds `report` verbatim. Actually sending
    a reply still goes through .agent/protocols/slack_send.md with explicit approval.

ABSOLUTELY NO SEND PATH: this script contains zero Slack write calls. It only reads
the ledger state file and calls agy-bridge to generate text. Nothing here can post
to Slack, WhatsApp, or email.

Subcommands:
  draft [--limit 15]   draft (or re-draft changed) replies for open ledger items,
                        write journal/reply_drafts_<date>.md
  report [--all]       re-emit the current drafted queue as briefing-ready markdown
                        without calling the LLM again (for embedding in daily updates)

State: journal/state/reply_queue.json - drafted map:
  {"items": {<item_id>: {"drafted_at": epoch, "text_hash": "...", "draft_text": "..."}},
   "last_run": epoch}

Ledger (read-only): journal/state/slack_mention_ledger.json
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))

# Slack sends `<@<SLACK_ID>>`, not `@Teammate`. Without this, every "Original:" line below quotes a
# message that names nobody, which is what the owner was reading on 25 Aug 2026. Shared with the rest
# of the harness, and behaviourally identical to the Rust half in the ASB app's slackpush.rs.
sys.path.insert(0, os.path.join(BASE_DIR, '.agent', 'scripts'))
from slack_text import render as slack_render  # noqa: E402
STATE_PATH = os.path.join(BASE_DIR, 'journal', 'state', 'reply_queue.json')
LEDGER_PATH = os.path.join(BASE_DIR, 'journal', 'state', 'slack_mention_ledger.json')
TOKEN_ENV = os.path.join(BASE_DIR, '.agent', 'skills', 'slack-connector', 'token.env')
AGY_BRIDGE = os.path.join(BASE_DIR, '.agent', 'skills', 'agy-bridge', 'run.py')
OUTPUT_DIR = os.path.join(BASE_DIR, 'journal')

WIB = timezone(timedelta(hours=7))
API_PAUSE = 0.15
DEFAULT_LIMIT = 15
RETENTION_DAYS = 14   # drop drafted entries for items no longer open/known > this

VOICE_PROMPT_HEADER = (
    "You are drafting Slack REPLIES for the owner to review and send himself - you are "
    "NOT sending anything. Write in the owner's voice: plain flowing prose, no emoji, no "
    "numbered or bolded lists, direct and warm-brief (2-5 sentences). Do not use "
    "em-dashes. Do not add parenthetical asides. Each draft should read like a real "
    "person replying in Slack, not a formal memo.\n\n"
    "For EACH item below, output exactly this block format (blank line between items):\n"
    "ITEM: <item_id>\n"
    "DRAFT: <the reply text, one paragraph, no line breaks>\n\n"
    "Only draft a reply when there is something the owner can plausibly say without more "
    "context than given. If an item cannot be drafted responsibly (needs info you don't "
    "have), still include the block but set DRAFT to exactly: SKIP - needs more context.\n\n"
    "Items:\n\n"
)

# ------------------------------------------------------------------- state --

def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {'items': {}, 'last_run': None}

def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f, indent=1, ensure_ascii=False)
    os.replace(tmp, STATE_PATH)

def load_ledger():
    if not os.path.exists(LEDGER_PATH):
        return {'items': {}, 'user_names': {}}
    with open(LEDGER_PATH) as f:
        return json.load(f)

def text_hash(text):
    return hashlib.sha256((text or '').encode('utf-8')).hexdigest()[:16]

# ---------------------------------------------------------------- selection --

def open_items_sorted(ledger):
    items = [(iid, it) for iid, it in ledger.get('items', {}).items()
             if it.get('status') == 'open']
    items.sort(key=lambda x: (not x[1].get('priority'), -float(x[1].get('ts', 0))))
    return items

def context_str(it):
    parts = []
    for uid, ctx in it.get('context', []) or []:
        ctx1 = slack_render(ctx or '', collapse=True)[:200]
        parts.append(f'(re: {ctx1})')
    return ' '.join(parts)

def item_prompt_block(iid, it):
    chan = it.get('channel_name', '?')
    # Decoded for the MODEL too, not only for the owner. A drafter handed `<@<SLACK_ID>>` cannot tell
    # who is being asked for what, and writes a reply that answers nobody in particular.
    text = slack_render(it.get('text', ''), collapse=True)[:400]
    ctx = context_str(it)
    priority = ' [PRIORITY]' if it.get('priority') else ''
    lines = [f'item_id: {iid}{priority}', f'channel: {chan}', f'message: {text}']
    if ctx:
        lines.append(f'context: {ctx}')
    return '\n'.join(lines)

# ------------------------------------------------------------------ agy call --

def run_agy_bridge(prompt_text):
    """Returns (rc, stdout). rc==0 -> stdout is the model's answer text.
    rc==3 -> stdout is the {"status":"fallback_to_claude",...} sentinel JSON."""
    tmp = os.path.join(os.path.dirname(__file__), '.reply_queue_prompt.txt')
    with open(tmp, 'w') as f:
        f.write(prompt_text)
    import subprocess
    out = subprocess.run(
        [sys.executable, AGY_BRIDGE, '--task', 'harvest', '--prompt-file', tmp,
         '--timeout', '180'],
        capture_output=True, text=True)
    return out.returncode, out.stdout

def parse_drafts(stdout):
    """Parse 'ITEM: <id>\\nDRAFT: <text>' blocks into {item_id: draft_text}."""
    out = {}
    blocks = re.split(r'\n\s*\n', stdout.strip())
    for b in blocks:
        m_id = re.search(r'ITEM:\s*(\S+)', b)
        m_dr = re.search(r'DRAFT:\s*(.*)', b, re.DOTALL)
        if m_id and m_dr:
            iid = m_id.group(1).strip()
            draft = re.sub(r'\s+', ' ', m_dr.group(1)).strip()
            out[iid] = draft
    return out

# ------------------------------------------------------------------- prune --

def prune(state, ledger):
    """Drop drafted entries whose ledger item is gone or answered/dismissed and
    older than retention, or whose ledger item no longer exists at all."""
    cutoff = time.time() - RETENTION_DAYS * 86400
    ledger_items = ledger.get('items', {})
    dropped = 0
    for iid in list(state['items'].keys()):
        it = ledger_items.get(iid)
        if it is None:
            if state['items'][iid].get('drafted_at', 0) < cutoff:
                del state['items'][iid]
                dropped += 1
            continue
        if it.get('status') != 'open' and state['items'][iid].get('drafted_at', 0) < cutoff:
            del state['items'][iid]
            dropped += 1
    return dropped

# ------------------------------------------------------------------ output --

def output_path(date_str=None):
    date_str = date_str or datetime.now(WIB).strftime('%Y-%m-%d')
    return os.path.join(OUTPUT_DIR, f'reply_drafts_{date_str}.md')

def render_markdown(state, ledger, fallback_ids=None, all_items=False):
    fallback_ids = fallback_ids or []
    ledger_items = ledger.get('items', {})
    lines = []
    lines.append('> DRAFTS ONLY -- never send from here. Slack sends are '
                  'approval-gated via .agent/protocols/slack_send.md.')
    lines.append('')
    lines.append(f'# Reply Queue ({datetime.now(WIB).strftime("%Y-%m-%d %H:%M")} WIB)')
    lines.append('')

    rows = []
    for iid, entry in state.get('items', {}).items():
        it = ledger_items.get(iid)
        if it is None:
            continue
        if not all_items and it.get('status') != 'open':
            continue
        rows.append((iid, it, entry))
    rows.sort(key=lambda x: (not x[1].get('priority'), -float(x[1].get('ts', 0))))

    if not rows and not fallback_ids:
        lines.append('No drafted replies. Run `reply_queue.py draft` to populate the queue.')
        return '\n'.join(lines) + '\n'

    lines.append(f'## Ready to review ({len(rows)})')
    lines.append('')
    for iid, it, entry in rows:
        flag = '🔥 ' if it.get('priority') else ''
        chan = it.get('channel_name', '?')
        link = it.get('permalink') or ''
        link_md = f' [thread]({link})' if link else ''
        orig = slack_render(it.get('text', ''), collapse=True)[:160]
        ctx = context_str(it)
        draft = entry.get('draft_text', '')
        lines.append(f'- {flag}**{chan}**{link_md} `{iid}`')
        lines.append(f'  - Original: {orig}')
        if ctx:
            lines.append(f'  - {ctx}')
        if draft == 'SKIP - needs more context':
            lines.append('  - Draft: _skipped - needs more context, draft manually_')
        else:
            lines.append(f'  - Draft: {draft}')
        lines.append('')

    if fallback_ids:
        lines.append('## FALLBACK_TO_CLAUDE')
        lines.append('')
        lines.append('agy-bridge returned rc=3 (or failed to parse) for these items - '
                      'Claude should draft these directly instead:')
        lines.append('')
        for iid in fallback_ids:
            it = ledger_items.get(iid, {})
            orig = re.sub(r'\s+', ' ', it.get('text', ''))[:160]
            chan = it.get('channel_name', '?')
            lines.append(f'- `{iid}` **{chan}**: {orig}')
        lines.append('')

    return '\n'.join(lines) + '\n'

def write_output(md_text, date_str=None):
    path = output_path(date_str)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        f.write(md_text)
    os.replace(tmp, path)
    return path

# --------------------------------------------------------------- commands --

def cmd_draft(args):
    ledger = load_ledger()
    state = load_state()
    open_items = open_items_sorted(ledger)[:max(1, args.limit)]

    needs_draft = []
    for iid, it in open_items:
        th = text_hash(it.get('text', ''))
        cached = state['items'].get(iid)
        if cached is None or cached.get('text_hash') != th:
            needs_draft.append((iid, it, th))

    fallback_ids = []
    if needs_draft:
        prompt = VOICE_PROMPT_HEADER + '\n\n'.join(
            item_prompt_block(iid, it) for iid, it, _ in needs_draft)
        rc, stdout = run_agy_bridge(prompt)
        if rc == 3:
            fallback_ids = [iid for iid, _, _ in needs_draft]
            print(f'agy-bridge rc=3 (fallback_to_claude) for {len(fallback_ids)} items',
                  file=sys.stderr)
        elif rc != 0:
            fallback_ids = [iid for iid, _, _ in needs_draft]
            print(f'agy-bridge failed rc={rc}; treating {len(fallback_ids)} items as '
                  'FALLBACK_TO_CLAUDE', file=sys.stderr)
        else:
            parsed = parse_drafts(stdout)
            now = time.time()
            for iid, it, th in needs_draft:
                draft = parsed.get(iid)
                if not draft:
                    fallback_ids.append(iid)
                    continue
                state['items'][iid] = {
                    'drafted_at': now, 'text_hash': th, 'draft_text': draft,
                }

    n_pruned = prune(state, ledger)
    state['last_run'] = time.time()
    save_state(state)

    md = render_markdown(state, ledger, fallback_ids=fallback_ids)
    path = write_output(md)
    n_open_in_queue = sum(1 for iid in state['items'] if iid in ledger.get('items', {})
                          and ledger['items'][iid].get('status') == 'open')
    print(f'draft done: {len(needs_draft)} candidates, '
          f'{len(needs_draft) - len(fallback_ids)} drafted, {len(fallback_ids)} fallback, '
          f'{n_pruned} pruned -> {n_open_in_queue} in queue. Wrote {path}')

def cmd_report(args):
    ledger = load_ledger()
    state = load_state()
    md = render_markdown(state, ledger, all_items=args.all)
    print(md)

def main():
    p = argparse.ArgumentParser(description='Auto-drafted reply queue (drafts only, never sends)')
    sub = p.add_subparsers(dest='cmd')
    dp = sub.add_parser('draft')
    dp.add_argument('--limit', type=int, default=DEFAULT_LIMIT)
    rp = sub.add_parser('report')
    rp.add_argument('--all', action='store_true')
    args = p.parse_args()
    if args.cmd == 'report':
        cmd_report(args)
    else:
        if not hasattr(args, 'limit'):
            args.limit = DEFAULT_LIMIT
        cmd_draft(args)

if __name__ == '__main__':
    main()
