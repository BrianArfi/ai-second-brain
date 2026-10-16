"""Outbound-action gates shared by the Claude Code hooks and the sending scripts.

Why this package exists (Grok Bot lessons plan, B4, 15 Oct 2026). A gate that lives
only in a `.claude/hooks/` file holds while Claude Code is the harness and nothing
else. Run the same send under agy-bridge, opencode, a cron job, or a member's setup,
and the hook never fires. The check has to sit in the script that does the action,
the way `--approved` already does.

The logic is written once, here. The hook calls it on the command line it is about
to run; the script calls it on the text it is about to send. Same function, same
wording, same override.

  gates.outbound   em-dash / en-dash and internal Fathom link checks on message text
  gates.claims     the reply-router conversation claim check for a Slack post
  gates.handles    a named, resolvable person in a Slack post must carry a <@ID>

Rules every caller follows:
  - Run BEFORE the first network call.
  - A bypass mirrors the hook's existing override (an environment variable set on the
    command, e.g. `SLOP_GUARD_ALLOW_EMDASH=1 python3 ...`). No new silent bypass.
  - Fail open on a gate bug or unreadable state, never on a clear violation.
"""
