#!/usr/bin/env python3
"""Merge two versions of the Slack mention ledger without losing either.

`slack_mention_ledger.json` is a whole-document snapshot, and `save_state` used
to replace it outright from whatever the writing process held in memory. That is
safe while one machine writes and fatal the moment two do: the second write is
built on a picture that never contained the first machine's records, so it
deletes them, silently and with no conflict to notice.

That is not hypothetical here. CLAUDE.md records 17 Aug 2026, when two records
added on macOS at 11:53 were removed fourteen minutes later by a WSL sweep that
wrote back its own complete snapshot. `ledger_lock` cannot prevent it, because
`fcntl` covers one filesystem and these are two machines.

So a write stops being a replacement and becomes a merge. The rule that makes it
safe is that a merge may only **add or advance**:

* an item present on either side survives
* an item on both sides resolves to the more advanced of the two
* a watermark takes the larger value
* nothing is ever reverted to an earlier state

Deletion then needs its own answer, because a pure union can never forget
anything. It gets one that needs no coordination: retention is re-applied after
the union, using the same deterministic rule both machines already run in
`prune`. A terminal record past its retention window is dropped by whichever
machine writes next, and both agree on which records those are without talking.

The same function backs the git merge driver, so two commits that both touch this
file resolve by union instead of asking a person to pick a side, which is the
other way half the ledger disappears.
"""

import json

# Ranked so a merge can tell "more advanced" from "less". A record only ever
# moves up this list, never down, which is what makes the merge safe to apply in
# either direction.
STATUS_RANK = {'open': 0, 'dismissed': 1, 'answered': 2}

# Keys whose values are timestamps and merge by taking the later one.
MAX_SCALARS = ('search_watermark', 'last_sweep', 'last_push_ingest')

def _num(value):
    """Timestamps arrive as floats and sometimes as strings. Compare as numbers."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0

def _merge_item(a, b):
    """One item id present on both sides. The more advanced record wins."""
    rank_a = STATUS_RANK.get(a.get('status'), 0)
    rank_b = STATUS_RANK.get(b.get('status'), 0)
    if rank_a != rank_b:
        winner, loser = (a, b) if rank_a > rank_b else (b, a)
    else:
        # Same status. The one resolved later is the more recent truth, and when
        # neither is resolved it does not matter which body is kept.
        winner, loser = (a, b) if _num(a.get('answered_at')) >= _num(b.get('answered_at')) \
            else (b, a)

    merged = dict(winner)
    # Enrichment is expensive and one sided: whichever machine ran
    # `enrich_pointers` paid for a Slack call to resolve what a bare "^" meant.
    # Losing that because the other machine answered the item first would throw
    # the work away and leave a pointer nobody can read.
    for key in ('context', 'permalink', 'channel_name', 'thread_ts', 'text'):
        if not merged.get(key) and loser.get(key):
            merged[key] = loser[key]
    # The earliest sighting is the true one; a second machine seeing it later
    # does not make it newer.
    first_seen = [x.get('first_seen') for x in (a, b) if x.get('first_seen')]
    if first_seen:
        merged['first_seen'] = min(first_seen)
    merged['priority'] = bool(a.get('priority')) or bool(b.get('priority'))
    return merged

def merge_states(a, b, retention_days=14, now=None):
    """Union of two ledger states. Symmetric except for tie-breaks, which take `a`.

    `now` is injectable so the retention pass is testable without waiting a
    fortnight.
    """
    import time as _time
    now = _time.time() if now is None else now

    merged = dict(b)
    merged.update({k: v for k, v in a.items() if k not in merged})

    for key in MAX_SCALARS:
        if key in a or key in b:
            merged[key] = max(_num(a.get(key)), _num(b.get(key))) or None

    # Watermarks only ever move forward. Taking the smaller one would re-read a
    # window already swept, which is harmless, but taking the larger is correct
    # and cheaper.
    watermarks = dict(b.get('watermarks') or {})
    for cid, ts in (a.get('watermarks') or {}).items():
        watermarks[cid] = max(_num(ts), _num(watermarks.get(cid)))
    merged['watermarks'] = watermarks

    threads = dict(b.get('threads') or {})
    for tid, rec in (a.get('threads') or {}).items():
        if tid not in threads:
            threads[tid] = dict(rec)
            continue
        other = dict(threads[tid])
        other['last_seen_reply'] = max(_num(rec.get('last_seen_reply')),
                                       _num(other.get('last_seen_reply')))
        threads[tid] = other
    merged['threads'] = threads

    names = dict(b.get('channel_names') or {})
    for cid, name in (a.get('channel_names') or {}).items():
        if name:
            names.setdefault(cid, name)
    merged['channel_names'] = names

    items_a = a.get('items') or {}
    items_b = b.get('items') or {}
    items = {}
    for iid in set(items_a) | set(items_b):
        if iid in items_a and iid in items_b:
            items[iid] = _merge_item(items_a[iid], items_b[iid])
        else:
            items[iid] = dict(items_a.get(iid) or items_b[iid])
    merged['items'] = items

    _apply_retention(merged, retention_days, now)
    return merged

def _apply_retention(state, retention_days, now):
    """The only way a record leaves the ledger.

    A union cannot forget, so forgetting is re-derived instead of transmitted.
    Both machines run this same rule over the same data and reach the same set,
    which is why no coordination is needed to agree on a deletion.
    """
    cutoff = now - retention_days * 86400
    dead = [iid for iid, it in state['items'].items()
            if it.get('status') in ('answered', 'dismissed')
            and _num(it.get('answered_at') or it.get('first_seen')) < cutoff]
    for iid in dead:
        del state['items'][iid]

    live = {f"{it['channel']}:{it['thread_ts']}" for it in state['items'].values()
            if it.get('thread_ts') and it.get('status') == 'open'}
    for tid in [t for t in state.get('threads', {}) if t not in live]:
        state['threads'].pop(tid, None)

def merge_files(path_a, path_b, retention_days=14):
    """Read two ledger files and return the merged state."""
    def _load(path):
        with open(path) as f:
            return json.load(f)
    return merge_states(_load(path_a), _load(path_b), retention_days)
