---
name: WhatsApp Connector
description: Read and draft WhatsApp messages through the local whatsapp-mcp bridge (MCP over stdio), never through browser automation. Sends are staged for out-of-band approval, never claimed as delivered.
---

# WhatsApp Connector Skill

This is the owner's personal infrastructure, not Work client work. It runs
outside this repo's client connectors and outside the ASB app.

The old route through `web.whatsapp.com` selectors and an antigravity
Chromium profile is dead. WhatsApp goes through the **whatsapp-mcp** server
over MCP (stdio), talking to a local REST bridge. There is no browser
automation left in this connector.

## How it's wired on this machine

- **Bridge**: a local Go process, `com.owner.wa-bridge`, managed by launchd,
  serving REST on `localhost:8080`. the owner set this up by hand at
  `~/wa-bridge`. (The ASB app manages its own separate copy of the same
  bridge on port `8181`. Different install, different port; do not
  conflate the two.)
- **MCP server**: `~/wa-bridge/whatsapp-mcp-server`, run via `uv`, talking
  to the bridge over stdio. Its config lives at `~/wa-bridge/.mcp.json` and
  is only live in a Claude Code session opened from `~/wa-bridge`. A
  session opened from this repo does not see these tools unless that
  config is active.
- **Credentials**: the WhatsApp account session lives under `~/wa-bridge/store/`
  and never leaves this machine. That is the whole account link; treat it
  like a password.

## Send mode

`WA_SEND_MODE` gates every send tool. Default on this machine is
**disabled** (read-only): send tools refuse outright. In **approval** mode,
a send tool does not deliver anything. It appends the draft to
`~/.local/share/whatsapp-mcp/outbox.jsonl` and returns "staged", and the owner
approves it out-of-band with `approve.py`, separate from this chat.

**Never say a message was sent, delivered, or received unless it actually
was.** "Staged" and "sent" are different states. If a send tool returns
"Draft staged", the true sentence is "Drafted and staged in the outbox,
waiting on your approval", never "sent" or "I've messaged them."

## Tool surface (17 tools)

**Contacts and chats** (read, ungated): `search_contacts`, `get_contact`,
`list_chats`, `get_chat`, `get_direct_chat_by_contact`, `get_contact_chats`,
`get_last_interaction`

**Messages** (read, ungated): `list_messages`, `get_message_context`,
`download_media`

**Send** (gated by `WA_SEND_MODE`): `send_message`, `send_file`,
`send_audio_message`, `send_reaction`

**Channels and status** (read, ungated): `list_channels`,
`get_channel_messages`, `list_status_updates`

## Usage guidelines

### Reading

Reads have no gate. Search contacts, list chats, pull history, check
channels and status freely when a task calls for it.

### Drafting and sending

1. Gather context first: `list_messages` or `get_last_interaction` on the
   relevant chat, so the draft answers what was actually said.
2. Write the draft in the recipient's language and the owner's voice. Run it
   through this repo's normal writing checks the same as any other message
   to a named person (see `.agent/skills/no-ai-slop/SKILL.md`).
3. Show the draft to the owner and wait for explicit approval.
4. Only after approval, call `send_message` (or `send_file` /
   `send_audio_message` / `send_reaction`). If `WA_SEND_MODE` is
   `disabled`, the call will refuse; say so plainly rather than retrying.
   If it is `approval`, report the result as staged in the outbox, not
   sent, and name where the owner confirms it (`approve.py`, out-of-band).

## Safety rules

- **Use a second number for testing**, never the owner's primary line, when
  trying anything new against this bridge.
- **Never claim a message was sent** when the tool only staged it. This is
  the same rule as `feedback-never-claim-unexecuted-action` in harness
  memory, applied to WhatsApp specifically.
- **`~/wa-bridge/store/` holds the full account session.** It is
  equivalent to being logged into the owner's WhatsApp. Never copy it, upload
  it, or expose it to any other tool or service.
- **Panic button**: on the phone, WhatsApp → Linked Devices → log out the
  linked session. That kills the bridge's access immediately, independent
  of anything on this machine.

## If the bridge or MCP server isn't running

Reads and sends will fail outright. Tell the owner the bridge or MCP
connection looks down rather than guessing at chat contents; this is
personal infrastructure he maintains by hand, not something this repo can
restart on its own.
