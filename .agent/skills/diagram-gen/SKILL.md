---
name: Diagram Generator
description: Turn a plain-English description into a clean Mermaid diagram, validate it renders, and hand it to the GDoc embed pipeline. Triggers: "make a diagram", "diagram this flow", "draw the architecture", "/diagram". For PRDs, BRDs, architecture, sequence flows. Adapted from gstack /diagram.
---

# Diagram Generator

Convert an English description (a flow, an architecture, a sequence, an ER model) into valid Mermaid, verify it actually renders, then embed it into a Google Doc via the existing pipeline. Mermaid is the right target because [embed_mermaid_in_gdoc.py](../../../scripts/embed_mermaid_in_gdoc.py) already turns it into an inline PNG in a GDoc.

## When to use
A PRD/BRD needs an architecture or flow diagram; you want to visualize a sequence, a state machine, or an entity model from a verbal description. NOT for freehand/visual mockups (use the design tools for that).

## Procedure

1. **Pick the Mermaid type that fits the intent:**
   - process / architecture / decision flow → `flowchart TB` (or `LR` for wide, shallow flows)
   - actor-to-actor message order → `sequenceDiagram`
   - data model / tables + relations → `erDiagram`
   - lifecycle / status transitions → `stateDiagram-v2`
   - timeline / roadmap → `gantt`
2. **Write the Mermaid** following these rules (they prevent the most common render failures):
   - Quote any node label containing spaces, parentheses, or punctuation: `A["Loyalty Engine (Teammate)"]`.
   - Group related nodes with `subgraph Name["Label"] ... end`.
   - One edge per line; keep arrows simple (`-->`, `-.->`, `==>`); label edges with `-->|text|`.
   - No raw `(`/`)`/`:`/`;` outside quotes; no markdown inside labels.
   - Keep it readable: if a flow exceeds ~20 nodes, split into two diagrams.
3. **Validate it renders BEFORE showing the owner** (never hand over un-rendered Mermaid):
   ```bash
   python3 .agent/skills/diagram-gen/render_check.py --file <mermaid.mmd> --out /tmp/diagram_preview.png
   ```
   Exit 0 + a saved PNG = valid. **Exit 1** = the renderer read the source and rejected it: fix the Mermaid and re-run. **Exit 3** = every renderer is down, so the diagram is NOT the problem and editing it wastes time. Do not proceed on a failed render either way.
4. **Deliver:**
   - For a quick answer: show the fenced ```mermaid block + the preview PNG path.
   - For a GDoc (PRD/BRD): put a `[[PLACEHOLDER_NAME]]` in the source markdown where the diagram goes, add the `"[[PLACEHOLDER_NAME]]": """<mermaid>"""` pair to the `DIAGRAMS` dict in [embed_mermaid_in_gdoc.py](../../../scripts/embed_mermaid_in_gdoc.py), run `gdocs-create`/`update --convert`, then run that script to replace the placeholder with the inline PNG. (Per [[feedback_gdoc_formatting_pass]], a re-convert wipes inline images, so re-run the embed after every convert.)

## Notes
- Validation uses the same renderer stack the embed script uses (`.agent/skills/diagram-gen/mermaid_render.py`: kroki.io first, then mermaid.ink), so "renders in render_check" guarantees "renders in the GDoc."
- **There are two renderers because one of them fails on its own.** On 9 Sep 2026 kroki.io's mermaid backend returned HTTP 500 for every diagram, including `flowchart TB / A-->B`, while kroki's graphviz backend on the same host answered 200. The old `render_check.py` printed that as "INVALID Mermaid", so a correct diagram looked like a syntax error and no diagram could be produced at all. A renderer outage and a broken diagram are different failures and no longer share a message.
- Keep generation in whatever model the session is on; this is small. Under GLM mode the Mermaid drafting MAY be offloaded via `agy-bridge --task draft`, but always run `render_check.py` in the main loop before delivering.
