---
name: Slack Connector
description: A skill to interact with Slack, allowing listing channels and reading message history.
---

# Slack Connector Skill

This skill allows the agent to interact with a Slack workspace using a Bot Token.

## Capabilities

1.  **List Channels**: Retrieve a list of public channels in the workspace.
2.  **Read History**: Retrieve message history from a specific channel.

## Prerequisites

-   A Slack Bot Token (starting with `xoxb-`) with the following scopes:
    -   `channels:read`
    -   `channels:history`
    -   (Optional) `groups:read`, `groups:history`, `im:read`, `im:history`, `mpim:read`, `mpim:history` for private channels/DMs.
-   Python 3 installed.
-   `requests` library (`pip install requests`).
-   **Timeouts**: The script has a built-in **180-second global timeout**. Always wrap background calls in `timeout 180s` for safety.

## Usage

The skill uses a helper script located at `scripts/slack_client.py`.

### List Channels

```bash
timeout 180s python3 .agent/skills/slack-connector/scripts/slack_client.py --action list_channels --token <YOUR_SLACK_TOKEN>
```

### Read Channel History

```bash
timeout 180s python3 .agent/skills/slack-connector/scripts/slack_client.py --action history --channel <CHANNEL_ID> --token <YOUR_SLACK_TOKEN> [--replies]
```
Use `--replies` to fetch thread replies for each message.

You can also set the `SLACK_BOT_TOKEN` environment variable to avoid passing `--token` every time.

```bash
export SLACK_BOT_TOKEN="xoxb-..."
python .agent/skills/slack-connector/scripts/slack_client.py --action list_channels
```

### Send Approval Gate

`post`, `upload`, `update`, `delete`, and `invite` mutate outbound Slack state
(they post, edit, or delete messages, post files, or add channel members), so
they are default-blocked. The gate itself
lives in `.agent/scripts/file_utils.py` as `require_send_approval()` and is
imported by both this connector and `secondary-slack-connector`, so there is one
implementation and no ungated twin.

The command refuses before touching the network unless `--approved` is passed,
and approval is per message: pass it only once the owner has explicitly signed off on
that specific draft.

```bash
python3 .agent/skills/slack-connector/scripts/slack_client.py --action post \
  --channel <CHANNEL_ID> --text "Your message" --approved
```

There is deliberately no environment escape hatch. An env flag would be
process-wide and permanent, so exporting it once into a shell or cron
environment would un-gate every later send in that process tree, which is the
opposite of the per-message approval the rule requires. Unattended callers pass
approval explicitly at their own call site; `dashboard/server.py` does this on
the click path.

`token.env` supplies credentials only. The loader refuses any key that would
relax the gate, so a credentials file can never grant send authorization.

Without `--approved`, the command exits nonzero. Never retry a refused send by
adding `--approved` on a guess; only pass it once the owner has actually approved
the message.
