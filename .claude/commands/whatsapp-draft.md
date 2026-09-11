---
description: Comms - Draft a WhatsApp message for the user's approval - never sends without it
---

Draft a WhatsApp message to a specific person or chat, following the same
approval discipline as any other outbound message. See **WhatsApp** in
`CLAUDE.md` for the full rules; this command just walks through them.

1. **Find the right chat.** Use `search_contacts` or `list_chats` to find
   the person or group. If more than one match is close, show the
   candidates and ask which one instead of guessing.

2. **Read before writing.** Pull recent context with `list_messages` or
   `get_last_interaction` on that chat so the draft answers what was
   actually said, not what the user is assumed to mean. Skip this only for
   a message that opens a new thread with no history to read.

3. **Write the draft** in the language the chat has been using, in the
   user's own voice. Keep it to the length a WhatsApp message actually is:
   short, no email formatting, no headers.

4. **Show the draft plainly** before touching any send tool: who it is to,
   which chat, and the full text. Wait for the user to approve it, change
   it, or say no. Never call `send_message` on an unapproved draft.

5. **Once approved, call `send_message`.** This stages the draft to the
   app's approval queue; it does not deliver it. Tell the user exactly
   that: "Staged, awaiting your approval in Settings → Connected tools."
   Do not say it was sent. (Exception: if the user has switched the
   WhatsApp card to direct mode, the tool delivers the message itself;
   report it as sent only after the tool returns success.)

6. **If the user asks to attach a file, a voice note, or a reaction**, use
   `send_file`, `send_audio_message`, or `send_reaction` the same way:
   draft or describe what will go out, get approval, call the tool, then
   report it as staged, not sent.

If WhatsApp is not connected yet, point at the **WhatsApp** card in
**Settings, then "Connected tools"** and stop there; do not invent what the
chat history would have said.
