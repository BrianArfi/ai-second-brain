---
description: Product - Feature or PRD section into INVEST-validated user stories with Gherkin acceptance criteria
argument-hint: "<feature name, or a path to the PRD>"
---

Turn a feature into sprint-ready stories a developer can build without asking a question.

Authoritative SOP: [`.agent/skills/user-story-writer/SKILL.md`](../../.agent/skills/user-story-writer/SKILL.md). Follow it.

Use this for backlog grooming, sprint planning, or breaking down a PRD requirement. For a whole
PRD use `/prd`. To file the result as tickets use `/ticket`.

## 1. Read the context first

Look in `Clients/<Client>/<Project>/` for `personas.md`, `product.md`, and any existing PRD.
If a PRD exists, every story references its Req ID. Writing stories that do not map back to the
PRD is how scope quietly grows between the spec and the sprint.

## 2. Write each story

Format `As a <persona> / I want to <action> / So that <outcome>`, id'd `US.<Feature>.<Number>`.

Validate against INVEST and say so per story: Independent, Negotiable, Valuable, Estimable, Small,
Testable. A story failing Small gets split before you move on, not flagged and left.

## 3. Acceptance criteria in Gherkin, three minimum

Happy path, alternate path, and an error or edge case. **Every `Then` must be objectively
measurable by QA.** "Then the user sees a friendly message" is not testable and does not ship.
"Then the page shows `Code already used` and the Redeem button is disabled" is.

List the edge cases you found and did not cover, so they are a decision rather than an omission.

## 4. Size it

T-shirt: S under a day, M one to three days, L three to five, XL over five. An XL is a signal to
split, not an estimate.

## 5. Batch mode

More than one story: list all the titles first, get the owner's confirmation on the set, then write
them out. Writing twelve stories against the wrong breakdown wastes the whole pass.

Output to `Clients/<Client>/<Project>/`. No em-dashes.

Feature: $ARGUMENTS
