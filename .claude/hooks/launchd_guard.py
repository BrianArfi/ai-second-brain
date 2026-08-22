#!/usr/bin/env python3
"""PreToolUse hook on Bash: block `launchctl submit`, which silently sets KeepAlive.

What it prevents. `launchctl submit -l LABEL -- cmd` is the obvious way to hand a
script to launchd so it survives the process that scheduled it. It is also a trap:
submitted jobs carry KeepAlive, so launchd restarts the script every time it exits.
A one-shot script becomes an infinite loop, and there is nothing in the command that
says so.

It happened twice on 22 Aug 2026, both times to the ASB app installer
(`~/asb-artifacts/install.sh`, scheduled as `asb-install` then `asb-install-2`).
Each run quit the app, backed up the bundle, replaced it, and reopened it. launchd
then ran it again, 11 seconds later, 87 times: the app blinked on and off for
sixteen minutes across the two episodes, and the backups reached 7 GB. Neither job
was ever written to disk, so nothing on the filesystem showed the cause. The only
way to see it was `launchctl print gui/$UID/<label>`.

The replacement is a plist with RunAtLoad true and KeepAlive false, bootstrapped
with `launchctl bootstrap gui/$UID <plist>`. For the ASB installer that is
`~/asb-artifacts/schedule_install.sh`.

Severity: deny. This repo runs defaultMode bypassPermissions, so "ask" never
reaches a prompt and the command runs anyway. Escape hatch, for the rare job that
genuinely wants restart-on-exit: prefix the command with LAUNCHD_GUARD_ALLOW=1.

Contract: always exit 0. A crash here must never break a shell call.
"""
import json
import re
import sys

# `launchctl submit`, allowing flags and whitespace between the two words, but only
# where a command can actually start: beginning of the line, or after a separator
# (`;` `&&` `||` `|` `(` newline) or a command substitution. A commit message that
# merely quotes the phrase is not a command, and blocking it would be a false
# positive of exactly the kind `ledger_watch.py` had to be fixed for. Env-var
# prefixes are still commands, so they still count.
SUBMIT = re.compile(
    r'(?:^|[;&|(\n`]|\$\()\s*'          # command position
    r'(?:\w+=\S*\s+)*'                    # optional env-var prefixes
    r'(?:sudo\s+)?'
    r'launchctl\s+(?:-\S+\s+)*submit\b'
)

REASON = (
    'Blocked: `launchctl submit` sets KeepAlive, so launchd restarts the command '
    'every time it exits. A one-shot script becomes an infinite loop. This is what '
    'reinstalled the ASB app 87 times on 22 Aug 2026.\n\n'
    'Use a plist with RunAtLoad true and KeepAlive false instead:\n'
    '  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/<label>.plist\n\n'
    'For the ASB installer, the wrapper already exists:\n'
    '  bash ~/asb-artifacts/schedule_install.sh\n\n'
    'If restart-on-exit is genuinely wanted, prefix with LAUNCHD_GUARD_ALLOW=1.'
)

def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    if payload.get('tool_name') != 'Bash':
        sys.exit(0)

    command = payload.get('tool_input', {}).get('command', '') or ''
    if 'LAUNCHD_GUARD_ALLOW=1' in command:
        sys.exit(0)
    if not SUBMIT.search(command):
        sys.exit(0)

    print(json.dumps({
        'hookSpecificOutput': {
            'hookEventName': 'PreToolUse',
            'permissionDecision': 'deny',
            'permissionDecisionReason': REASON,
        }
    }))
    sys.exit(0)

if __name__ == '__main__':
    try:
        main()
    except Exception:
        sys.exit(0)   # never break a shell call on a guard bug
