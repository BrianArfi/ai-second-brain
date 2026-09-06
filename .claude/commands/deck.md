---
description: Artifact - Slide deck as one self-contained HTML file, keyboard driven, for presenting live
argument-hint: "<topic and audience>"
---

Build a slide deck as a single HTML file, presentable from a browser.

## 1. The argument comes before the slides

Write the spine first, one line per slide, and show that list before building anything. A deck is
an argument in a fixed order, so the order IS the work. Building twenty slides and then looking
for the argument wastes the expensive part.

Ask who is in the room. A deck for a decision-maker is status and the decision: what, when, who.
A deck for a team can carry the reasoning.

## 2. One idea per slide

- The title states the takeaway, not the topic. "Integration is not the blocker" beats "Integration status".
- If a slide needs a paragraph, it is two slides, or it is a talk track.
- Numbers get a chart, not a table, unless exact values matter.
- A system gets a diagram, with the prose as its caption.

## 3. Build it

- **One file, self-contained.** No external CSS, font, script or image.
- **16:9**, scaling to the viewport, readable from the back of a room. Body text no smaller than
  20px at 1280 wide.
- **Keyboard driven**: arrows or space to advance, `Esc` for a grid overview, `F` for fullscreen.
  Print the key map on the title slide.
- **Slide numbers and a thin progress bar.** A presenter needs to know where they are.
- **Speaker notes** behind a key or in a `<details>` block, never on the slide.
- Light and dark via `:root` custom properties plus `[data-theme]`. Honour `prefers-reduced-motion`.

## 4. Verify

```
grep -nE 'https?://|@import|fetch\(|XMLHttpRequest|<img|src=' <file>.html
```

Hand over the **absolute** path.

Deck: $ARGUMENTS
