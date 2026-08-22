---
name: gmail-connector
description: Reads, searches, and organizes Gmail through the Gmail API. Use to sweep the inbox, read a thread in full, or archive. Sending stays approval-gated.
---

# Gmail Connector Skill

Manage the owner's Gmail account (connected to `you@yourcompany.com`) for reading, searching, and organizing emails.

## Overview
This skill allows the assistant to interact with the Gmail API. It supports fetching recent emails, searching using standard Gmail queries, reading full content, and archiving messages.

## Tools

### `profile`
Get the user's Gmail profile (email address and message counts).
- **Command**: `python .agent/skills/gmail-connector/gmail_manager.py profile`

### `list-emails`
List recent emails or search with a query.
- **Command**: `python .agent/skills/gmail-connector/gmail_manager.py list`
- **Arguments**:
  - `--query`: Gmail search string (e.g., `from:work`, `is:unread`, `subject:urgent`).
  - `--limit`: Number of results to return (default 10).

### `get-email`
Retrieve the full content of a specific email.
- **Command**: `python .agent/skills/gmail-connector/gmail_manager.py get <msg_id>`

### `archive-email`
Move an email from Inbox to Archive.
- **Command**: `python .agent/skills/gmail-connector/gmail_manager.py archive <msg_id>`

### `send`
Send a plain-text email from `you@yourcompany.com` (appears in Sent). The `gmail.modify` scope already permits sending. Always confirm with the owner before sending.
- **Command**:
  ```bash
  python .agent/skills/gmail-connector/gmail_manager.py send \
    --to "teammate@yourcompany.com" \
    --cc "teammate@yourcompany.com, other@yourcompany.com" \
    --subject "Subject" \
    --body-file /tmp/body.txt
  ```
- **Arguments**:
  - `--to`: recipient(s), comma-separated (required).
  - `--cc`: cc recipient(s), comma-separated (optional).
  - `--subject`: subject line (required **unless** `--reply-to` is used).
  - `--reply-to`: Gmail message id to reply INTO. The new message inherits that message's `threadId`, subject, and `In-Reply-To`/`References` chain, so Gmail and Outlook both keep it inside the existing thread. `--subject` is ignored in this mode.
  - `--body` OR `--body-file`: inline text, or a file with the body (use `--body-file` for multi-line to avoid shell escaping).
  - `--attach`: path to a file to attach. Repeat the flag once per file. MIME type is guessed from the extension; a missing path or a total over Gmail's 35 MB message limit fails **before** any network call.
    ```bash
    python .agent/skills/gmail-connector/gmail_manager.py send \
      --to "you@yourcompany.com" \
      --subject "Travel documents" \
      --body-file /tmp/body.txt \
      --attach /tmp/eticket.pdf --attach /tmp/voucher.pdf
    ```

- **Threading gotcha**: without `--reply-to`, Gmail starts a **new** thread even when the subject matches the original character for character. Any reply on a live client thread must pass `--reply-to <last_message_id>`, or the client sees a second, orphaned thread. To reply-all, copy the To/Cc set off the message you are replying to (`get <msg_id>` prints them) and pass them explicitly; the script does not expand reply-all for you.
  ```bash
  python .agent/skills/gmail-connector/gmail_manager.py send \
    --reply-to <last_message_id> \
    --to "someone@example.com" \
    --cc "other@example.com,third@example.com" \
    --body-file /tmp/body.txt
  ```

## Setup & Authentication
The skill uses the Work project's `credentials.json` (located in `.agent/skills/work-drive-connector/`) and saves `token_gmail_work.json` in the skill directory.

**Interactive (WSL/terminal with stdin):** Run any command; it triggers an OAuth flow. Copy the `code` from the browser address bar and paste it when prompted.

**Headless (two-step, for non-interactive shells):**
1. `python .agent/skills/gmail-connector/gmail_manager.py auth-url` → open the printed URL signed in as `you@yourcompany.com`, approve, copy the `code=` value from the (failed-to-load) `localhost:8080` redirect URL.
2. `python .agent/skills/gmail-connector/gmail_manager.py auth-save --code "PASTE_CODE_HERE"` → saves the token.

If a send fails with "Gmail API has not been used in project ... or it is disabled," enable the Gmail API once in the Google Cloud project tied to the Work OAuth client, then retry.

## Best Practices
- **Privacy**: Only read emails that are relevant to the current task.
- **Filtering**: Use queries to minimize the amount of data processed.
- **Organization**: Archive emails once they have been converted into tasks or processed.
