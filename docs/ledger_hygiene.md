# Ledger hygiene: how the trackers stay small and true

The commitment ledger, the waiting-on watchdog and the work tree are only useful while
they are short enough to read. This page is the operating rule for keeping them that
way. It was written on 26 Sep 2026 after the second full cleanup in a week.

## Why the pile grew back after the 19 September cleanup

| Finding (26 Sep 2026) | Number |
| :--- | ---: |
| Records added in the previous 30 days, commitments plus waiting-on | 595 |
| Records closed in the same 30 days | 316 |
| Live waiting-on records never chased once | 263 of 344 |
| Open commitments with no named recipient | 172 of 331 |
| Open commitments with no due date | 189 of 331 |
| Chase drafts sent from the chase queue since it started | 0 |
| Final notices marked sent from the 19 Sep escalation run | 0 of 28 |
| Weekly ledger audits that finished since 8 Aug | 0 of 7 |
| Spent on the four audit runs that were killed mid-check | about USD 104 |

Three causes, in order of size:

1. **Intake is wider than attention.** Every MoM action line that names the owner becomes an
   open commitment with no recipient and no due date. Every ask the owner makes becomes a
   waiting-on record, whether or not he means to follow it up.
2. **Every exit needed an outbound message.** A record could leave only by evidence in
   the tracked thread, a chase, or a final notice. Chases and notices wait for the owner's
   approval, and at 30 bundles a day none were approved. So records only arrived.
3. **The safety net was broken.** The weekly audit started its checks in the background
   and exited before they finished: four runs killed mid-check at USD 19 to 38 each, three
   more lost to an expired login or no network. Fixed 26 Sep.

## The rules

**1. A record needs an owner, a date and a reason to exist.**

| Ledger | A record means | It must carry |
| :--- | :--- | :--- |
| Commitment (`COM-`) | the owner owes a named person a concrete deliverable | recipient, due date, node |
| Waiting-on (`WAIT-`) | Someone owes the owner something that blocks current work | owner, the Slack link of the ask, SLA, node |
| Work-tree node | An area of work that is live | at least one live record, or a planned date |

A meeting action item that fails this test stays in the MoM. It is not a record.

**2. Every record leaves within 30 days.** It closes with evidence, or it gets chased, or
it gets dropped with a reason. Nothing sits.

**3. At most 5 chases a day.** The morning update picks them: live-priority work first,
then the oldest. One message per person. the owner approves the batch with one word. The
rest waits for the weekly review, so a breached record is never silently forgotten and
never a daily alarm either.

**4. One weekly review decides the rest.** The audit runs Saturday 15:00 WIB. It closes
what has evidence by itself and writes one list for the owner to
`journal/ledger_audit/<date>.md`:

| Verdict | What happens |
| :--- | :--- |
| `CLOSE`, `DROP_EXPIRED`, `DROP_SUPERSEDED`, `DROP_DUPLICATE`, `DROP_MOOT` | Applied by the audit, with the evidence in the note |
| `DEPRIORITISE` | Proposed. the owner approves the batch with one word, or names exceptions |
| `CHASE` | Goes into the next mornings' chase lists |
| `KEEP` | Stays, with a new due date where one was missing |
| `ASK` | One question for the owner |

**5. A node is archived when its work ends.** Same day when a drop ships or an
engagement stops. Otherwise the weekly review proposes nodes with no live record and no
activity for 30 days. Archived nodes disappear from the Work tab (the Archived filter
still shows them), and old records keep resolving.

**6. Everything is reversible.** `reopen` brings a closed or dropped record back for 14
days, after which the ledger prunes it and only git history holds it. Every cleanup
writes its full list into `journal/ledger_audit/` so nothing depends on that window.
`work_tree.py unarchive-node <id>` restores a node with its old status.

## Accuracy without spending Claude quota

Added the same evening, after a chase draft asked Teammate for four things that were
already done. Asks that finish as an action (access granted, ticket resolved, doc
written) leave no reply in the ask's thread, so a thread check calls them open forever.
Most of those misses were findable by a script. So the order is: script first, cheap
model second, Claude last.

| Layer | What it does | Model |
| :--- | :--- | :--- |
| `autoclose_check.py`, daily 13:10 WIB and before every Saturday audit | Reads each record's tickets (Work Jira, ExampleVendor Jira, Linear), decisions and MoMs that cite it, and Slack (waiting-on only). A record whose `done_ticket` is Done closes with no model. Other movement goes to one cheap call; DONE counts only when the model's quote is found verbatim in the evidence. Tickets still To Do are kept as dated `open_evidence` in `journal/state/autoclose_state.json` | none, then the cheap chain |
| Cheap chain, `.agent/scripts/cheap_llm.py` | Gemini 3.8 Flash, then GLM 5.3 Flash, then Haiku. Any step that fails or returns no valid JSON falls through; if all fail, nothing is written. Every attempt is logged to `dashboard-data/cheap_llm_log.jsonl` | cheap only |
| Intake, both ledger CLIs | `add` requires `--done-when` (or `--done-ticket KEY`). Waiting-on also requires the link of the ask (`--source`, or `--no-link-why`). Commitments also require `--to` and `--due`. `set-done` adds these to an existing record | none |
| Meeting action items | Enter as candidates. `extract` asks the cheap chain whether each is a real promise to a named person; only those become commitments, with a due date and a done condition. The rest stay in the MoM | cheap chain |
| `work_tree_stale.py` | Proposes nodes with no live record, no open todo, no ticket updated and no record activity for 30 days. Only proposes; the Saturday report lists them | none |
| Saturday readers | Only what the layers above could not settle | sonnet |

**Chases need evidence.** A record goes on a chase list only with dated evidence that it
is still open. No evidence either way is a question for the owner, never a chase.

## Targets

| Measure | Target | Seen in |
| :--- | ---: | :--- |
| Open commitments | 60 or fewer | morning update health line |
| Open waiting-on | 60 or fewer | morning update health line |
| Breached waiting-on | 15 or fewer | morning update health line |
| Open records with no node | 0 | `work_tree.py coverage` |

## Who does what

| When | The harness | the owner |
| :--- | :--- | :--- |
| Every day 13:10 WIB | `autoclose_check.py` closes what finished quietly, with evidence | |
| Every morning | Health line, plus up to 5 chase drafts, each with dated evidence it is still open | Approve the chases with one word |
| Saturday 15:00 WIB | Weekly audit: closes what has evidence, writes the review list | |
| Weekend or Monday morning | Links the review in the briefing | Answer the list: "ok", or name exceptions. About 10 minutes |
| A drop ships, an engagement stops | Archives the node, drops its moot records | Say so in one line |
| Adding a record by hand | Refuses a record with no node | Say who it is for and by when |

## Commands

```bash
# weekly audit by hand (skips if a report under 5 days old exists; --force overrides)
python3 .agent/skills/ledger-audit/scripts/weekly_ledger_audit.py

# a full cleanup: slice every live record, then apply the readers' verdicts
python3 .agent/skills/ledger-audit/scripts/build_review_set.py --all --out _temp/ledger_audit/<date>
python3 .agent/skills/ledger-audit/scripts/apply_verdicts.py --verdicts _temp/ledger_audit/<date>/verdicts            # dry run
python3 .agent/skills/ledger-audit/scripts/apply_verdicts.py --verdicts _temp/ledger_audit/<date>/verdicts --apply    # auto verdicts
python3 .agent/skills/ledger-audit/scripts/apply_verdicts.py --verdicts <dir> --apply --approve-deprioritise          # after the owner's ok

# the automatic check (dry run without --apply)
python3 .agent/skills/ledger-audit/scripts/autoclose_check.py --apply
python3 .agent/skills/ledger-audit/scripts/autoclose_check.py WAIT-0603 --apply
python3 .agent/scripts/work_tree_stale.py
python3 .agent/scripts/cheap_llm.py                     # self-test of the Gemini -> GLM -> Haiku chain

# say what done looks like on an existing record
python3 .agent/skills/waiting-watchdog/scripts/waiting_watchdog.py set-done WAIT-xxxx --done-when "..." [--done-ticket ABC-123]
python3 .agent/skills/commitment-ledger/scripts/commitment_ledger.py set-done COM-xxxx --due 2026-10-03 --done-when "..."

# undo
python3 .agent/skills/commitment-ledger/scripts/commitment_ledger.py reopen COM-xxxx
python3 .agent/skills/waiting-watchdog/scripts/waiting_watchdog.py reopen WAIT-xxxx
python3 .agent/scripts/work_tree.py unarchive-node <node-id>
```

The triage rules the readers follow: [`triage_instructions.md`](../.agent/skills/ledger-audit/triage_instructions.md).
