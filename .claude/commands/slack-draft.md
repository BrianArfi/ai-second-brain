---
description: Draft a Slack message for the owner's review - NEVER sends without explicit approval ("kirim")
argument-hint: "<what to say and to whom / which channel>"
---

Slack message workflow (approval-gated):

1. Identify the target channel/DM. If ambiguous, ask the owner. For thread/channel context, read history via `python3 .agent/skills/slack-connector/scripts/slack_client.py` (read-only).
2. Draft the full message. Language: English for Work channels (match the thread's language if it differs). No em-dashes. When replying about a specific task, include the direct Slack permalink (see harness memory `feedback_slack_sending_playbook`).
3. Edit the draft against `.agent/skills/no-ai-slop/SKILL.md` and self-check it against that skill's `eval.md`. Do this in the main loop, before the reviewer runs.
4. Spawn the `draft-reviewer` subagent with: the draft, type "Slack", target channel, and audience. Fix any issues it raises before presenting.
5. Present to the owner: the final draft + target channel/DM + one-line reason for sending.
6. WAIT for explicit approval ("kirim", "send", "approve"). Do NOT send speculatively. Do NOT treat general agreement as send approval.
7. Only after approval: send via `slack_client.py --action post`, which uses the owner's user token (`SLACK_USER_TOKEN`, xoxp) by default so the message posts **as the owner** with no "Sent using @Claude" footer. Never use the MCP Slack send tools; those post as the Claude bot and add the footer.

   ```bash
   python3 .agent/skills/slack-connector/scripts/slack_client.py \
     --action post --channel <CHANNEL_ID> --text-file <path> --approved
   ```

   `--approved` is mandatory and is the only signal that the owner signed off on this specific draft. There is no environment-variable bypass, so add it only once approval is actually in hand. Add `--thread-ts <parent_ts>` for a thread reply. Prefer `--text-file` over `--text` on anything long, to avoid shell escaping.
8. Report the permalink the command prints on success (see harness memory `feedback_slack_sending_playbook`).

Request: $ARGUMENTS
