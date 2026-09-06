---
name: reply-queue
description: Auto-drafted Reply Queue - batch-drafts ready-to-approve Slack replies for open mention-ledger items in the owner's voice, via agy-bridge. Drafts only, never sends.
---

## Capabilities

- Reads OPEN items from the Slack mention ledger (`journal/state/slack_mention_ledger.json`, read-only) and batches them to `agy-bridge --task harvest` (GLM) to draft replies in the owner's voice: plain flowing prose, no emoji, no numbered/bolded lists, direct and warm-brief.
- Persists a drafted map (`journal/state/reply_queue.json`) keyed by ledger item id, storing `drafted_at`, `text_hash` (to detect when the underlying message/context changed and needs a re-draft), and the cached `draft_text`.
- Writes a briefing-ready markdown file per day: `journal/reply_drafts_<YYYY-MM-DD>.md`, headed with a hard "DRAFTS ONLY" warning. (Named `reply_drafts_*` deliberately: `journal/reply_queue_<date>.md` is the mention-ledger triage file convention and must never be clobbered by this skill.)
- Honors the agy-bridge fallback contract: rc 3 (or any non-zero/unparsable result) puts those items in a `## FALLBACK_TO_CLAUDE` section instead of silently dropping them - Claude should draft those directly in the SOP.
- Priority items (YourManager, etc., per the ledger's `priority` flag) always sort first; each entry includes the enriched context chain (`it['context']`) when the ledger attached one (e.g. a bare pointer message like "see above").

## Usage

```bash
# Draft (or re-draft changed) replies for up to 15 open ledger items, write today's queue file
python3 .agent/skills/reply-queue/scripts/reply_queue.py draft --limit 15

# Re-emit the current drafted queue as markdown (no LLM call) - for embedding in
# morning/evening updates
python3 .agent/skills/reply-queue/scripts/reply_queue.py report

# Include non-open (answered/dismissed) items too, e.g. for a full audit
python3 .agent/skills/reply-queue/scripts/reply_queue.py report --all
```

## After the queue is drafted: branch it

**Standing pre-approval, 3 Aug 2026. Branch directly, do not offer first.**

`journal/reply_drafts_<date>.md` hands the owner one file holding many unrelated replies, which
forces him to context-switch through all of them at once. Once the drafts are written, split
them:

- **One branch per counterpart or per thread**, whichever is coarser. Several mentions from
  the same person about the same thing are one session.
- **Only what needs the owner himself.** Items you already answered, and items still sitting in
  `## FALLBACK_TO_CLAUDE` waiting to be drafted, do not branch.
- **One item is not a branch.** Leave it here.
- Write ONE request file to `.asb/branches/requests/` covering every branch, per
  `## Branching Into Sub-Sessions` in `CLAUDE.md`, then say in one line per branch what you
  split.

Each `brief` carries the inbound message quoted, who sent it, the channel and permalink, the
drafted reply verbatim, and what you recommend. The sub-session starts blank and can not see
this queue.

**No send path here either.** Branching creates sessions, it never posts. Approving and
sending still goes through [`slack_send.md`](../../protocols/slack_send.md) with the owner's explicit approval inside the
sub-session.

## Notes

- **No send path.** This script contains zero Slack write calls (no `chat.postMessage`, no `--action post`, no MCP send tools). Approving and actually sending a reply always goes through [`slack_send.md`](../../protocols/slack_send.md) with the owner's explicit approval - this queue only prepares the draft text for review.
- Ledger is source of truth for open/answered state; this skill never mutates the ledger. It only reads `slack_mention_ledger.json`.
- `draft` skips re-calling the LLM for items whose `text_hash` hasn't changed since the last draft (cheap re-runs); it only spends tokens on new or changed items.
- On agy-bridge rc 3 (`fallback_to_claude`) or a non-zero/parse failure, the batch's items are NOT written into the drafted state - they show up in `## FALLBACK_TO_CLAUDE` in the output file every run until a human (Claude, in the SOP) drafts them or the underlying ledger item closes.
- Retention: drafted entries for items that are no longer `open` in the ledger (or no longer exist there at all) are pruned after 14 days.
- Cron: **none** - this is SOP-run only (morning/evening update workflow calls `draft` then embeds `report`'s output verbatim). LLM drafting work is intentionally kept out of cron per the plan's "LLM out of cron" rule.
- Voice constraints live in the LLM prompt itself (`VOICE_PROMPT_HEADER` in `reply_queue.py`): plain flowing prose, no emoji, no numbered-bold lists, no em-dashes, no parenthetical asides.
