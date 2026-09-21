#!/usr/bin/env python3
"""Refetch the full text of Slack ledger records that were stored truncated.

Until 15 Sep 2026 `new_item` stored `text[:600]`. Reply drafts quote the original
message back to the owner, and the ledger is the only copy the drafting session sees,
so a cut record makes the reply answer half the ask with nothing on the page to
show that half is missing. Teammate's 11 Sep Apple Store list stopped at "proper bra".

The cap is gone, but records written before the fix still hold the cut copy. This
walks them, refetches each message from Slack, and writes the whole text back.

    python3 .agent/skills/slack-tracker/scripts/rehydrate_texts.py            # open records, dry run
    python3 .agent/skills/slack-tracker/scripts/rehydrate_texts.py --apply
    python3 .agent/skills/slack-tracker/scripts/rehydrate_texts.py --all --apply

A record whose message was deleted, or that sits in a channel the token cannot
read, is reported and left as it is.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mention_ledger as ml

# Anything at or above the old cap is a candidate. A message that happened to be
# exactly 600 characters refetches to the same string, so a false positive costs
# one API call and changes nothing.
OLD_CAP = 600

def fetch_text(token, channel, ts, thread_ts=None):
    """A message sits in history OR in a thread, never both.

    `conversations.history` returns channel-level messages only, so every thread
    reply comes back not_found there. Ask the thread for those.
    """
    resp = ml.slack('conversations.history', token, {
        'channel': channel, 'latest': ts, 'oldest': ts,
        'inclusive': 'true', 'limit': 1})
    if not resp.get('ok'):
        return None, resp.get('error', 'unknown')
    for msg in resp.get('messages') or []:
        if msg.get('ts') == ts:
            return msg.get('text') or '', None

    parent = thread_ts or ts
    resp = ml.slack('conversations.replies', token, {
        'channel': channel, 'ts': parent, 'limit': 200})
    if not resp.get('ok'):
        return None, resp.get('error', 'unknown')
    for msg in resp.get('messages') or []:
        if msg.get('ts') == ts:
            return msg.get('text') or '', None
    return None, 'not_found'

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='write the results back')
    ap.add_argument('--all', action='store_true',
                    help='every record, not only the open ones')
    args = ap.parse_args()

    token = ml.load_token()
    state = ml.load_state()
    items = state.get('items', {})

    todo = [(k, v) for k, v in items.items()
            if len(v.get('text') or '') >= OLD_CAP
            and (args.all or v.get('status') == 'open')]
    print(f'{len(todo)} record(s) to check '
          f'({"all statuses" if args.all else "open only"})')

    grown = failed = same = 0
    recovered = {}
    for key, rec in todo:
        channel, _, ts = key.partition(':')
        text, err = fetch_text(token, channel, ts, rec.get('thread_ts'))
        if err:
            failed += 1
            print(f'  SKIP {key} ({rec.get("channel_name")}): {err}')
            continue
        if len(text) <= len(rec.get('text') or ''):
            same += 1
            continue
        grown += 1
        print(f'  FULL {key} ({rec.get("channel_name")}): '
              f'{len(rec.get("text") or "")} -> {len(text)} chars')
        recovered[key] = ml.store_text(text)
        if args.apply:
            rec['text'] = recovered[key]
        time.sleep(0.4)          # conversations.history is tier 3, stay under it

    print(f'\n{grown} recovered, {same} already complete, {failed} unreadable')
    if args.apply and grown:
        # save_state merges against whatever is on disk, and the merge decides
        # which copy of the text to keep. It can only recognise a cut copy by
        # comparing the two strings, and the older sweep normalised what it
        # stored (bold marks dropped, link labels dropped), so some pairs do not
        # compare as prefixes however carefully the rule is written. This does
        # not have to guess: it just read these exact messages from Slack. So
        # merge first, to keep every record and every status the other machine
        # wrote, then put the authoritative text back on top and write.
        merged = ml.save_state(state)
        for key, text in recovered.items():
            if key in merged.get('items', {}):
                merged['items'][key]['text'] = text
        tmp = ml.STATE_PATH + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(merged, f, indent=1, ensure_ascii=False)
        os.replace(tmp, ml.STATE_PATH)
        print('ledger written')
    elif grown:
        print('dry run, nothing written. Re-run with --apply')

if __name__ == '__main__':
    main()
