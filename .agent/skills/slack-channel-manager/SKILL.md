---
name: slack-channel-manager
description: Keeps a per-client whitelist of Slack channels so sweeps read the channels that matter and ignore the rest. Use when adding a client, onboarding a channel, or when a sweep is pulling noise.
---

# Slack Channel Manager Skill

## Description
This skill manages a curated whitelist of Slack channels per client (Work, Secondary, etc.) to ensure that Antigravity only monitors and retrieves updates from relevant channels. This prevents noise and ensures high-level project visibility.

## Components
- `channels.json`: The source of truth for whitelisted channels.
- `scripts/manage_channels.py`: Tool to add or remove channels from the whitelist.
- `scripts/get_channel_updates.py`: Utility to fetch recent messages from all whitelisted channels for a specific client.

## Usage

### 1. Retrieve Whitelisted Channels
To see which channels are currently being tracked for a client:
```bash
python3 .agent/skills/slack-channel-manager/scripts/manage_channels.py --action list --client work
```

### 2. Add a Channel
When the user mentions a new channel to follow:
```bash
python3 .agent/skills/slack-channel-manager/scripts/manage_channels.py --action add --client work --channel_id C12345 --channel_name "#new-channel"
```

### 3. Remove a Channel
Only when explicitly requested by the user:
```bash
python3 .agent/skills/slack-channel-manager/scripts/manage_channels.py --action remove --client work --channel_id C12345
```

### 4. Get High-Level Updates
To scan all relevant channels for today's summary:
```bash
python3 .agent/skills/slack-channel-manager/scripts/get_channel_updates.py --client work --days 1
```

## Maintenance
- This file should be updated whenever the user's focus shifts or new teams/projects are added.
- Always prefer channel IDs over names to avoid issues with renaming.
