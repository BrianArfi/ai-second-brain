---
name: Fathom Connector
description: A skill to interact with the Fathom AI API, allowing retrieval of meetings, recordings, and transcripts.
---

# Fathom Connector Skill

> **Preferred method**: Use the **MCP Fathom server** (`mcp__fathom__*` tools), configured in `~/.claude/settings.json`. It is faster, needs no timeout guards, and exposes meetings, transcripts, and summaries natively.
>
> Use this Python script only as a **fallback** when MCP is unavailable.

## Capabilities

1.  **List Meetings**: Retrieve a list of recent meetings recorded by the user.
2.  **Get Transcript**: Retrieve the full transcript for a specific meeting.
3.  **Search Recordings**: Find recordings by title or date.
- **Timeouts**: Scripts have a built-in **180-second global timeout**. Always wrap background calls in `timeout 180s` for safety.

## Usage (Fallback — Python Script)

The skill uses a helper script located at `.agent/skills/fathom-connector/scripts/fathom_client.py`.

### List Recent Meetings

```bash
timeout 180s python3 .agent/skills/fathom-connector/scripts/fathom_client.py --action list
```

### Get Transcript for a Meeting

```bash
timeout 180s python3 .agent/skills/fathom-connector/scripts/fathom_client.py --action transcript --id <MEETING_ID>
```

## Token Configuration

The script expects a `FATHOM_API_KEY` in `.agent/skills/fathom-connector/token.env`.
Format:
```env
FATHOM_API_KEY=your_api_key_here
```

The MCP server uses the same key, configured via the `Authorization: Bearer` header in `~/.claude/settings.json`.

## Sharing a recording with a person: never send the /calls/ link

`https://fathom.video/calls/<id>` is the **internal** link. It needs a Fathom account and a manual approval from the recording owner. Send it to somebody and you have not shared the recording, you have created an access request pointed at the owner.

That is exactly what happened on 31 August 2026. A `/calls/` link went into a Slack thread with ExampleVendor, and the next morning Teammate Meer was asking for access instead of watching the demo.

Every recording already carries a public `share_url` that opens with no account:

```bash
python3 .agent/skills/fathom-connector/scripts/fathom_client.py --action share-link --id https://fathom.video/calls/804588583
python3 .agent/skills/fathom-connector/scripts/fathom_client.py --action share-link --id 178180584   # recording id works too
```

It prints the share URL on stdout and the meeting title on stderr, so it composes: `--text "Recording: $(... --action share-link --id X 2>/dev/null)"`.

There is no per-recording endpoint (`/meetings/<id>` and `/recordings/<id>` both 404), so the lookup pages `/meetings` until it matches, then caches the answer in `journal/state/fathom_share_links.json`. A second lookup is instant, and `--refresh` re-queries. `--max-pages` (default 20, 50 recordings a page) bounds how far back it looks.

**This is enforced.** `.claude/hooks/send_slop_guard.py` **blocks** any Slack, Gmail or Google Docs comment send whose text carries a `fathom.video/calls/` link, and prints the share link from the cache in the refusal. Override with `SLOP_GUARD_ALLOW_FATHOM_CALLS=1` only when the recipient is already on the owner's Fathom team.
