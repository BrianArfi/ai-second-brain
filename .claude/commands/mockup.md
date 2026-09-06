---
description: Artifact - Clickable HTML mockup of a flow, built to be shown live or screen recorded
argument-hint: "<the flow to mock up>"
---

Build a working clickable prototype of a flow, in one HTML file, for showing on a call.

This is screens a person can drive. For an explainer page use `/artifact`. For slides use `/deck`.

## 1. Get the flow right before the pixels

Write the step list out in plain sentences first and check it against the source: who acts, what
they see, what changes, and what happens when it fails. Then build.

A convincing mockup of the wrong flow costs more than no mockup, because people approve what they
see. If a step is genuinely undecided, mock the recommended path and mark the alternative visibly
on the screen rather than quietly picking one.

## 2. Build it

- **One file, fully self-contained.** No external CSS, font, script or image. Inline SVG for icons.
- **A device frame** when it is a phone journey, so the reader can tell app from web.
- **Every screen reachable, no dead buttons.** A control that does nothing must look disabled.
- **Presenter controls**, because this gets driven live: number keys jump to a flow, arrow keys
  step, space plays, one key hides the presenter bar for a clean recording. Print the key map on screen.
- **A self-playing option**, so the presenter can talk over it instead of clicking.
- **Realistic content.** Real names, real prices, the right currency and language. Lorem ipsum
  reads as unfinished and turns the review into a discussion about the copy.
- Light and dark via `:root` custom properties plus a `[data-theme]` override. Honour
  `prefers-reduced-motion`.

## 3. Verify

```
grep -nE 'https?://|@import|fetch\(|XMLHttpRequest|<img|src=' <file>.html
```

Only internal SVG references may match. Then walk every screen yourself and confirm each control
does what it claims.

Hand over the **absolute** path. File it under a work-tree node if it belongs to tracked work (`/work-tree`).

Flow: $ARGUMENTS
