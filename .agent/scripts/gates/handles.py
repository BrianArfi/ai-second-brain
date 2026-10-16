"""Slack handle gate: a named, resolvable person must carry a `<@ID>`.

Extracted from `.claude/hooks/send_slop_guard.py`. Slack posts a bare "Teammate" as plain
text, so Teammate gets no ping, no badge and no notification, and the message sits in the
channel until somebody scrolls past it. The people index lives in
`.agent/scripts/slack_mentions.py` (`Clients/Work/People/*.md` first, then the cached
`slack_user_names.json`).

The hook and `slack_client.py post` / `update` call the same functions, so the wording, the
decision and the override are one piece of code.

  one live account matches a name   -> refuse, and print the exact `<@ID>` to paste
  two live accounts match a name    -> warn only. The gate cannot pick the id, and a block
                                       with no correct fix is a dead end.
  index missing or unreadable       -> pass. A broken index must not stop a send.

Override: `SLOP_GUARD_ALLOW_PLAIN_NAMES=1`, the hook's existing one, for a person talked
ABOUT rather than addressed. On the command line the hook reads it from the command text;
a script reads it from the environment.

Unattended callers (the chase queue, the dashboard click-send) send drafts that name the
recipient in plain text ("Hi Teammate, ..."), and the thread or DM already addresses that
person. A hard block there would turn a cron send into a failure, so those callers set
this same override on the subprocess and print `warning_lines()` first: the same finding,
as a warning. The script then treats it as a deliberate bypass and stays silent.
"""
import os
import sys

from .outbound import env_allows, eprint

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
ENV_ALLOW_PLAIN_NAMES = 'SLOP_GUARD_ALLOW_PLAIN_NAMES'

def missing_handles(text):
    """People named in an outbound Slack message with no `<@ID>` behind the name.

    Returns (resolvable, ambiguous). Never raises: a broken index must not stop a send.
    """
    try:
        sys.path.insert(0, os.path.join(REPO_ROOT, '.agent', 'scripts'))
        from slack_mentions import unmentioned
        people = unmentioned(text)
    except Exception:
        return [], []
    return ([p for p in people if len(p['ids']) == 1],
            [p for p in people if len(p['ids']) > 1])

def _ambiguous_text(ambiguous, limit=4):
    return '; '.join(
        '%s (%s)' % (p['name'], ', '.join('<@%s>' % i for i in p['ids']))
        for p in ambiguous[:limit])

def handle_refusal(origin, resolvable, ambiguous=None):
    """Reason string when `resolvable` is non-empty, else None. Same wording as the hook."""
    if not resolvable:
        return None
    fixes = '; '.join('%s -> <@%s>' % (p['name'], p['ids'][0]) for p in resolvable[:8])
    reason = (
        'SLACK HANDLE GATE - the message about to be sent (%s) names %s '
        'without a Slack handle, so Slack posts the name as plain text and '
        'nobody gets a ping. Replace the first mention of each with: %s. '
        'Then re-run the send. Override with SLOP_GUARD_ALLOW_PLAIN_NAMES=1 '
        'when the person is being talked about rather than addressed. '
        'Resolve any other name with: python3 .agent/scripts/slack_mentions.py '
        'check --file <draft>'
    ) % (origin, ', '.join(p['name'] for p in resolvable), fixes)
    if ambiguous:
        reason += '. Ambiguous, pick by hand: ' + _ambiguous_text(ambiguous)
    return reason

def ambiguous_warning(ambiguous):
    """One-line warning for names that match two live accounts, else None."""
    if not ambiguous:
        return None
    return (
        'slack handles: %s match more than one live Slack account, so no handle was '
        'added and nobody gets a ping. Pick one by hand: %s. The People page for that '
        'person is the place to record the answer.'
    ) % (', '.join(p['name'] for p in ambiguous), _ambiguous_text(ambiguous))

def warning_lines(text, origin='slack post'):
    """The findings as warnings, never a block. For callers that bypass the gate on purpose."""
    try:
        resolvable, ambiguous = missing_handles(text)
    except Exception:
        return []
    lines = []
    if resolvable:
        lines.append(
            '[WARN] %s names %s without a Slack handle, so nobody gets a ping: %s' % (
                origin, ', '.join(p['name'] for p in resolvable),
                '; '.join('%s -> <@%s>' % (p['name'], p['ids'][0]) for p in resolvable[:8])))
    w = ambiguous_warning(ambiguous)
    if w:
        lines.append('[WARN] ' + w)
    return lines

def enforce(text, origin, env=None):
    """Script-side gate. Refuse (stderr, exit 1) before any network call, else return.

    Ambiguous names print a warning and pass. A gate bug lets the send continue, because
    the approval gate still stands behind it. A clear violation always stops.
    """
    try:
        if env_allows(ENV_ALLOW_PLAIN_NAMES, env) or not text:
            return
        resolvable, ambiguous = missing_handles(text)
        reason = handle_refusal(origin, resolvable, ambiguous)
        warn = None if reason else ambiguous_warning(ambiguous)
    except Exception:
        return
    if warn:
        eprint('[WARN] ' + warn)
    if not reason:
        return
    eprint('[BLOCKED] ' + reason)
    eprint('Nothing sent.')
    sys.exit(1)
