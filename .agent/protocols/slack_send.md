# Slack send protocol (approval-gated)

> Source of truth for ANY Slack message. Follow it whenever the owner asks for a Slack
> message, DM, or thread reply, whether or not he names this file.

Slack message workflow (approval-gated):

1. Identify the target channel/DM. If ambiguous, ask the owner. For thread/channel context, read history via `python3 .agent/skills/slack-connector/scripts/slack_client.py` (read-only).
2. Draft the full message, **in the owner's voice, from [`no-ai-slop/brian_voice.md`](../skills/no-ai-slop/brian_voice.md)**. Read it before writing the first line, not after. It is measured from 1,530 Slack messages he typed himself, and the headline is length: his median message is 61 characters and 75% are under 200, so a draft over 200 characters is already longer than three out of four things he has ever sent. One message, one thing. Lowercase is normal. Soften every ask with a sentence-final "ya", a question, or "right?". No heading, no table, no bold lead-in, no sign-off. Language: English for Work channels (match the thread's language if it differs), casual Indonesian with the Indonesian speakers. No em-dashes. When replying about a specific task, include the direct Slack permalink (see harness memory `feedback_slack_sending_playbook`).
3. Give every person named in the draft their handle. Slack posts a bare "Teammate" as text, so nobody gets a ping and the message waits until somebody scrolls past it. Run the check, then paste the ids it prints:

   ```bash
   python3 .agent/scripts/slack_mentions.py check --file <draft>   # names -> <@ID>
   python3 .agent/scripts/slack_mentions.py apply --file <draft> --in-place   # mention each person once
   ```

   **Then give every handle its name, and save the draft as `.md`.** The draft file goes to `journal/drafts/<name>_<YYYY-MM-DD>.md`, and every mention in it reads `<@<SLACK_ID>|Teammate Dev Singh>`, never the bare `<@<SLACK_ID>>`. the owner reads the draft on the page, away from Slack, where nothing renders an id for him, so a bare id hides who the message addresses and he cannot approve it. Slack renders the piped form as a real mention, so the ping is unchanged. `.txt` does not soft-wrap in the viewer; `.md` does, and `--text-file` reads either as raw text.

   ```bash
   python3 .agent/scripts/slack_mentions.py expand --file <draft> --in-place   # <@ID> -> <@ID|Name>
   ```

   `check` fails on a bare id and prints that command. Quoting somebody else's message back to the owner takes `expand --plain`, which writes `@Teammate Dev Singh`, because those ids belong to the message being shown rather than to anything being sent. Rule: harness memory `feedback-drafts-readable-md-named-handles`.

   `apply` mentions the FIRST occurrence of each person and leaves the rest as plain text, which is how a person writes. A name that matches two live accounts is left alone and reported, so pick the right id by hand and record it on that person's page in `Clients/Work/People/`. `send_slop_guard.py` refuses the send when a resolvable name still carries no handle; override with `SLOP_GUARD_ALLOW_PLAIN_NAMES=1` when the person is talked about rather than addressed.
4. Edit the draft against [`no-ai-slop/SKILL.md`](../skills/no-ai-slop/SKILL.md) and self-check it against that skill's `eval.md`. Then run the four-question voice test at the end of [`brian_voice.md`](../skills/no-ai-slop/brian_voice.md): under 200 characters, could it have been two messages, does the ask carry a softener, does it read as a status report. A no on any one means rewrite. Do all of this in the main loop, before the reviewer runs.
5. Spawn the `draft-reviewer` subagent with: the draft, type "Slack", target channel, and audience. Fix any issues it raises before presenting.
6. Present to the owner. **Any message that answers somebody uses the three-part reply-draft format, whatever produced it** (reply-router branch, `/sweep`, `/follow-ups`, a chase, or the owner asking by hand):

   1. **The original message**, quoted, with who sent it, when, and the permalink. The actual text, not a summary of it. Every `<@Uxxx>` inside it shows the person's name, through `slack_mentions.py expand --plain`. Quote it in full when it is short; when it is long, quote the part being answered and say what was cut. Several messages in the exchange means showing the chain, because the reply answers the sequence.
   2. **The draft**, exactly as it would be sent, with the target channel or DM named.
   3. **Pointers**: what is going on, and what the owner has to do or decide. Non-technical only, no file paths, no ledger ids, no commands. Send commands and technical detail go after this part, never inside it.

   A first message that answers nobody has no original, so it shows parts 2 and 3 only.

   the owner reviews replies away from the terminal with no thread open in front of him. A reply on its own gives him nothing to judge it against, so he has to go and find the original first, which is the work the draft was supposed to save. Rule: harness memory `feedback-reply-drafts-show-original-and-pointers`.

   **Never quote a truncated original.** The mention ledger holds the message text, and a cut copy reads as complete, so the reply answers half the ask and nothing on the page shows it. If the quote ends mid-sentence, pull the real text before drafting:

   ```bash
   python3 .agent/skills/slack-connector/scripts/slack_client.py --action history --channel <CHANNEL_ID> --limit 20
   ```
7. WAIT for explicit approval ("kirim", "send", "approve"). Do NOT send speculatively. Do NOT treat general agreement as send approval.
8. Only after approval: send via `slack_client.py --action post`, which uses the owner's user token (`SLACK_USER_TOKEN`, xoxp) by default so the message posts **as the owner** with no "Sent using @Claude" footer. Never use the MCP Slack send tools; those post as the Claude bot and add the footer.

   ```bash
   python3 .agent/skills/slack-connector/scripts/slack_client.py \
     --action post --channel <CHANNEL_ID> --text-file <path> --approved
   ```

   `--approved` is mandatory and is the only signal that the owner signed off on this specific draft. There is no environment-variable bypass, so add it only once approval is actually in hand. Add `--thread-ts <parent_ts>` for a thread reply. Prefer `--text-file` over `--text` on anything long, to avoid shell escaping.
9. Report the permalink the command prints on success (see harness memory `feedback_slack_sending_playbook`).

