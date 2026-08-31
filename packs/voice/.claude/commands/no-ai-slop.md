---
description: Edit a draft into sharper, more human writing, or detect AI-slop patterns without rewriting
argument-hint: "[paste a draft, or a path such as journal/reply_drafts_2026-08-10.md, or 'detect']"
---

Follow `.agent/skills/no-ai-slop/SKILL.md` exactly. That file is the source of truth for
the rules, the repo overrides, and the workflow.

Target: $ARGUMENTS

- **No argument.** Take the most recent drafted message in this session. If there is none,
  offer the pending drafts in `journal/` (`reply_drafts_*.md`, `slack_draft_*.md`) and ask
  which one.
- **A path.** Read the file and edit it. Show the owner the edited draft. Do not write over the
  file until he approves.
- **"detect", "audit", "scan", or "is this AI".** Run the detect job. Name each pattern,
  quote the line, give the fix in a few words. Do not rewrite.

This edits. It never sends. Slack, WhatsApp, Gmail, Drive, and anything client-facing still
wait for the owner's explicit approval.
