# ASB app skill review: what belongs to the app, what belongs to the workspace

Source of the list: `SKILLS_TO_SYNC` in [`.agent/skills/sync-public/sync.py`](../.agent/skills/sync-public/sync.py), read on 11 Sep 2026. 62 skills, plus the 3 renamed connectors, plus 24 commands in [`.claude/commands/`](../.claude/commands/).

## The test applied to every row

A skill belongs to the **app** when it needs neither of these:

- repo state (`journal/state/*.json`, `Dashboard.md`, `Clients/`, `journal/`)
- one named client (Work, Secondary, a specific Drive folder, a specific Slack workspace)

Anything that fails the test belongs to the **workspace**, and travels with the seeded template instead.

Three buckets come out of it, not two. The third is the one that makes "all ASB skills in the app" possible at all.

| Bucket | Meaning | Count |
| :--- | :--- | :--- |
| **A. App** | Pure technique. Ships global, works in an empty folder. | 17 |
| **B. App + workspace credential** | Code is generic, the token is not. App owns the skill, workspace owns the token. | 22 |
| **C. Workspace** | Reads or writes repo state. Ships with the seeded template, never global. | 23 |

Bucket B is where the Connected tools pane already lives, so the app is already halfway through that work.

---

## Bucket A: ship as app skills

| Skill | Why it qualifies | Caveat before it ships |
| :--- | :--- | :--- |
| `hyperplan` | 5 adversarial critics, no state | none |
| `ulw` | fan-out execution mode, no state | none |
| `harvester` | bulk read-and-extract agent | none |
| `user-story-writer` | INVEST plus Gherkin, generic | none |
| `execution-guard` | how to write a runner that cannot hang | none |
| `no-emdash` | writing rule | none |
| `no-title-change` | Drive-write rule | none |
| `make-pdf` | markdown to PDF | needs a PDF toolchain on the machine |
| `mcp-switcher` | keeps tool count under the limit | overlaps the app's own Connected tools pane, see Q1 |
| `browser-service` | starts the Chrome CDP service | binary dependency, must degrade quietly |
| `interview-assistant` | CV analysis, rubrics, generic | none |
| `document-alignment` | checks related docs agree | none |
| `diagram-gen` | plain English to validated Mermaid | hands off to a GDoc embed step, split that out |
| `draft-reviewer` | pre-send quality gate | language rule per client lives in CLAUDE.md, read it from the workspace |
| `marketplace-product-manager` | PRD expertise | hardcodes Work standards, generalize first |
| `temp-file-organizer` | tidies stray files in a workspace root | generic per workspace, harmless global |
| `seo-audit` | listed, but see the defect below | **empty directory, nothing to ship** |

## Bucket B: app skill, workspace credential

Every row is the same shape. The code is generic, the token is per person or per client, and the app already has a pane that holds tokens.

| Skill | Service |
| :--- | :--- |
| `slack-connector` | Slack |
| `gmail-connector` | Gmail |
| `google-calendar-connector` | Google Calendar |
| `work-drive-connector` (public: `work-drive-connector`) | Drive, work account |
| `personal-drive-connector` (public: `personal-drive-connector`) | Drive, personal account |
| `secondary-drive-connector` | Drive, second client |
| `secondary-slack-connector` | Slack, second client |
| `gdocs-create` | Google Docs |
| `gdocs-writer` | Google Docs, legacy |
| `gdoc-surgical` | Google Docs, in-place edits |
| `gdoc-comment` | Google Docs comments |
| `fathom-connector` | Fathom |
| `fathom-frame-grab` | Fathom video stream |
| `figma-connector` | Figma |
| `jira-connector` | Jira |
| `linear-connector` | Linear |
| `clickup-connector` | ClickUp |
| `metabase-connector` | Metabase |
| `mixpanel-connector` | Mixpanel |
| `ga4-connector` | GA4 |
| `google-ads-connector` | Google Ads, **not built, blocked on a developer token** |
| `whatsapp-connector` | WhatsApp bridge |
| `gemini-image` | Gemini API key |
| `agy-bridge` | Antigravity CLI plus z.ai key |
| `meetbot` | local recorder, app already has a Record panel |

## Bucket C: workspace only

These read or write `journal/state`, `Dashboard.md`, or `Clients/`. Global installation makes them fail in every folder that is not a harness.

| Skill | State it depends on |
| :--- | :--- |
| `commitment-ledger` | `journal/state/commitments.json` |
| `waiting-watchdog` | `journal/state/waiting_on.json` |
| `decision-log` | `journal/state/decisions.json` |
| `reply-queue` | mention ledger |
| `reply-router` | mention ledger plus branching protocol |
| `slack-tracker` | mention ledger |
| `slack-channel-manager` | per-client channel whitelist |
| `inbox-hub` | mention ledger plus Gmail plus Drive |
| `access-watch` | mention ledger plus Gmail |
| `dashboard-updater` | `Dashboard.md` |
| `project-tracking-update` | Dashboard, todo, all three ledgers |
| `premeeting-cards` | calendar joined against the people pages |
| `master-product-list` | a specific Sheet |
| `work-link-sync` | a specific Sheet |
| `outcomes-loop` | PRD success criteria plus Mixpanel |
| `prd-pipeline` | repo PRD state machine |
| `weekly-report-generator` | `AGENT_STATE.md` |
| `report-auditor` | the rubric in the repo |
| `proactive-assistant` | the whole repo |
| `command-queue` | dashboard tickets, needs a local `claude` binary |
| `harness-health` | cron heartbeats, state staleness |
| `token-tracker` | per-task cost log |
| `work-hours` | local transcripts plus git history |

---

## Three defects found while classifying: fixed 11 Sep 2026

Skill discovery reads the `description` field, so a skill without one can never be auto-invoked.

| Defect | Fix applied |
| :--- | :--- |
| `gdoc-comment` had no `SKILL.md`, only `gdoc_comment.py` | `SKILL.md` written from the script's own docstring and argument parser |
| `gemini-image` had no `SKILL.md`, only `generate.py` | `SKILL.md` written, including both backends and the metered-cost gate |
| `seo-audit` is an empty directory, yet listed in `SKILLS_TO_SYNC` | removed from the manifest, with a comment saying to put it back once the skill exists |

## Decisions

| # | Question | Decision, 11 Sep 2026 |
| :--- | :--- | :--- |
| 1 | Retire `mcp-switcher`? | Open. See the analysis below: the Connected tools pane does **not** cover it. |
| 2 | Merge the Google connectors? | Open. Merge by account only. See the analysis below. |
| 3 | `secondary-*` assumes two clients | **Generalize to N clients.** the owner decided. |
| 4 | `google-ads-connector` is not built | **Hold. Remove it from the app layer** until the developer token is approved. |

### Q1 analysis: what retiring `mcp-switcher` actually costs

The skill renames keys in `mcp_config.json` to disable an MCP server, so the
client stays under a 100-tool ceiling. Two facts change the picture:

- **The Connected tools pane is not a replacement.** It manages the claude.ai
  connectors. It does not touch arbitrary local MCP servers in `mcp_config.json`,
  which is what this skill swaps. Retiring it leaves that job with no tool.
- **Deferred tool loading already removes the pressure.** Where a client loads
  tool schemas on demand, the tool count stops being the binding constraint, and
  the skill has nothing to solve. Antigravity still has the hard limit.

So the honest answer is that it is not an app skill and not a workspace skill.
It edits a machine-level config file. Keep it, move it out of the app layer, and
retire it when the last client that enforces a tool ceiling is gone.

### Q2 analysis: the Google connectors are two different overlaps, not one

Splitting them correctly matters, because only one of the two is safe to merge.

**Overlap by account, safe to merge.** `work-drive-connector`,
`personal-drive-connector` and `secondary-drive-connector` are the same code
against three accounts. `gdocs-create` already proves the pattern with its
`--account work|personal|secondary` flag. Merging these three into one Drive
connector with an account flag removes real duplication, and it is the same work
as the Q3 decision to generalize to N clients.

**Overlap by function, do not merge.** `gdocs-create`, `gdocs-writer`,
`gdoc-surgical` and `gdoc-comment` are four different jobs, not four accounts.
`gdocs-writer` is already marked legacy, so retire that one and leave the other
three alone.

**Gmail, Calendar and GA4 stay separate.** Their scopes are different products:
`gmail.modify`, `calendar.events` plus `calendar.readonly`, and
`analytics.readonly`.

Negatives of merging, in order of how much they hurt:

1. **Account separation is currently a safety rail, not an accident.** Which
   connector gets called is what keeps work and personal data apart. Visibility
   defaults differ by account (domain for work, private for personal), and only
   `work-drive-connector/token.json` can read Doc revisions. A merged connector
   has to carry that policy in code, where it can be got wrong, instead of by
   construction, where it cannot.
2. **Scope escalation on every account.** One merged connector needs the union of
   the scopes. A personal account would be asked to consent to work scopes it
   never needed. `work-drive-connector` already carries calendar scopes on top
   of Drive, so the union is wider than it looks.
3. **One token failure takes everything down.** Today an expired work token
   leaves personal Drive working.
4. **Re-consent for every existing user, with no rollback** if the merged build
   has a bug.
5. **Google verification gets harder** for one OAuth client that asks for many
   sensitive scopes at once.
