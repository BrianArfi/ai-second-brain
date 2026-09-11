---
name: gdoc-comment
description: Post anchored comments on a native Google Doc by driving the real editor with Playwright. Use when a comment must attach to specific text and survive, because Drive API comments always orphan as "Original content deleted". Also verifies each posted comment carries a real kix anchor.
---

# gdoc-comment

Creates **anchored** comments on a Google Doc.

## Why it exists

The Drive and Docs APIs cannot mint the internal `kix.*` anchor that Google Docs
uses to attach a comment to a range of text. A comment created through the API
therefore orphans immediately and shows as "Original content deleted". Driving
the real editor mints a true anchor every time.

The script types through the editor: Find, select, `Ctrl+Alt+M`, type, post.

## Auth

A persistent Chromium profile per account, at `profile/<account>/`. Sign in once
per account and the session persists.

```bash
python3 .agent/skills/gdoc-comment/gdoc_comment.py login --account work
```

This opens a real window (WSLg on WSL). Accounts today: `work`, `personal`. It
reads the matching Drive token from `work-drive-connector` or
`personal-drive-connector` for the read-back check.

## Posting comments

`--items` takes a JSON list of `{"anchor": "<unique text in the doc>", "text": "<the comment>"}`.
The anchor must appear exactly once in the document.

```bash
python3 .agent/skills/gdoc-comment/gdoc_comment.py comment \
    --doc <DOC_ID> --account work --items items.json
```

Read the items from stdin with `--items -`. Add `--headed` to watch the browser
when something fails.

## Verification

After posting, it reads each comment back through the Drive API and confirms the
comment carries a `kix.*` anchor and the matching `quotedFileContent`. An anchor
of `None` means the comment orphaned, and the run reports that as a failure.

## Gates

The comment text is a message a named human reads, so it passes the no-ai-slop
gate first. `.claude/hooks/send_slop_guard.py` checks the `text` fields inside
`--items` on the way out: an em-dash blocks the send.
