---
description: Artifact - Validated Mermaid diagram, rendered and checked, ready to embed in a PRD or Doc
argument-hint: "<what to diagram, in plain English>"
---

Turn a description into a Mermaid diagram that actually renders, then place it.

Authoritative SOP: [`.agent/skills/diagram-gen/SKILL.md`](../../.agent/skills/diagram-gen/SKILL.md). Follow it.

Use this for a flow, sequence, architecture, state machine or ER diagram inside a document.
For a whole standalone page use `/artifact`.

## Pick the type

`flowchart TB` or `LR` for process and architecture. `sequenceDiagram` when the point is who calls
whom in what order. `erDiagram` for data model. `stateDiagram-v2` for lifecycle. `gantt` for phasing.

Show movement between parts, not where parts sit. A box diagram that only names components tells
the reader nothing they could not get from a list.

## Syntax rules that break renders

- Quote any label with a space, parenthesis or punctuation: `A["Loyalty Engine (Teammate)"]`
- One edge per line. No markdown inside labels. No raw `(`, `:` or `;` outside quotes.
- `subgraph` for grouping. Under 20 nodes per diagram; split it if you go over.

## Validate before showing the owner, always

```bash
python3 .agent/skills/diagram-gen/render_check.py --file <mermaid.mmd> --out /tmp/diagram_preview.png
```

Exit 0 with a saved PNG means valid. Anything else means fix and retry. Never show an unvalidated
diagram: the failure surfaces in front of the reader, not in front of you.

## Deliver

- **Quick answer**: the fenced mermaid block plus the absolute path to the preview PNG.
- **Into a PRD or BRD**: put `[[PLACEHOLDER_NAME]]` in the markdown, add `"[[PLACEHOLDER_NAME]]": """<mermaid>"""` to the `DIAGRAMS` dict in `embed_mermaid_in_gdoc.py`, run the gdocs create or update with `--convert`, then run the embed script to swap the placeholder for an inline PNG. `render_check.py` uses the same renderer as the embed script, so what you validated is what lands.

Diagram: $ARGUMENTS
