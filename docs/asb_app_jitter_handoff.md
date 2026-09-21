# ASB app UI jitter: evidence pack for the Mac session

Written 11 Sep 2026 from the Windows box, where the app source is not available.
Everything below comes from `%APPDATA%\com.aisecondbrain.desktop\app.log`, which
the app writes itself. Open this before touching the app repo, so that session
starts with evidence instead of a reproduction hunt.

App version: **0.13.11**. The behaviour is still present in it.

## The symptom the owner reports

Text and layout move up and down in the main area while he is not typing.

## What the app already logs

The app has its own instrumentation for this, named `jitter idle`. It prints the
element, the old height and the new height:

```
[ui] jitter idle: bannerBar 186px -> 138px
[ui] jitter idle: timeline  233px -> 281px
```

Volume in the current log, which spans 31 Aug to 11 Sep 2026:

| Day | Events |
| :--- | ---: |
| 2026-08-31 | 1051 |
| 2026-09-10 | 260 |
| 2026-09-11 | 149 |
| **Total** | **1460** |

By element:

| Element | Events |
| :--- | ---: |
| `timeline` | 644 |
| `bannerBar` | 625 |
| `composerWrap` | 90 |
| `banner` | 36 |
| `pinnedUserMessage` | 29 |
| `chatHeader` | 14 |
| `bannerPromo` | 9 |
| `agentDock` | 8 |
| `branchBanner` | 5 |

## The finding: two elements share a fixed height budget

`timeline` and `bannerBar` account for 1269 of the 1460 events, and they never
move alone. They always move in the same millisecond, in opposite directions,
and **their heights always sum to 419px**:

| bannerBar | timeline | sum |
| ---: | ---: | ---: |
| 186 | 233 | 419 |
| 138 | 281 | 419 |
| 162 | 257 | 419 |
| 125 | 294 | 419 |
| 210 | 209 | 419 |
| 234 | 185 | 419 |

That constant holds across every observed pair. So this is not two independent
bugs and not content growing. It is one zero-sum layout: both elements live in a
container of fixed height, one is measured and sized first, the other absorbs
the remainder, and sizing the second changes the measurement that produced the
first. The loop then repeats every few seconds.

**Where to look first:** whatever computes `bannerBar` height, and whatever
assigns `timeline` the remaining space. A `ResizeObserver` on one that writes a
style on the other is the classic shape of this. Break the cycle by measuring
from a source that the write cannot change, for example the container rather
than the sibling.

## The second, separate case: `composerWrap`

90 events, and it behaves differently. It oscillates between two values, often
one second apart:

```
[ui] jitter idle: composerWrap 116px -> 128px
[ui] jitter idle: composerWrap 128px -> 116px
[ui] jitter idle: composerWrap  53px ->  65px
[ui] jitter idle: composerWrap  65px ->  53px
```

Two different step sizes appear. The series 53, 65, 87, 109, 130, 152, 174 steps
by about 22px, which is one text line. But 116 to 128 is 12px, which is not a
line. A 12px step is the size of a scrollbar or a padding change, which points
at an auto-grow textarea measuring `scrollHeight` while its own resize makes a
scrollbar appear and disappear.

Treat this as a second bug with the same family of cause. Fixing the
`bannerBar` / `timeline` loop will not fix it.

## What to confirm on the Mac, in order

1. Find the emitter of `jitter idle` in the UI source. It already knows the
   element names, so it is the fastest route into the layout code.
2. Confirm the 419px container and identify which of the two elements is written
   to, and by what.
3. Check whether `composerWrap` measures `scrollHeight` on an element whose own
   height it then sets.

## What is not the cause

Every event is labelled `idle`, not streaming. The log separately records
streaming under `[ui] stream:`, and those lines do not coincide with the jitter
lines. Time spent looking at token streaming is time wasted.

---

# Resolved in 0.15.0, and the hypothesis above was wrong

Written 12 Sep 2026 from the Windows box, after fixing it in `asb-main-win`
(commit `19d3f34`). Kept rather than deleted, because the evidence above is what
found the answer and the wrong guess is worth reading next to it.

## There is no observe-measure-write cycle

The section above predicts "a `ResizeObserver` on one that writes a style on the
other". There isn't one. Nothing in JavaScript writes `#bannerBar`'s height at
all: its rows are stated in CSS, and the one measure-and-write in `banner.js`
reads a text row's own `scrollHeight` to toggle a fade mask. It converges in one
pass and never touches a sibling.

## The 419px constant is not a container. It is arithmetic.

That number appears nowhere in the stylesheet, and looking for it is what makes
this hard. It is the height of the chat column minus the header and the
composer, at that window size, and the reason the pair always sums to it is that
one of them was defined as "whatever the other one left":

- `.banner-bar` declared no `flex-shrink`, so it defaulted to shrinkable. Every
  other child of that column pins itself (`#chatHeader`, `.branch-banner`,
  `.pinned-user-message`, `#composerWrap`), which left the banner as the only
  thing flexbox could take space from.
- `.timeline` was `flex: 1 1 auto`, so its flex **base size** was the full height
  of the conversation — normally far taller than the column. Every layout pass
  therefore began with a large deficit and resolved it by shrinking both
  shrinkable children in proportion.

So the two could only ever trade pixels, in the same millisecond, in opposite
directions, summing to the space they shared. Which is exactly what the log says.

The fix is two declarations: `flex-shrink: 0` on `.banner-bar`, and
`flex: 1 1 0` plus `min-height: 0` on `.timeline`. `min-height: 0` is not
optional — without it a flex item's automatic minimum size is its content, which
silently reintroduces the coupling the zero basis just removed.

Note this also explains why stating the banner's rows in 0.13 did not fix it. The
commit that did so quoted `bannerBar 153px -> 144px` as its evidence and then
conceded the shrink "was not reproducible headless". 153px is precisely what
those rows state, so those nine pixels were never the rows resizing.

## The composerWrap reading was partly the instrument

The "oscillates between two values about a second apart" shape was an artifact.
The probe updated its per-element baseline **before** its 1000ms throttle, so
every change swallowed by the window silently became the new baseline and was
never reported. Anything moving faster than 1Hz was therefore logged as a tidy
two-value secondary once a second, and the amplitude in `app.log` was a floor rather
than the real excursion. It reports the whole excursion now, with a count.

The genuine composer cost was real but different: `autoGrowComposer` sets
`height: auto`, reads `scrollHeight`, then writes a pixel height — two
layout-dirtying writes per keystroke, and `#composerWrap` is a flex sibling of an
unvirtualized `.timeline`. 0.15.0 skips the work when nothing that determines the
height has changed, and adds `contain: layout` to `.msg` so a viewport change
stops re-laying-out the inside of every message.

**Still open:** measuring against an off-flow mirror element would remove the
remaining per-keystroke layout entirely. It was deliberately not done, because
jsdom has no layout engine and would report any drift between the mirror and the
real textarea as a pass. It needs a real browser to verify.

## The probe is opt-in now

It shipped on, behind no flag, in 0.13 and 0.14. Every height change while no
turn was running called `ui_log`, a synchronous Tauri command that does five
blocking syscalls on the UI thread — and "no turn running" is exactly the state
you are in while typing. Turn it on for the next layout bug with
`localStorage.setItem("asb-jitter-probe", "1")` and reload.
