# Slack send protocol (approval-gated)

> Source of truth for ANY Slack message. Follow it whenever the owner asks for a Slack
> message, DM, or thread reply, whether or not he names this file.

Slack message workflow (approval-gated):

1. Identify the target channel/DM. If ambiguous, ask the owner. For thread/channel context, read history via `python3 .agent/skills/slack-connector/scripts/slack_client.py` (read-only).
2. Draft the full message. Language: English for Work channels (match the thread's language if it differs). No em-dashes. When replying about a specific task, include the direct Slack permalink (see harness memory `feedback_slack_sending_playbook`).
3. Give every person named in the draft their handle. Slack posts a bare "Teammate" as text, so nobody gets a ping and the message waits until somebody scrolls past it. Run the check, then paste the ids it prints:

   ```bash
   python3 .agent/scripts/slack_mentions.py check --file <draft>   # names -> <@ID>
   python3 .agent/scripts/slack_mentions.py apply --file <draft> --in-place   # mention each person once
   ```

   `apply` mentions the FIRST occurrence of each person and leaves the rest as plain text, which is how a person writes. A name that matches two live accounts is left alone and reported, so pick the right id by hand and record it on that person's page in `Clients/Work/People/`. `send_slop_guard.py` refuses the send when a resolvable name still carries no handle; override with `SLOP_GUARD_ALLOW_PLAIN_NAMES=1` when the person is talked about rather than addressed.
4. Edit the draft against [`no-ai-slop/SKILL.md`](../skills/no-ai-slop/SKILL.md) and self-check it against that skill's `eval.md`. Do this in the main loop, before the reviewer runs.
5. Spawn the `draft-reviewer` subagent with: the draft, type "Slack", target channel, and audience. Fix any issues it raises before presenting.
6. Present to the owner: the final draft + target channel/DM + one-line reason for sending.
7. WAIT for explicit approval ("kirim", "send", "approve"). Do NOT send speculatively. Do NOT treat general agreement as send approval.
8. Only after approval: send via `slack_client.py --action post`, which uses the owner's user token (`SLACK_USER_TOKEN`, xoxp) by default so the message posts **as the owner** with no "Sent using @Claude" footer. Never use the MCP Slack send tools; those post as the Claude bot and add the footer.

   ```bash
   python3 .agent/skills/slack-connector/scripts/slack_client.py \
     --action post --channel <CHANNEL_ID> --text-file <path> --approved
   ```

   `--approved` is mandatory and is the only signal that the owner signed off on this specific draft. There is no environment-variable bypass, so add it only once approval is actually in hand. Add `--thread-ts <parent_ts>` for a thread reply. Prefer `--text-file` over `--text` on anything long, to avoid shell escaping.
9. Report the permalink the command prints on success (see harness memory `feedback_slack_sending_playbook`).

