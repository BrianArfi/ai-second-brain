"""Reply-router conversation claim check for a Slack post.

Extracted from `.claude/hooks/conversation_claim_guard.py`. The router opens one session
per Slack conversation and records the owner in `journal/state/reply_router_state.json`.
On 20 Sep 2026 two sessions worked one thread (Teammate, banner out-of-stock fallback), one
filed ABC-123 and a draft, the other drafted a second reply and was about to file a
duplicate ticket.

`evaluate()` is pure: state in, decision out. The hook turns the decision into a
permissionDecision; `enforce_slack_post()` turns it into an exit for the Slack script.

  claimed by a DIFFERENT open session  -> deny
  recently closed as done              -> ask (script side: a warning, never a block)
  unclaimed or claimed by this session -> pass

Override: CLAIM_GUARD_DISABLE=1, the hook's existing kill switch, read from the
environment here.

The script learns who "this session" is from CLAUDE_CODE_SESSION_ID. Without it (cron,
agy-bridge, a member's setup) there is no session to compare, so the check is skipped:
the claim exists to stop one Claude session stepping on another.
"""
import json
import os
import sys
import time
from pathlib import Path

from .outbound import eprint

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
STATE = REPO_ROOT / 'journal' / 'state' / 'reply_router_state.json'
OPEN_STATUSES = ('open', 'claimed', 'active')
# A conversation closed longer ago than this is history, not a live duplicate.
DONE_WINDOW_SECONDS = 7 * 24 * 3600
ENV_DISABLE = 'CLAIM_GUARD_DISABLE'

def candidates(convs, channel, thread_ts):
    """Conversations this send could land in, most specific first."""
    found = []
    if thread_ts:
        key = '%s:t%s' % (channel, thread_ts)
        if key in convs:
            found.append((key, convs[key]))
    for key, conv in convs.items():
        if (key, conv) in found or not key.startswith(channel + ':'):
            continue
        if thread_ts:
            # A reply whose parent ts the router filed under a different key
            # (channel-level `:u<user>` conversations carry their messages here).
            msgs = conv.get('messages') or []
            if not any(len(m) > 1 and m[1] == thread_ts for m in msgs):
                continue
        elif key.startswith(channel + ':t'):
            # A top-level post cannot land inside a thread. Matching it against
            # every open thread in the channel blocked a new announcement on
            # 28 Sep 2026 because an unrelated six-day-old thread was claimed.
            continue
        elif conv.get('status') not in OPEN_STATUSES:
            # No thread-ts to disambiguate: only a live conversation is evidence.
            continue
        found.append((key, conv))
    return found

def load_conversations(state_path=None):
    path = Path(state_path or STATE)
    return (json.loads(path.read_text(encoding='utf-8')) or {}).get('conversations') or {}

def evaluate(convs, channel, thread_ts, me, now=None):
    """First applicable (decision, reason) for this send, or None for a clean pass."""
    now = time.time() if now is None else now
    for key, conv in candidates(convs, channel, thread_ts):
        owner = conv.get('session_id')
        status = conv.get('status')

        if status in OPEN_STATUSES and owner and owner != me:
            return ('deny', (
                'ANOTHER SESSION OWNS THIS CONVERSATION. The reply-router opened '
                '`%s` as session %s, and it is still open. Two sessions replying to one '
                'thread is how the same thread got two drafts and nearly two Jira tickets '
                'on 20 Sep 2026.\n'
                'Do this instead: open that session and send from there, or close it first '
                '(`reply_router.py close --conv \'%s\' --summary ...`). Check what it already '
                'produced before drafting anything new.\n'
                'Override for this call only: CLAIM_GUARD_DISABLE=1.'
                % (key, owner, key)
            ))

        if status == 'done':
            closed_at = conv.get('closed_at') or 0
            if now - closed_at < DONE_WINDOW_SECONDS:
                return ('ask', (
                    'This conversation is already CLOSED in the reply-router (`%s`, reason: %s).\n'
                    'What it was closed with: %s\n'
                    'Confirm this is a genuinely new message and not a duplicate of the reply '
                    'that session already sent.'
                    % (key, conv.get('closed_reason') or '?', conv.get('summary') or '(no summary)')
                ))
    return None

def enforce_slack_post(channel, thread_ts=None, env=None, state_path=None):
    """Script-side claim check. Exit 1 on a deny, warn on an ask, fail open on anything else."""
    env = os.environ if env is None else env
    try:
        if str(env.get(ENV_DISABLE, '')).strip() == '1':
            return
        me = str(env.get('CLAUDE_CODE_SESSION_ID') or '')
        if not me or not channel:
            return
        result = evaluate(load_conversations(state_path), channel, thread_ts, me)
    except Exception:
        return
    if not result:
        return
    decision, reason = result
    if decision == 'deny':
        eprint('[BLOCKED] ' + reason)
        eprint('Nothing sent.')
        sys.exit(1)
    eprint('[WARN] ' + reason)
