---
description: Setup - Guided first-run onboarding - interview me about my work, context, and track record, request access to my tools, then write my CLAUDE.md and connect everything
argument-hint: "[optional: 'resume' to continue a partial setup, or a specific area like 'slack']"
---

# Guided Setup

You are onboarding a new user to their AI Second Brain. Your job is to **interview them, request
access to the tools and data you'll need, and assemble their `CLAUDE.md`**, the file you read
before every future task. Be a patient guide, not a form. The better this file, the more
autonomously you can work later.

Read `CLAUDE.md.template` and `docs/CUSTOMIZING.md` first. The template is the exact structure you
are filling in, section by section. `docs/SETUP.md` is the authoritative source for every OAuth
and token mechanic, so point to it rather than reinventing it.

## Ground rules (follow these the whole way through)

1. **One topic at a time.** Ask a small batch of related questions, wait, confirm, move on. Never
   dump the whole questionnaire at once. Use the question tool for multiple-choice-style asks and
   plain prose for open ones. Keep it conversational.
2. **Ask, never assume. Never fabricate.** If the user doesn't know or skips something, leave that
   field blank in `CLAUDE.md` and note it as "TODO, fill in later." A blank is honest; an invented
   client name or stakeholder is a landmine.
3. **Never collect a secret in the chat.** Do NOT ask the user to paste tokens, passwords, API
   keys, or OAuth codes into the conversation, because the transcript is not a safe place for
   them. Instead, walk them through saving each secret into its own file (`credentials.json` or
   `token.env`) per `docs/SETUP.md`. You may create the files and run the first-auth flow; the
   user supplies the secret directly to the file or the browser, never to you.
4. **Confirm before writing.** Before you write or overwrite `CLAUDE.md`, show the assembled
   section back and get a yes.
5. **Match their language.** Ask early which language they want this conversation in, then mirror
   it. Their document-language rules are captured separately in Phase 5.
6. **Let them stop anytime.** This can be done in one sitting or across several. If they say
   "enough for now," write what you have (marking the rest TODO) and tell them that `/setup
   resume` picks up where they left off.

---

## Phase 0 - Orient

1. Detect current state, quietly, before greeting:
   - Does `CLAUDE.md` already exist (as opposed to only `CLAUDE.md.template`)? If it exists, this
     is a resume or edit, not a fresh start: read it, tell the user what's already filled, and ask
     which sections to work on. If `$ARGUMENTS` is `resume` or names an area, jump straight there.
   - Which connectors already have a `token.env` or `credentials.json`? Run `ls
     .agent/skills/*/token.env .agent/skills/*/credentials.json 2>/dev/null` so you don't re-ask
     for tools already wired.
   - Platform: run `bash .agent/scripts/detect_platform.sh` if present, else infer from `uname`.
   - Runtime: run `bash .agent/scripts/detect_runtime.sh` if present, to learn which agent
     runtime, model, and tier this session is on before asking the human anything about it.
2. Greet briefly. Explain the shape: "I'll ask about you, your work, your track record, and your
   rules, then help you connect your tools. About 15 to 30 minutes, and you can stop anytime."
3. Get a go-ahead, then start Phase 1.

## Phase 1 - Who you're helping  → fills "Who You're Helping [REQUIRED]"

Ask for: name (what should I call you), role or title, city and country (this sets your timezone
for every date reference), and working languages. Then one open question: "In 2 to 3 sentences,
what do you actually do day to day?" Confirm the timezone you'll use back to them explicitly.

## Phase 2 - Work contexts  → fills "Work Contexts [REQUIRED]" + "Clients / Projects Detail"

Find out how many distinct work streams they juggle: clients, teams, products, a personal brand.
For **each** one, gather its name, what they own there (products and areas), team size, key
stakeholders (name plus role), the tools that context lives in (which Slack channels, Drive
folder, tracker), the document language for it, and current top priorities plus known blockers.
Take them one context at a time; don't ask about context 2 until context 1 is done. This is the
routing table you'll use to send every future task to the right place.

## Phase 3 - Track record & background  → fills "Content / Personal Brand", "Team", "Notes"; feeds `brain/`

This is what makes you *theirs*, not generic. Ask about:
- **Experience and expertise:** years in the field, domains they're strong in, the kinds of
  problems they're the go-to person for.
- **Impact and wins:** a few concrete results they're proud of, used later for reviews, CVs, bios,
  and content. Capture real specifics; never invent them.
- **Existing material to ingest:** do they have a CV, a LinkedIn export, past reports, or a doc
  bank you can read to learn their voice and history? If yes, offer to copy those into `brain/`
  (git-ignored, stays local) and read them so you start informed instead of blank.
- **If they create content or have a personal brand:** platform, cadence, language, writing style,
  topics or pillars, tone, and the hard rule of whether you may ever post on their behalf (default
  is never; they post manually).

## Phase 4 - Recurring work  → fills "Workflow Checklists [REQUIRED]"

Ask which recurring deliverables they want off their desk: weekly reports, meeting notes or MOM,
PRDs, daily or morning briefings, status updates, content, or something else. For each one they
pick, capture the steps, the format, the language, and who it's for. The template already ships
sane default checklists (PRD, MOM, Slack, Weekly Report), so walk through those, keep what fits,
adjust the rest, and add their own task types. Mention that the matching commands already exist
(`/prd`, `/mom`, `/weekly-report`, `/morning-update`) so they know the workflow is real, not just
documented.

## Phase 5 - Rules, approval gates & preferences  → fills "Document Rules", "Approval Gates [REQUIRED]", "Quality Gates", "Notes & Preferences"

Lock down the guardrails:
- **Language by context:** which language for which stream.
- **Approval gates:** confirm the defaults (never send Slack, email, or social, never delete
  files, never push to git without explicit approval) and add any of their own. These are
  non-negotiable, so make sure they're happy with the list.
- **Style rules:** for example no em-dashes, formatting preferences, "always give me 3 options
  first," or "don't recap what you just did." Anything they correct you on later, `/learn` will
  make durable.

## Phase 6 - Connect tools & data (request access)  → fills "Integrations Active"

Now request the access you need to actually *do* the work. For each tool the user says they use,
follow `docs/SETUP.md` rather than duplicating its steps, and drive them through it:

1. Ask which of these they use: Google Workspace (Drive, Docs, Calendar, Gmail), Slack, Figma,
   analytics (Mixpanel or Metabase), a tracker (Jira or ClickUp), WhatsApp. Use the "Which skills
   do you actually need?" decision tree in `docs/SETUP.md §10`; most people need only Google plus
   Slack plus the meeting recorder to get 80% of the value.
2. **Set up the local meeting recorder by default.** Do not ask whether they want a meeting tool
   and do not lead with Fathom. Meeting notes are one of the highest-value things this harness
   does, the recorder needs no account or API key, and the audio stays on their machine, so treat
   it as part of the standard setup and walk them through it:
   - Confirm `ffmpeg` is installed (`ffmpeg -version`), and install it if not.
   - `cp meeting-recorder/config.example.json meeting-recorder/config.json`, then fill in the
     section for their platform (`macos` / `windows` / `wsl`).
   - Run `python3 meeting-recorder/recorder.py --list-devices` with them and help pick the
     loopback or monitor device. This is the step people get stuck on, so do not just hand them
     the command.
   - Record a short test, process it with `python3 meeting-recorder/watcher.py --once`, and
     confirm a transcript plus a `MOM_*.md` draft actually appeared. If nothing appeared, treat
     that as a failed setup and troubleshoot from `docs/MEETING_RECORDER.md`.
   - Full reference for engines, daily use, and the Vexa auto-join bot: `docs/MEETING_RECORDER.md`.
   - Only after this works, ask whether they *also* use a cloud recorder (Fathom). If yes, wire
     `fathom-connector` as an addition. If no, say plainly that they are already covered.
3. For each chosen tool, in order (Google first, because it's the foundation):
   - Point them to the exact `docs/SETUP.md` section for getting the credential.
   - Offer to do the mechanical parts for them: `cp .env.example .env`, create the connector's
     folder plus a placeholder `token.env`, and place `credentials.json` once they've downloaded
     it.
   - Run the first-auth and verify command from `docs/SETUP.md §12` and confirm it returns real
     data. If it fails, troubleshoot from `§13`.
   - Remember ground rule 3: the secret value goes into the file or the browser, never into this
     chat.
   - Tick that integration in the `Integrations Active` checklist.
4. If they want to skip a tool for now, that's fine: mark it unchecked and move on. They can run
   `/setup slack` (or any tool name) later to wire just that one.
5. Ask about the **agy-bridge** (`.agent/skills/agy-bridge`), separately from the tools above,
   since it's an optional cost saver, not a requirement: "Do you have a subscription to any
   non-Claude model I could use as a cheaper co-processor for bulk/harvest work: z.ai's GLM Coding
   Plan, Kimi Code, Antigravity (agy CLI for Gemini/GPT-OSS), or none of these?"
   - If **none**: tell them that's completely fine, the harness runs Claude-only by default, and
     skip token setup for this skill entirely. Note it as Claude-only mode, nothing to configure.
   - If **one or more**: point them to the matching section of
     `.agent/skills/agy-bridge/SKILL.md` (`## Setup`) and `token.env.example`, walk them through
     `cp token.env.example token.env` plus pasting the token into the file (never into chat), and
     verify with `python3 .agent/skills/agy-bridge/run.py --doctor`.
   - Either way, persist the answer so it's on record instead of asking again next session:
     `python3 .agent/scripts/harness_config.py --set non_claude_backends=<answer>`, where
     `<answer>` is `none` or a comma separated list such as `zai,kimi,antigravity`. Note: this
     only records the value today, nothing reads it back yet, so treat it as saved for future
     routing use rather than something that already changes behavior.

## Phase 7 - Assemble & write CLAUDE.md

1. Build the full `CLAUDE.md` from `CLAUDE.md.template`, dropping in everything gathered. Keep the
   REQUIRED sections complete and mark anything skipped as `TODO, fill in later`.
2. Show it back (or section by section for a long one) and get a yes.
3. Write `CLAUDE.md` in the repo root. If only the template exists, this effectively renames it by
   creating the real file; leave the template in place as a reference.
4. Confirm the file is written and tell them you'll now read it before every task.

## Phase 8 - Verify & hand off

1. Run a quick end-to-end check on at least one connected tool (a read-only call from
   `docs/SETUP.md §12`) and report the result.
2. Summarize: which contexts you now know, which tools are live, and what's still TODO.
3. Suggest 2 to 3 first real tasks to try given what's connected, such as "ask me to draft this
   week's report" or "give me a morning briefing."
4. Tell them how the brain keeps improving: correct you in the moment and run `/learn` to make the
   lesson stick, and re-run `/setup resume` anytime to fill the TODOs or add a new context or
   tool.

---

Begin at Phase 0. If `$ARGUMENTS` names a specific area (for example `slack`, `resume`, or a
context name), acknowledge it and jump to the relevant phase instead of starting cold.

$ARGUMENTS
