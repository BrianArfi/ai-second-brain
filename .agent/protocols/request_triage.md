# Request triage: do it, draft it, or ask the owner

Standing rule, approved by the owner on 20 Oct 2026. It applies to every incoming request (Slack, email, Doc comment, Jira comment, meeting action item) and to every reply-router session.

Sort each request into exactly one kind before you write anything.

## Kind 1: just do it, then report

Internal, reversible work. Do it in the same turn, with no approval step, and tell the owner afterwards in one line with the link.

- Create or edit a Jira, Linear or Legal (MLSP) ticket, including the description, labels, links and the sprint. Pass `--approved` yourself: the owner's standing approval covers it. Dedupe first, and every ticket still needs a work-tree node.
- Update the ledgers (commitments, waiting-on, decisions, chases) and `journal/todo.md`.
- Draft, update or publish an internal doc in the Work Drive (domain-restricted, never public), and reply to Doc comments when the answer comes from a decision already logged or a source doc.
- One short acknowledgement in the SAME thread as the request, when it only reports that the asked-for item exists, for example "raised it as MLSP-1485" or "added ABC-123 to Sprint 128". It names the item and the link and says nothing else. It still passes every send gate (slop guard, handles, claim guard).

## Kind 2: draft, the owner approves

Anything else sent as the owner. The existing gates stay unchanged.

- Any Slack, WhatsApp or email message that does more than kind 1's acknowledgement: an answer, an opinion, a commitment, a date, a push-back or a chase.
- Anything client-facing (ExampleCo, Example Program, any external party) and any email outside yourcompany.com (double approval).
- Anything addressed to YourManager Teammate or Julie (double approval, every channel).
- Anything destructive: delete, archive or close someone else's work.

## Kind 3: the owner decides

Do the unblocked part, then ask ONE question with a recommendation.

- Scope, priority, deadlines and dates promised to others.
- Money, pricing, commercial terms and contracts.
- A request that conflicts with a logged decision (check `decisions.json`) or with the owner's earlier answer.
- Who owns the work, when the source does not say.

## When unsure

A request that mixes kinds: do the kind 1 part, draft the kind 2 part, and ask the kind 3 part, all in one reply. A request you cannot place: treat it as kind 3.
