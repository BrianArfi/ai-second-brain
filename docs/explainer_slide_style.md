# Explainer Slide Style (reference: Albadarneh's ExampleProgram App Workshop deck)

> Source: [ExampleProgram App Workshop v5.2](https://docs.google.com/presentation/d/<YOUR_DRIVE_ID>/edit) by Mohammad Albadarneh, presented to ExampleCo on 2 Nov 2026 (workshop: ExampleCo App and Payment mechanism). Local copy: [PDF](C:/Users/you/.gemini/antigravity/scratch/product-second-brain/docs/explainer_slide_style/ExampleProgram_App_Workshop_v5.2_reference.pdf). Contact sheets: [1-6](C:/Users/you/.gemini/antigravity/scratch/product-second-brain/docs/explainer_slide_style/grid1.png), [7-12](C:/Users/you/.gemini/antigravity/scratch/product-second-brain/docs/explainer_slide_style/grid2.png), [13-18](C:/Users/you/.gemini/antigravity/scratch/product-second-brain/docs/explainer_slide_style/grid3.png), [19-21](C:/Users/you/.gemini/antigravity/scratch/product-second-brain/docs/explainer_slide_style/grid4.png).
>
> Status: breakdown only. Not yet wired into `/deck` or `/artifact`.
>
> Note: text overlaps in the contact sheets come from the PDF render (Calibri was substituted). The real deck has no overlaps.

## 1. Why it works

1. **One system, repeated.** About 12 layouts, one grid, one type scale, one chrome. The audience learns the pattern on slide 3 and then reads only the content.
2. **Two audiences on every slide.** The numbered points say what the member gets. The "For ExampleProgram:" box says how the client makes money. A client exec never has to ask "so what for us".
3. **Real product is the hero.** Phone screens and portal screenshots carry the slide. Text explains the screen, it does not replace it.
4. **Claims, not topics, as headlines.** "Verify once. Earn everywhere." "No flight this month? Miles keep working." Many headlines are two short beats.
5. **Low text, hard limits.** 3 to 4 points per slide. Each point is one bold title and one grey line. No paragraphs.
6. **Numbers create the path.** Journeys 1 to 7, numbered circles, numbered money-flow steps, numbered payment priority. The presenter never loses the room.
7. **Problem in one line.** For an audience that knows the pain (Julie's call), skip the basics and go straight to impact and solution.

## 2. Narrative arc (21 slides)

| # | Beat | Slides | Question it answers |
| :--- | :--- | :--- | :--- |
| 1 | Cover | 1 | Who and what |
| 2 | Vision | 2 | Where are we going (one quote line) |
| 3 | Proof | 3 | Why believe us (before, live today, what it proved) |
| 4 | System | 4-6 | How the whole thing fits: ecosystem, money flow, identity |
| 5 | Tools | 7-8 | What the client and partners operate |
| 6 | Show it | 9-18 | Section divider, journey index, one slide per journey |
| 7 | Plan | 19 | When (phases) |
| 8 | Future | 20 | What comes after launch |
| 9 | Close | 21 | Thank you (mirror of cover) |

Rule: vision, proof, system, show, plan. Proof comes before the system so the system reads as an extension of something already live.

## 3. Layout library

| ID | Layout | Seen on | Structure |
| :--- | :--- | :--- | :--- |
| L1 | Co-brand cover | 1, 21 | Logo lockup (Work, divider, client), large bold title left, hero image right over vertical brand bars and a thin line pattern. Closing slide mirrors it. |
| L2 | Dark vision | 2 | Full-bleed dark photo. Eyebrow, quote headline, one subline, "Powered by Work". Phone in the centre-right with floating chips for moments of the day. |
| L3 | Before, now, proved | 3 | Three panels with arrow connectors. Middle panel is dark and emphasised, with LIVE badges on tiles and a "behind the scenes" capability list. |
| L4 | Hub and spoke | 4 | Dark. Left column: contexts (the member's day). Centre: phone. Right column: what is inside, plus a second group (portals). Curved connector lines. A highlight bar at the bottom names the one thing that connects all (Work ID). |
| L5 | Money cycle | 5 | Actor nodes (icon plus label) around a centre box. Numbered dashed arrows. Pill labels say who sets each value. Equation bar at the bottom: "New revenue = A + B + C + D". |
| L6 | Fan-out | 6 | One source node, one highlighted product card (own colour), six capability rows to the right. |
| L7 | Screenshot plus features | 7, 8 | Large screenshot left. Four rows right: icon in a filled square, bold title, one grey line. |
| L8 | Section divider | 9 | Dark brand colour, geometric blocks on the right, eyebrow, big title, one subline. |
| L9 | Journey index | 10 | All journey phones in one row, each with a numbered circle and a 1-3 word label. Works as the agenda. |
| L10 | Journey detail | 11-14, 16, 18 | Eyebrow "JOURNEY n · NAME". 1-3 phones left. 3-4 numbered points right. Optional "For <client>:" callout box at the bottom. |
| L11 | Twin image cards | 15 | Two full-bleed photo cards, each with eyebrow, headline and a phone overlay. For emotional or national themes. |
| L12 | Priority list | 17 | Numbered option cards in order of rollout. The first card is tinted, the rest neutral. Phones left with captions. |
| L13 | Phase columns | 19 | Four columns. Dark header with date range, phase verb as title (Prepare, Build, Launch, Grow), 3-4 bullets. The launch column is tinted. |
| L14 | Tile grid | 20 | Icon tiles, title only, 3 per row. For "what comes next". |

## 4. Chrome (every content slide)

- **Eyebrow**, top left: uppercase, bold, brand colour, about 9-10pt, letter-spaced. Names the section ("HOW IT WORKS", "JOURNEY 2 · EARN EVERY DAY").
- **Headline** below it: sentence case, regular weight, about 32pt, one line, dark ink.
- **Footer**, bottom left: "CLIENT × WORK · STRICTLY PRIVATE AND CONFIDENTIAL", tiny grey caps.
- **Slide number** bottom right, plus the brand corner mark.
- **Margins**: about 0.6 in on a 13.33 × 7.5 in slide. Content starts at about 1.7 in from the top.

## 5. Type scale

| Role | Size | Weight | Colour role |
| :--- | :--- | :--- | :--- |
| Cover title | 40+ | Bold | Ink |
| Headline | 32 | Regular | Ink |
| Point title | 16 | Bold | Ink |
| Body line | 12-13 | Regular | Muted |
| Eyebrow, labels, chips | 9-10.5 | Bold caps | Primary |

Font in the source: Calibri throughout. One family, weight does the hierarchy.

## 6. Colour roles (theme tokens)

Colours change per client; the roles stay. Values below are the ExampleCo theme from the source.

| Token | Role | ExampleCo value |
| :--- | :--- | :--- |
| `--ink` | Headlines, point titles | `#0D3D1E` |
| `--muted` | Body lines | `#5A6B60` |
| `--primary` | Icon squares, eyebrows, numbered circles, phase headers | `#1A6B32` |
| `--accent` | Arrows, LIVE badges, brand bars | `#6ABF4B` |
| `--tint` | Panels, callout boxes, highlighted cards | `#E8F4EA` / `#F2F7F3` |
| `--dark` | Dark slides (vision, ecosystem, divider) | `#0E2414` |
| `--product` | One product that owns its own colour (Work ID) | Work blue, tint `#EEF0FF` |

Rule: one product can own a colour across the whole deck (Work ID is always blue). Everything else uses the client theme.

## 7. Components

- **Numbered point**: filled circle with number, bold title, one grey line.
- **Feature row**: rounded filled square with a white line icon, bold title, one grey line.
- **Client callout**: tinted rounded box, "For <client>:" in bold, one sentence on the business value.
- **Phone frame**: real screen, rounded device frame, optional caption under it.
- **Chip**: small rounded pill with icon and 1-2 words, floating on dark backgrounds.
- **LIVE badge**: small accent pill on a tile.
- **Equation bar**: "New revenue for <client> = A + B + C" in pills.
- **Arrow connector**: accent circle with a chevron between panels.

## 8. Copy rules

- Headline is a claim or a benefit, 3 to 9 words. Two-beat lines are good.
- Point title 2 to 5 words. Point line 6 to 15 words.
- No paragraph anywhere. The talk track carries the detail.
- Every journey slide has a client-value line, either in the points or in the callout box.
- The problem gets one line when the audience already knows it.

## 9. Weak spots to fix in our version

1. **Slide 5 (money cycle) is too dense.** Many 9pt labels and dashed lines; hard to read on a shared screen. Our version: animate the four steps one at a time, or split into two slides.
2. **Body text is small for a room.** 12-13pt on a 13.33 in slide is about 17px at 1280 wide. Our `/deck` rule says 20px minimum. Keep the rule.
3. **Roadmap (slide 19) has no milestones or owners.** Phases carry date ranges only. Our version adds a dated milestone per phase and one dependency line.
4. **"What comes next" tiles (slide 20) have no timing or value line.** Add one grey line per tile.
5. **No visible agenda before the journeys start.** Slide 10 does this job for the walkthrough only. For decks over 15 slides, add a small progress marker by section.

## 10. How it maps to our HTML decks

Already in [`/deck`](C:/Users/you/.gemini/antigravity/scratch/product-second-brain/.claude/commands/deck.md): takeaway headlines, one idea per slide, diagram first, 16:9, light and dark themes. New from this deck: the layout library (L1-L14), the chrome, the type scale, the colour roles as theme tokens, the client callout, and the two-audience rule.
