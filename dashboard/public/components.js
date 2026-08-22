/* ═══════════════════════════════════════════════════════════════════
   COMPONENT USAGE NOTES — the ONLY API tab modules may use
   ═══════════════════════════════════════════════════════════════════

   Tab modules (tab-work.js / tab-meetings.js / tab-system.js) register:
       window.Tabs = window.Tabs || {};
       Tabs.work = { load(filter) { ... } };   // .work / .meetings / .system
   `load(filter)` is called on tab switch and on the 60s refresh; `filter`
   is the hash sub-path string ("#work/overdue" -> "overdue"). Expected
   #work filters: overdue | due-today | p0 | blocked | waiting | done |
   decisions | commitments | people (interpret as: activate that chip or
   open that card section). Render into your container: #tab-work /
   #tab-meetings / #tab-system. Fetch with Promise.allSettled — one failed
   endpoint renders `<div class="load-error">…</div>` inline, others keep going.

   RULES: every dynamic TEXT value below is escaped internally — pass raw
   strings. Params documented as HTML (body, inner, expandBody, right,
   badges[]) are trusted HTML — build them from other Comp.* calls.
   NO inline style= anywhere except the --pct custom prop (Comp.progress,
   Comp.ringStat and Comp.duoBars emit it for you — never write style=
   yourself).

   TRUNCATION POLICY (v2): card titles, initiative names and ticket titles
   wrap up to 2 lines (line-clamp) — never single-line ellipsis. Always pass
   the FULL string; Comp.* put it in title= for you, never pre-truncate.
   Keep card `count` strings compact glyph style, e.g. "2 ⛔ · 8 ◷".

   MOTION (CSS-only; ALL of it disabled under prefers-reduced-motion):
   motion = signal — urgent glows/moves, healthy stays calm. Never put a
   motion class on a healthy row.
     .border-sweep   rotating red/orange alarm border. Add ONLY to
                     critical/breached cards & tiles. .stat--critical and an
                     escalation strip containing a ⛔ badge get it free.
     .pulse-dot      soft 2s pulse for live/health dots (standalone 8px dot,
                     colors via currentColor). The tab alert dot pulses free.
     Free, nothing to do: aurora background drift, card/tile hover sheen,
     active-tab glow, count-up hero numbers (see tick below).

   RGB AMBIENT (v3 — always-on spectrum chrome, "gaming rig meets command
   center"; decoration ONLY, never encodes data; static-rainbow fallback
   under prefers-reduced-motion):
     Free, nothing to do — already on the shell: header spectrum hairline
     (+ blurred glow halo), 🧠+title hue-cycling brand glow, RGB gradient
     underline on the active tab, brightened aurora, a soft always-on RGB
     shimmer border on every idle .stat-tile (serious/critical tiles keep
     their status treatment instead), and a 1px conic RGB border on EVERY
     .card at hover.
     .rgb-ring       opt-in PERMANENT 1px conic RGB border on any element
                     (e.g. frame the savings ring). Needs a border-radius to
                     look right (border-radius: inherit). NEVER combine with
                     .border-sweep — the alarm stays red and wins; RGB
                     selectors all exclude .border-sweep on purpose.
     The red .border-sweep alarm is UNCHANGED and stays reserved for
     critical/breach. RGB never repaints an alarm.

   U helpers
     U.esc(s)                        -> HTML-escaped string
     U.fmtAge(hours)                 -> "3h" | "2d"   (also "<1h")
     U.fmtDue('2026-07-15', today)   -> {text:'due in 4d', state:'overdue'|'today'|'soon'|'future'|null}
     U.mdToHtml(md)                  -> HTML (h1-h3, **bold**, *italic*, lists, links, `code`)
     await U.fetchJSON(url, opts)    -> parsed JSON; 15s timeout (opts.timeoutMs to override,
                                        e.g. {timeoutMs:45000} for slow /api/calendar); throws on !ok
     U.tickUp(el, 42)                (side effect) counts el's number 0→42 in
       ~400ms; instant set under prefers-reduced-motion. You rarely call it:
       Comp.statTile({…, tick:true}) emits data-tick on the value and
       components.js animates it after render automatically — and ONLY when
       the value changed since the last render (motion = signal).

   Comp builders (each RETURNS AN HTML STRING unless noted)
     Comp.statTile({key:'overdue', icon:'⏰', label:'Overdue', value:14,
                    sub:'oldest 12d', status:'serious', href:'#work/overdue',
                    tick:true})
       status: 'good'|'warn'|'serious'|'critical'|null. href -> renders <a>.
       tick:true (numeric value only) -> hero number counts up on change.
       status:'critical' auto-gets the .border-sweep alarm border.
     Comp.card({key:'tracker', icon:'🎯', title:'Tracker',
                count:'14 open · 3 overdue', status:'warn', body:rowsHtml, open:true})
       -> <details class="card" data-key>. Expansion is preserved across
       re-renders via data-key (UI.openKeys); `open` is only the default.
       Known keys get a domain hue wash automatically: tracker, top-tickets,
       activity, freshness (blue) · meetings, meetings-today, moms, routines,
       health (aqua) · commitments, cost (yellow) · portfolio (green) ·
       decisions, harness (violet) · people (magenta) · bots (orange).
     Comp.listRow({key:'t:T-123', icon:'🎫', title:'Fix the thing',
                   badges:[Comp.badge('p0','P0')], meta:'Teammate · 2d',
                   right:Comp.slaCountdown({remainingHours:-3}), dim:false,
                   expandBody:noteHtml + Comp.actionBar(t)})
       badges: array of Comp.badge() strings. right: HTML. expandBody
       optional -> row becomes an expandable <details data-key>.
     Comp.badge(kind, text)
       kind: 'good'|'warn'|'serious'|'critical' (auto-prefix ✓ ⚠ ⏳ ⛔ —
       color never alone) | 'p0'|'p1'|'p2' | 'cat-1'..'cat-8' (identity
       dot; categorical project map: 1 Marketplace, 2 B2C, 3 PIM,
       4 Seller Portal, 5 Example Program, 6 Work ID, 7 Ecommerce, 8 Other)
       | 'muted'.
     Comp.progress({pct:64, kind:'bar'|'ring', label:'64%'})
     Comp.slaCountdown({remainingHours:-3.5})  -> badge: over SLA (serious,
       critical past 24h over) / <24h left (warn) / else muted.
     Comp.personChip({slug:'Teammate', name:'Teammate K', role:'Design lead',
                      counts:{commitments:2, waiting:1, decisions:0},
                      relPath:'Clients/Work/People/Teammate.md'})
       click opens the Drawer on relPath (wired automatically).
     Comp.emptyState({icon:'🌤', title:'No meetings today', hint:'Enjoy the focus time'})
     Comp.staleWrap({state:'stale'|'dead'|'fresh', ageH:37, inner:cardHtml})
       'stale'/'dead' -> stale badge + dims content (.is-dim, opacity .55,
       never hidden); any other state returns inner unchanged. Use the
       matching /api/overview (or /api/metrics) freshness verdict.
     Comp.spark([3,5,2,8,4], {w:120, h:28})  -> recessive inline-SVG polyline.
     Comp.actionBar(ticket)  -> status select + priority select + comment
       input + Save. Save POSTs /api/action, shows a toast, then emits
       `psb:ticket-saved` {detail:{id,ticket}} on window — app.js already
       refetches overview + the active tab on that event; listen only if
       you need something extra.
     Comp.toast('Saved T-123', true)   (SIDE EFFECT, returns '' — shows a
       transient toast; false = error styling)
     Comp.drawer()  -> the drawer shell HTML (app.js injects it; don't call)
     Drawer.open('PRD title', 'Clients/Work/…/file.md')  fetches
       /api/file/<relPath> and renders markdown; ESC / overlay / ✕ close.
       Declarative alternative: any element with data-drawer-path (and
       optional data-drawer-title) opens the drawer on click.
     Drawer.openHtml('Title', html)  paints trusted HTML (build it from
       Comp.* calls) into the SAME drawer shell — same overlay/✕/ESC close
       wiring as Drawer.open, no fetch. Use for drafts/details that don't
       live in a repo file.

   v2 builders (A2 visual system) — same rules as above
     Comp.breadcrumb([{label:'Work', href:'#work'}, {label:'OTO Aggregator'}])
       -> <nav> trail for drill views; the LAST part is the current page and
       never renders as a link (href ignored there).
     Comp.jiraChip('ABC-123')  -> compact mono ticket chip, opens the right
       Jira in a new tab. Prefix routing: MSP- / MBA- / STOR- ->
       examplevendor.atlassian.net; MP-, MPS- and every other prefix ->
       yourcompany.atlassian.net. Visually distinct from badges.
     Comp.taskRow({ticket:{id:'T-9', title:'OTO webhook retries',
                   priority:'P1', jira_key:'ABC-123', project:'Marketplace',
                   due:'2026-07-15', owner:'Teammate', status:'in_progress'},
                   children:[{ticket:{id:'T-9a', title:'…', status:'done'}}],
                   depth:0, expandBody:noteHtml + Comp.actionBar(t)})
       -> hierarchy row: priority badge + jiraChip (only when jira_key) +
       project badge (categorical map above) + due badge + owner meta.
       children[] (ticket objects, or {ticket, children} to nest) render as
       an indented block one level down (depth 1 = subtasks: smaller, muted
       left rail) and the parent row shows a "1/3 done" rollup.
       status:'done' dims a row. Optional `today:'2026-07-11'` overrides the
       browser date for due math. expandBody (HTML, optional) makes the row
       itself an expandable <details data-key="task:<id>"> — same pattern
       as listRow — so notes/comments/ActionBar fuse INTO the row (one row
       per ticket, never a paired strip); works with or without children.
     Comp.chaseButton({owner:'Teammate', what:'image CR approval',
                       sourceUrl:'https://…/slack-permalink', slaHours:24})
       -> small "⏱ Chase" button. Click POSTs /api/waiting-add
       {owner, what, sla_hours, source_url} then toasts
       "Masuk watchdog: <id>" and dispatches `psb:waiting-added`
       {detail:{id, owner, what}} on window. Wiring is delegated inside
       components.js — just render it, no listeners needed.
     Comp.ringStat({pct:64, label:'Saved', sub:'$1,240 this month'})
       -> big conic ring with the % in the middle (savings/completion).
     Comp.duoBars([{date:'2026-07-08', a:12, b:5}, …])
       -> paired thin daily bars, a=spent (cat-1 blue) vs b=saved (cat-2
       green) — categorical colors, NOT status colors — normalized to the
       max across both series; hover shows "date — spent a · saved b".
     Comp.actionItemRow({id:'ai-3', text:'Send OTO pricing sheet to YourManager',
                         source_type:'fathom', source_ref:'Work sync 9 Jul',
                         permalink:'https://fathom.video/…'})
       -> meeting action-item row with a "→ Jadiin ticket" button. The row
       carries data-commitment-id (from id). Click POSTs /api/action
       {action:'create', title:text (≤120 chars), priority:'P1',
       project:'Other', note:'From meeting: '+source_ref}, toasts, and
       dispatches `psb:ticket-saved` with commitment_id echoed from the
       row, so app.js links commitment→ticket + refreshes. Delegated
       wiring lives here too.
     Comp.logPanel({job:'slack-sweep', tail:['…line', '…line'],
                    last_heartbeat:{job:'slack-sweep', ts_wib:'2026-07-11T02:10:00',
                                    status:'ok', summary:'…'}})
       -> monospace scrollable tail + "♥ 2h ago" heartbeat line. Feed it
       GET /api/job-log?job=… ({job, tail[], last_heartbeat}) VERBATIM:
       last_heartbeat is the full heartbeat row object shown above, and
       logPanel extracts the timestamp (ts_wib/ts) itself — a plain ISO
       string or epoch number is also accepted. Use inside a Drawer body
       or a row expandBody. tail: string[] (one string ok).

   v3 builders (D1 foundation) — same rules as above
     Comp.linkChips(links, opts?)  -> labeled reference chips for a row/card.
       links: array of URL strings or {url, label} (label overrides the
       auto one); a bare string/object is accepted too. Source auto-detect:
       slack.com→"💬 Slack" · docs.google.com→"📄 GDoc" ·
       drive.google.com→"📄 Drive" · *.atlassian.net→"🎫 Jira" ·
       fathom.video→"🎥 Fathom" · figma.com→"🎨 Figma" · miro.com→"🗺 Miro" ·
       "#hash"→internal view link · any other http(s)→"🔗 <domain>" ·
       anything else (file-ish relative path)→"📁 file" button that opens
       the Drawer on that path (markdown renders best; other text degrades
       gracefully). http(s) chips get target=_blank rel=noopener. Empty /
       missing input -> ''. opts.max caps the count and appends a muted
       "+N" chip. Full URL always in title= (hover). Use these to put
       reference links (Slack thread, PRD, Jira, Fathom) on tasks/rows:
         Comp.listRow({…, expandBody: Comp.linkChips(t.links) + …})
     Comp.donut(segments, {size=96, label, legend})  -> SVG donut chart.
       segments: [{label, value, kind}] where kind: 'cat-1'..'cat-8' |
       'p0'|'p1'|'p2' — NO status colors as series (anything else folds to
       cat-8). Thin 10px ring, 2px surface gaps between segments, hover
       <title> per segment ("label — value"), center shows total (text
       tokens) + optional label. Legend chips render automatically for ≥2
       segments (dataviz rule); pass legend:false to suppress when an
       adjacent table/list already identifies the slices. Zero-total ->
       muted empty ring with 0.
     Comp.distBar(items, {label})  -> one horizontal 100%-stacked
       distribution bar (12px tall, 2px gaps, hover title with count + %)
       with legend chips underneath (swatch + label + n, text in text
       tokens). items: [{label, value, kind}], same kind rules as donut.
       Zero/empty -> ''.
     Comp.miniBars(points, {w=140, h=40, label, kind='cat-1'})  -> tiny
       recessive vertical bar series (meetings/week, done/day). points:
       numbers or {label, value}; hover title per bar (full-height invisible
       hit target, so thin bars are easy to hover); ONE categorical color
       for the whole series (kind override allowed, cat-N or pN only).
     Comp.backButton({href, label='Back'})  -> "← Back" pill. With href ->
       plain <a> that navigates natively; without href -> button that
       history.back()s via delegated wiring (nothing to hook up).
     Drawer.openWide(title, html)  -> same drawer shell at ~72% viewport
       width (min 680px, never past 96vw) for the initiative slide-over.
       Trusted HTML from Comp.* calls; ESC/✕/overlay close identical to
       Drawer.openHtml. Any later Drawer.open/openHtml resets to normal
       width automatically.

   v4 builders (E2 visual foundation) — same rules as above
     EXPAND AFFORDANCE (free, CSS-only): every <details class="card"> and
       every expandable <details class="row"> (listRow/taskRow with
       expandBody) now shows a circled ▸ chevron that rotates to ▾ when
       open and brightens on hover. Nothing to do — keep passing
       expandBody and the chevron appears; plain non-expandable div.row
       never grows one, so "has a chevron" === "click to expand".
     Comp.aiButton({kind:'draft-prd', ref:'T-123', label:'🤖 AI, work on it'})
       -> button that POSTs /api/ai-task {kind, ref} on click (wiring is
       delegated in components.js — just render it). On {ok, id} the
       button swaps itself into an "⏳ AI running…" status pill and the
       SHARED POLLER (one interval for ALL runs — never one per button)
       polls GET /api/ai-task?id=<id> every 5s for up to 8 min:
         done  -> pill "✅ Done · see result"; clicking it opens the
                  wide drawer with the run's tail (Comp.logPanel) + the
                  draft itself when the run carries result_path (fetched
                  via /api/file/<result_path>). `psb:ai-done`
                  {detail:{id, kind, ref, run}} fires on window once.
         error -> pill "⛔ Failed · see log" + toast (drawer shows tail)
         8 min -> pill "⏱ Timeout · see log", polling stops.
       POST failure or HTTP 409 -> toast + button reverts (no pill).
       Poller status vocabulary (defensive server contract): done/ok/
       success/completed -> done · error/fail/failed -> error · anything
       else (running/pending/queued/absent) keeps polling. The poll
       response may be the run object itself or wrapped as {run:{…}}.
       Re-render safe: aiButton checks the poller registry first — if a
       run for the same {kind, ref} is already tracked it renders that
       run's CURRENT pill instead of a fresh button, so 60s tab
       re-renders never lose a running/done state.
     Comp.aiResultPill({run})  -> pill for a PRE-EXISTING run (e.g. from
       GET /api/ai-task?list=1). run: {id, status, kind, ref, …,
       result_path?}. A still-running run is adopted by the shared
       poller (deliberate side effect); done/error runs render their
       clickable pill immediately. Same drawer + psb:ai-done behavior.
     Comp.ticketChip('T-123')  -> small mono "🎫 T-123" chip. href is
       '#work' and it carries data-ticket-id="T-123" — the Work tab may
       read that attr to highlight/scroll to the ticket. Internal link
       (never target=_blank); visually parallel to jiraChip.
     Comp.harnessMap(mapData)  -> the full 🗺 architecture map. Feed
       /api/harness-map's payload verbatim ({groups:[{key, label,
       nodes:[{id, label, desc, status, ref}]}]} — a bare groups array
       is also accepted). Renders one flex column per group: styled
       small-caps glowing header + rounded node CARDS (status dot +
       label + status word + 2-line desc; vocabulary ok|warn|fail|idle|
       gated, gated shows 🔒), with animated RGB bezier connector lanes
       BETWEEN columns (dash-flow + hue-cycle; static dashes under
       reduced motion; hidden <900px where the columns stack as rows and
       nodes re-wrap into a grid). CLICK CONTRACT (unchanged from the
       old chip map): every node card is
         <button class="map-node …" data-node-id="<node.id>">
       — tab-system's existing `#tab-system .map-node` delegated
       listener keeps working untouched; harnessMap wires NO clicks.
     Comp.trendCard({title:'Docs dibuat', icon:'📄', points:[…daily
       numbers or {label, value}…], total:38, unit:'docs · 14d',
       kind:'cat-1', good:'up'})
       -> the "momentum" chart card for Today: big count-up total +
       delta of the last 7 points vs the 7 before (▲/▼ colored good/
       warn, DIRECTION-AWARE via good:'up'|'down' — pass 'down' when a
       falling number is the good direction, e.g. overdue) + a gradient-
       filled smooth area chart in ONE categorical color (kind:
       cat-1..8 / p0..2 only — status colors are rejected and fold to
       cat-8) with per-point hover values and a glowing last-point dot.
       total omitted -> sum of points. <8 points -> delta line hidden.
       Lay 4 of them in `<div class="momentum-band">…</div>`: a grid
       that goes 4 -> 2 (≤1100px) -> 1 (≤560px) columns.
     Comp.spark v2: Comp.spark(points, {w, h, label, showLast, showAxis})
       — points may now be {label, value} objects. With NO new opts the
       output is the exact old compact recessive polyline. label and/or
       showLast wrap it with a header line (pass the FULL ready-made
       string as label, e.g. 'Aktivitas · 30d: 191 events') + hover
       tooltips per point, a last-value dot on the line and the last
       number at the right of the header; showAxis adds a faint
       baseline. Labeled sparks use cat-1 (categorical, single series).

   DELEGATED CLICK HANDLERS (components.js owns ONE document-level click
   listener — full inventory, keep this list true):
     .chase-btn (button, preventDefault) · .make-ticket-btn (button,
     preventDefault) · .ai-btn (button, preventDefault → POST
     /api/ai-task, swaps itself to the status pill) · .ai-pill[data-ai-id]
     when NOT running (button, preventDefault → opens the run's wide
     drawer) · .action-save (button) · .back-btn[data-back]
     (button, history.back) · [data-drawer-path] (preventDefault + Drawer,
     EXCEPT when the actual click landed on a plain <a href> nested inside
     the opener — the anchor always wins and is never swallowed).
     app.js adds its own listeners for: .tab-btn (inside #tab-nav),
     .draft-escalation-btn, .draft-copy-btn, the #tab-today SLA tile, and
     .action-item-row a.prep-link with a RELATIVE href (relative paths open
     in the Drawer; real https:// links pass through). None of them
     preventDefault a plain anchor either. RULE for new handlers: match via
     closest('[data-…]'/'.specific-class') as narrowly as possible and
     never preventDefault unless YOUR element matched.

   Window events: `psb:ticket-saved` {id, ticket, commitment_id?} (actionBar
   save + Jadiin ticket — commitment_id only on Jadiin-ticket creates, read
   off the triggering row's data-commitment-id; '' when the row has none and
   absent on actionBar saves), `psb:waiting-added` {id, owner, what} (chase
   button) and
   `psb:ai-done` {id, kind, ref, run} (shared AI poller, fired once when a
   tracked run completes successfully). app.js refetches on
   psb:ticket-saved; listen yourself for psb:waiting-added /psb:ai-done if
   your tab shows those items.

   Other CSS you may use (already styled): .momentum-band (4→2→1 grid for
   trendCards), .rows (row list wrapper),
   .chips + .chip(.is-active) for filter chips, .section-label,
   .grid-cards / .chip-grid / .two-col / .stack layout wrappers,
   .time-pill, .prep-link, .skeleton > .skeleton-line(.w-60/.w-80),
   .load-error, .escalation-strip + .escalation-title, .row-subtext
   (muted xs context line that wraps — place directly after its row inside
   .rows, or inside a .row-expand), the motion classes .border-sweep /
   .pulse-dot documented above, and the v3 chrome: .rgb-ring (permanent RGB
   border), .back-btn, .link-chips/.link-chip, .chart-legend + .legend-item
   /.legend-dot/.legend-n, and the .k-cat-1..8/.k-p0..2 swatch utilities
   (background-color only — data marks + legend dots, never text).
   ═══════════════════════════════════════════════════════════════════ */
'use strict';

const U = {
  esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  },

  fmtAge(hours) {
    const h = Math.abs(Number(hours) || 0);
    if (h < 1) return '<1h';
    if (h < 48) return `${Math.round(h)}h`;
    return `${Math.round(h / 24)}d`;
  },

  fmtDue(dueStr, today) {
    if (!dueStr) return { text: '', state: null };
    const d = Math.round((Date.parse(dueStr) - Date.parse(today)) / 86400000);
    if (Number.isNaN(d)) return { text: dueStr, state: null };
    if (d < 0) return { text: `${-d}d overdue`, state: 'overdue' };
    if (d === 0) return { text: 'due today', state: 'today' };
    if (d === 1) return { text: 'due tomorrow', state: 'soon' };
    if (d <= 7) return { text: `due in ${d}d`, state: 'soon' };
    return { text: `due ${dueStr.slice(5)}`, state: 'future' };
  },

  mdToHtml(md) {
    const inline = s => s
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      /* [text](url): http(s) opens a new tab; #hash is an internal view link;
         any other value is a repo-relative path (e.g. a BRD/PRD .md) — render
         it as a drawer opener so the doc is readable in-place instead of
         leaking through as raw [text](path) literal text (server serves it via
         /api/file/, same as the declarative data-drawer-path openers). */
      .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_m, text, url) => {
        if (/^https?:/i.test(url)) return `<a href="${url}" target="_blank" rel="noopener">${text}</a>`;
        if (/^#/.test(url)) return `<a href="${url}">${text}</a>`;
        return `<a class="doc-link" data-drawer-path="${url}" data-drawer-title="${text}">${text}</a>`;
      })
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:]|$)/g, '$1<em>$2</em>');
    const out = [];
    let listTag = null, para = [], quote = [];
    const flushPara = () => { if (para.length) { out.push(`<p>${inline(para.join(' '))}</p>`); para = []; } };
    const closeList = () => { if (listTag) { out.push(`</${listTag}>`); listTag = null; } };
    const flushQuote = () => {
      if (quote.length) { out.push(`<blockquote>${quote.map(q => `<p>${inline(q)}</p>`).join('')}</blockquote>`); quote = []; }
    };
    const openList = tag => {
      if (listTag !== tag) { closeList(); out.push(`<${tag}>`); listTag = tag; }
    };
    for (const raw of U.esc(md || '').split('\n')) {
      const ln = raw.trimEnd();
      /* input was HTML-escaped BEFORE this loop, so a '>' quote marker is '&gt;' */
      const bq = ln.match(/^\s*&gt;\s?(.*)/);
      const h = ln.match(/^(#{1,3})\s+(.+)/);
      const li = ln.match(/^\s*[-*]\s+(.+)/);
      /* numbered lines ("1. …") are ORDERED LIST items — without this branch a
         numbered Top-5 collapses into one giant unreadable paragraph */
      const oli = ln.match(/^\s*\d+[.)]\s+(.+)/);
      const hr = /^\s*(?:-{3,}|_{3,}|\*{3,})\s*$/.test(ln);
      if (bq) { flushPara(); closeList(); if (bq[1].trim()) quote.push(bq[1]); }
      else if (h) { flushPara(); closeList(); flushQuote(); out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); }
      else if (hr) { flushPara(); closeList(); flushQuote(); out.push('<hr>'); }
      else if (li) { flushPara(); flushQuote(); openList('ul'); out.push(`<li>${inline(li[1])}</li>`); }
      else if (oli) { flushPara(); flushQuote(); openList('ol'); out.push(`<li>${inline(oli[1])}</li>`); }
      else if (!ln.trim()) { flushPara(); closeList(); flushQuote(); }
      else para.push(ln);
    }
    flushPara(); closeList(); flushQuote();
    return out.join('\n');
  },

  async fetchJSON(url, opts = {}) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), opts.timeoutMs || 15000);
    try {
      const res = await fetch(url, { ...opts, signal: ctrl.signal });
      if (!res.ok) throw new Error(`HTTP ${res.status} on ${url}`);
      return await res.json();
    } finally {
      clearTimeout(timer);
    }
  },

  /* count a number up to `value` in ~400ms (instant under reduced motion) */
  tickUp(el, value) {
    if (!el) return;
    const target = Number(value);
    if (!Number.isFinite(target) ||
        (window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches)) {
      el.textContent = String(value);
      return;
    }
    const dur = 400;
    const t0 = performance.now();
    el.textContent = '0';
    const frame = now => {
      const k = Math.min(1, (now - t0) / dur);
      const eased = 1 - Math.pow(1 - k, 3);
      el.textContent = String(Math.round(target * eased));
      if (k < 1) requestAnimationFrame(frame);
    };
    requestAnimationFrame(frame);
  },
};

const STATUS_ICONS = { good: '✓', warn: '⚠', serious: '⏳', critical: '⛔' };
const STATUS_WORDS = { good: 'ok', warn: 'warn', serious: 'at risk', critical: 'failing' };

/* fixed project -> categorical color map (same order as the badge docs) */
const PROJECT_CAT = {
  'Marketplace': 'cat-1', 'B2C': 'cat-2', 'PIM': 'cat-3', 'Seller Portal': 'cat-4',
  'Example Program': 'cat-5', 'Work ID': 'cat-6', 'Ecommerce': 'cat-7', 'Other': 'cat-8',
};

/* Jira prefix routing: ExampleVendor boards vs Work's own instance */
const EXAMPLEVENDOR_PREFIXES = ['MSP', 'MBA', 'STOR'];

const Comp = {
  /* expansion default hook — app.js overrides via UI.isOpen(key, def) */
  _open(key, def) {
    return (window.UI && typeof UI.isOpen === 'function') ? UI.isOpen(key, def) : !!def;
  },

  statTile({ key, icon, label, value, sub, status, href, tick }) {
    const cls = status ? ` stat--${status}` : '';
    const ic = status ? STATUS_ICONS[status] : '';
    const num = Number(value);
    const tickAttr = (tick && Number.isFinite(num)) ? ` data-tick="${num}"` : '';
    const inner = `
      <div class="stat-label"><span>${U.esc(icon || '')}</span><span>${U.esc(label)}</span></div>
      <div class="stat-value"${tickAttr}>${U.esc(String(value))}</div>
      <div class="stat-sub">${ic ? `<span>${ic}</span>` : ''}<span title="${U.esc(sub || '')}">${U.esc(sub || '')}</span></div>`;
    return href
      ? `<a class="stat-tile${cls}" data-key="${U.esc(key)}" href="${U.esc(href)}">${inner}</a>`
      : `<div class="stat-tile${cls}" data-key="${U.esc(key)}">${inner}</div>`;
  },

  card({ key, icon, title, count, status, body, open = false }) {
    const isOpen = Comp._open(key, open);
    return `<details class="card" data-key="${U.esc(key)}"${isOpen ? ' open' : ''}>
      <summary>
        <span class="card-icon">${U.esc(icon || '')}</span>
        <span class="card-title" title="${U.esc(title)}">${U.esc(title)}</span>
        ${count ? `<span class="card-count num">${U.esc(count)}</span>` : ''}
        <span class="card-spacer"></span>
        ${status ? Comp.badge(status, STATUS_WORDS[status] || status) : ''}
        <span class="card-chevron">▸</span>
      </summary>
      <div class="card-body">${body || ''}</div>
    </details>`;
  },

  listRow({ key, icon, title, badges = [], meta, right, dim, expandBody }) {
    const inner = `
      ${icon ? `<span class="row-icon">${U.esc(icon)}</span>` : ''}
      <span class="row-title" title="${U.esc(title)}">${U.esc(title)}</span>
      ${badges.length ? `<span class="row-badges">${badges.join('')}</span>` : ''}
      ${meta ? `<span class="row-meta" title="${U.esc(meta)}">${U.esc(meta)}</span>` : ''}
      ${right ? `<span class="row-right">${right}</span>` : ''}`;
    const dimCls = dim ? ' is-dim' : '';
    if (expandBody) {
      const isOpen = Comp._open(key, false);
      return `<details class="row${dimCls}" data-key="${U.esc(key)}"${isOpen ? ' open' : ''}>
        <summary>${inner}</summary>
        <div class="row-expand">${expandBody}</div>
      </details>`;
    }
    return `<div class="row${dimCls}" data-key="${U.esc(key)}">${inner}</div>`;
  },

  badge(kind, text) {
    if (STATUS_ICONS[kind]) {
      return `<span class="badge badge--${kind}">${STATUS_ICONS[kind]} ${U.esc(text)}</span>`;
    }
    if (/^cat-[1-8]$/.test(kind || '')) {
      return `<span class="badge badge--cat badge--${kind}"><span class="badge-dot"></span>${U.esc(text)}</span>`;
    }
    const cls = /^(p0|p1|p2|muted)$/.test(kind || '') ? kind : 'muted';
    return `<span class="badge badge--${cls}">${U.esc(text)}</span>`;
  },

  progress({ pct, kind = 'bar', label }) {
    const p = Math.max(0, Math.min(100, Math.round(Number(pct) || 0)));
    const lab = label ? `<span class="progress-label">${U.esc(label)}</span>` : '';
    const shape = kind === 'ring' ? 'progress-ring' : 'progress-bar';
    return `<span class="progress"><span class="${shape}" style="--pct:${p}"></span>${lab}</span>`;
  },

  slaCountdown({ remainingHours }) {
    if (remainingHours == null) return Comp.badge('muted', 'no SLA');
    const h = Number(remainingHours);
    if (h < -24) return Comp.badge('critical', `${U.fmtAge(-h)} over SLA`);
    if (h < 0) return Comp.badge('serious', `${U.fmtAge(-h)} over SLA`);
    if (h < 24) return Comp.badge('warn', `${Math.max(1, Math.round(h))}h left`);
    return Comp.badge('muted', `${U.fmtAge(h)} left`);
  },

  personChip({ slug, name, role, counts = {}, relPath }) {
    const initials = String(name || '?').trim().split(/\s+/)
      .map(w => w[0]).slice(0, 2).join('').toUpperCase();
    const drawer = relPath
      ? ` data-drawer-path="${U.esc(relPath)}" data-drawer-title="${U.esc(name || relPath)}"` : '';
    const c = counts;
    return `<button class="person-chip" data-key="person:${U.esc(slug || name || '?')}"${drawer}>
      <span class="person-avatar">${U.esc(initials)}</span>
      <span class="person-main">
        <div class="person-name" title="${U.esc(name || '')}">${U.esc(name || '(unknown)')}</div>
        <div class="person-role" title="${U.esc(role || '')}">${U.esc(role || '')}</div>
      </span>
      <span class="person-counts">
        ${c.commitments ? `<span title="open commitments to them">📤 ${U.esc(c.commitments)}</span>` : ''}
        ${c.waiting ? `<span title="waiting on them">⏳ ${U.esc(c.waiting)}</span>` : ''}
        ${c.decisions ? `<span title="open decisions">⚖ ${U.esc(c.decisions)}</span>` : ''}
      </span>
    </button>`;
  },

  emptyState({ icon, title, hint }) {
    return `<div class="empty-state">
      <div class="empty-icon">${U.esc(icon || '·')}</div>
      <div class="empty-title">${U.esc(title || 'Nothing here')}</div>
      ${hint ? `<div class="empty-hint">${U.esc(hint)}</div>` : ''}
    </div>`;
  },

  staleWrap({ state, ageH, inner }) {
    if (state !== 'stale' && state !== 'dead') return inner || '';
    const dead = state === 'dead';
    return `<div class="stale-wrap">
      <span class="stale-note${dead ? ' stale-note--dead' : ''}">⚠ ${dead ? 'dead data' : 'stale'} · ${U.esc(U.fmtAge(ageH))} old</span>
      <div class="is-dim">${inner || ''}</div>
    </div>`;
  },

  /* v2: points may be numbers or {label, value}. With no new opts the
     output is the exact old compact polyline. label/showLast add the
     header line + last-value dot + hover tooltips; showAxis adds a
     faint baseline. */
  spark(points, opts = {}) {
    const w = opts.w || 120, h = opts.h || 28;
    const pts = (Array.isArray(points) ? points : []).map(p =>
      (p && typeof p === 'object')
        ? { label: p.label || '', value: Number(p.value) }
        : { label: '', value: Number(p) })
      .filter(p => Number.isFinite(p.value));
    if (pts.length < 2) return '';
    const vals = pts.map(p => p.value);
    const max = Math.max(...vals), min = Math.min(...vals);
    const span = (max - min) || 1;
    const step = (w - 4) / (pts.length - 1);
    const xy = pts.map((p, i) =>
      [2 + i * step, h - 3 - ((p.value - min) / span) * (h - 6)]);
    const coords = xy.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
    const labeled = !!(opts.label || opts.showLast || opts.showAxis);
    const axis = opts.showAxis
      ? `<line class="spark-axis" x1="2" y1="${h - 1.5}" x2="${w - 2}" y2="${h - 1.5}"/>` : '';
    const slots = labeled ? pts.map((p, i) => {
      const x0 = Math.max(0, 2 + i * step - step / 2);
      return `<rect x="${x0.toFixed(1)}" y="0" width="${step.toFixed(1)}" height="${h}" fill="transparent">` +
        `<title>${U.esc(p.label ? `${p.label} — ${p.value}` : String(p.value))}</title></rect>`;
    }).join('') : '';
    const last = xy[xy.length - 1];
    const dot = opts.showLast
      ? `<circle class="spark-dot" cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="2.5"/>` : '';
    const aria = labeled ? ` role="img" aria-label="${U.esc(opts.label || 'sparkline')}"` : ' aria-hidden="true"';
    const svg = `<svg class="spark${labeled ? ' spark--labeled' : ''}" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"${aria}>${axis}<polyline points="${coords}"/>${slots}${dot}</svg>`;
    if (!opts.label && !opts.showLast) return svg;
    const lastVal = opts.showLast
      ? `<span class="spark-lastval num">${U.esc(String(pts[pts.length - 1].value))}</span>` : '';
    return `<span class="spark-wrap">
      <span class="spark-head">${opts.label ? `<span class="spark-label">${U.esc(opts.label)}</span>` : ''}${lastVal}</span>
      ${svg}</span>`;
  },

  actionBar(t) {
    const opt = (list, cur) => list.map(v =>
      `<option value="${v}"${v === cur ? ' selected' : ''}>${v.replace('_', ' ')}</option>`).join('');
    return `<div class="action-bar" data-ticket-id="${U.esc(t.id)}">
      <select data-field="status" aria-label="status">${opt(['todo', 'in_progress', 'blocked', 'waiting', 'done'], t.status)}</select>
      <select data-field="priority" aria-label="priority">${opt(['P0', 'P1', 'P2'], t.priority)}</select>
      <input type="text" data-field="comment" placeholder="Add a comment…" />
      <button class="action-save">Save</button>
    </div>`;
  },

  /* action (optional): {label:'Undo', onClick:async fn} renders a button in the
     toast (used for reversible actions — mis-click safety). Such toasts linger
     8s instead of 3.2s and stay clickable; clicking runs onClick + dismisses. */
  toast(msg, ok = true, action = null) {
    let root = document.getElementById('toast-root');
    if (!root) {
      root = document.createElement('div');
      root.id = 'toast-root';
      root.className = 'toast-root';
      document.body.appendChild(root);
    }
    const el = document.createElement('div');
    el.className = `toast ${ok ? 'toast--ok' : 'toast--err'}${action ? ' has-action' : ''}`;
    el.textContent = `${ok ? '✓' : '⛔'} ${msg}`;
    if (action && action.label && typeof action.onClick === 'function') {
      const btn = document.createElement('button');
      btn.className = 'toast-action';
      btn.textContent = action.label;
      btn.addEventListener('click', async e => {
        e.preventDefault();
        btn.disabled = true;
        try { await action.onClick(); } catch (err) { Comp.toast(`Failed: ${err.message}`, false); }
        el.classList.add('is-leaving'); setTimeout(() => el.remove(), 300);
      });
      el.appendChild(btn);
    }
    const ttl = action ? 8000 : 3200;
    setTimeout(() => { el.classList.add('is-leaving'); setTimeout(() => el.remove(), 300); }, ttl);
    return '';
  },

  /* ── v2 builders ─────────────────────────────────────────────── */

  breadcrumb(parts) {
    const list = (Array.isArray(parts) ? parts : []).filter(Boolean);
    if (!list.length) return '';
    const html = list.map((p, i) => {
      const last = i === list.length - 1;
      const label = U.esc(p.label ?? '');
      if (!last && p.href) return `<a class="crumb" href="${U.esc(p.href)}">${label}</a>`;
      return `<span class="crumb${last ? ' crumb-current' : ''}"${last ? ' aria-current="page"' : ''}>${label}</span>`;
    }).join('<span class="crumb-sep">›</span>');
    return `<nav class="breadcrumb" aria-label="Breadcrumb">${html}</nav>`;
  },

  jiraChip(key) {
    const k = String(key || '').trim().toUpperCase();
    if (!k) return '';
    const prefix = k.split('-')[0];
    const host = EXAMPLEVENDOR_PREFIXES.includes(prefix)
      ? 'examplevendor.atlassian.net' : 'yourcompany.atlassian.net';
    return `<a class="jira-chip" href="https://${host}/browse/${encodeURIComponent(k)}"` +
      ` target="_blank" rel="noopener" title="Open ${U.esc(k)} in Jira">${U.esc(k)}</a>`;
  },

  taskRow({ ticket, children, depth = 0, today, expandBody } = {}) {
    const t = ticket || {};
    const kids = Array.isArray(children) ? children : [];
    const id = t.id ?? t.jira_key ?? t.title ?? '?';
    const title = t.title || t.id || '(untitled)';

    const badges = [];
    if (t.priority) badges.push(Comp.badge(String(t.priority).toLowerCase(), t.priority));
    if (t.jira_key) badges.push(Comp.jiraChip(t.jira_key));
    if (t.project) badges.push(Comp.badge(PROJECT_CAT[t.project] || 'cat-8', t.project));
    if (kids.length) {
      const done = kids.filter(c => ((c && c.ticket) || c || {}).status === 'done').length;
      badges.push(Comp.badge('muted', `${done}/${kids.length} done`));
    }

    let right = '';
    if (t.due) {
      const d = new Date();
      const localToday = today ||
        `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
      const due = U.fmtDue(t.due, localToday);
      if (due.text) {
        const kind = due.state === 'overdue' ? 'serious' : due.state === 'today' ? 'warn' : 'muted';
        right = Comp.badge(kind, due.text);
      }
    }

    const dimCls = t.status === 'done' ? ' is-dim' : '';
    const subCls = depth > 0 ? ' task-row--sub' : '';
    const inner = `
      <span class="row-title" title="${U.esc(title)}">${U.esc(title)}</span>
      ${badges.length ? `<span class="row-badges">${badges.join('')}</span>` : ''}
      ${t.owner ? `<span class="row-meta" title="${U.esc(t.owner)}">${U.esc(t.owner)}</span>` : ''}
      ${right ? `<span class="row-right">${right}</span>` : ''}`;
    /* expandBody -> the row itself is the expandable (same shape as listRow):
       one row per ticket, notes/actions inside it — never a paired strip */
    let row;
    if (expandBody) {
      const isOpen = Comp._open(`task:${id}`, false);
      row = `<details class="row task-row${subCls}${dimCls}" data-key="task:${U.esc(id)}"${isOpen ? ' open' : ''}>
        <summary>${inner}</summary>
        <div class="row-expand">${expandBody}</div>
      </details>`;
    } else {
      row = `<div class="row task-row${subCls}${dimCls}" data-key="task:${U.esc(id)}">${inner}
      </div>`;
    }
    if (!kids.length) return row;

    const childHtml = kids.map(c => (c && c.ticket)
      ? Comp.taskRow({ ...c, depth: depth + 1, today })
      : Comp.taskRow({ ticket: c, depth: depth + 1, today })).join('');
    return `<div class="task-group" data-key="taskgrp:${U.esc(id)}">${row}
      <div class="task-children">${childHtml}</div>
    </div>`;
  },

  chaseButton({ owner, what, sourceUrl, slaHours = 24 } = {}) {
    return `<button class="chase-btn"
      data-owner="${U.esc(owner || '')}"
      data-what="${U.esc(what || '')}"
      data-source-url="${U.esc(sourceUrl || '')}"
      data-sla-hours="${Number(slaHours) || 24}"
      title="Track in the waiting-on watchdog">⏱ Chase</button>`;
  },

  ringStat({ pct, label, sub } = {}) {
    const p = Math.max(0, Math.min(100, Math.round(Number(pct) || 0)));
    return `<div class="ring-stat">
      <div class="ring-stat-ring" style="--pct:${p}"><span class="ring-stat-pct">${p}%</span></div>
      ${label ? `<div class="ring-stat-label">${U.esc(label)}</div>` : ''}
      ${sub ? `<div class="ring-stat-sub">${U.esc(sub)}</div>` : ''}
    </div>`;
  },

  duoBars(days) {
    const list = (Array.isArray(days) ? days : []).map(d => ({
      date: (d && d.date) || '',
      a: Math.max(0, Number(d && d.a) || 0),
      b: Math.max(0, Number(d && d.b) || 0),
    }));
    if (!list.length) return '';
    const max = Math.max(1, ...list.map(d => Math.max(d.a, d.b)));
    const bars = list.map(d => {
      const pa = Math.round((d.a / max) * 100);
      const pb = Math.round((d.b / max) * 100);
      return `<div class="duo-day" title="${U.esc(`${d.date} — spent ${d.a} · saved ${d.b}`)}">
        <span class="duo-bar duo-bar--a" style="--pct:${pa}"></span>
        <span class="duo-bar duo-bar--b" style="--pct:${pb}"></span>
      </div>`;
    }).join('');
    return `<div class="duo-bars" role="img" aria-label="daily spent vs saved bars">${bars}</div>`;
  },

  actionItemRow({ id, text, source_type, source_ref, permalink } = {}) {
    const txt = text || '(empty action item)';
    const link = permalink
      ? `<a class="prep-link" href="${U.esc(permalink)}" target="_blank" rel="noopener">source ↗</a>` : '';
    return `<div class="row action-item-row" data-key="ai:${U.esc(id ?? txt.slice(0, 40))}" data-commitment-id="${U.esc(id ?? '')}">
      <span class="row-icon">☑️</span>
      <span class="row-title" title="${U.esc(txt)}">${U.esc(txt)}</span>
      ${source_type ? `<span class="row-badges">${Comp.badge('muted', source_type)}</span>` : ''}
      <span class="row-right">${link}<button class="make-ticket-btn"
        data-ai-text="${U.esc(txt)}"
        data-ai-ref="${U.esc(source_ref || '')}">→ Jadiin ticket</button></span>
    </div>`;
  },

  logPanel({ job, tail, last_heartbeat } = {}) {
    /* last_heartbeat may be the FULL heartbeat row object /api/job-log
       returns ({job, ts_wib, status, …}) or a plain ISO string / epoch
       number — normalize to a timestamp here so callers pass it verbatim */
    let hbRaw = last_heartbeat;
    if (hbRaw != null && typeof hbRaw === 'object') hbRaw = hbRaw.ts_wib ?? hbRaw.ts ?? null;
    let hb = 'no heartbeat';
    if (hbRaw != null && hbRaw !== '') {
      const ms = typeof hbRaw === 'number'
        ? (hbRaw > 1e12 ? hbRaw : hbRaw * 1000)
        : Date.parse(hbRaw);
      hb = Number.isFinite(ms)
        ? `♥ ${U.fmtAge((Date.now() - ms) / 3600000)} ago`
        : `♥ ${hbRaw}`;
    }
    const lines = Array.isArray(tail) ? tail : String(tail ?? '').split('\n');
    const body = lines.join('\n').trim();
    return `<div class="log-panel" data-key="log:${U.esc(job || '')}">
      <div class="log-head"><span class="log-job">${U.esc(job || 'log')}</span><span class="log-hb">${U.esc(hb)}</span></div>
      <pre class="log-tail">${body ? U.esc(body) : '(no output yet)'}</pre>
    </div>`;
  },

  /* ── v3 builders (D1 foundation) ─────────────────────────────── */

  /* chart-series kind validation: categorical cat-1..8 or priority p0..p2.
     Status kinds are DELIBERATELY rejected (dataviz rule: status colors
     never carry a series) — anything unknown folds into cat-8. */
  _chartKind(k) {
    return (/^cat-[1-8]$/.test(k || '') || /^p[0-2]$/.test(k || '')) ? k : 'cat-8';
  },
  _kindCls(k) { return `k-${Comp._chartKind(k)}`; },
  _kindVar(k) { return `var(--${Comp._chartKind(k)})`; },

  /* shared legend chips (donut + distBar): swatch carries the color,
     label/count stay in text tokens */
  _legend(items) {
    if (!items || !items.length) return '';
    return `<div class="chart-legend">${items.map(s =>
      `<span class="legend-item"><span class="legend-dot ${Comp._kindCls(s.kind)}"></span>` +
      `<span>${U.esc(s.label)}</span><span class="legend-n">${U.esc(String(s.value))}</span></span>`
    ).join('')}</div>`;
  },

  /* source auto-detect for linkChips */
  _linkInfo(url) {
    const u = String(url || '').trim();
    if (/^https?:\/\//i.test(u)) {
      let host = '';
      try { host = new URL(u).hostname.replace(/^www\./, ''); } catch { /* keep '' */ }
      const h = host.toLowerCase();
      if (h.endsWith('slack.com')) return { icon: '💬', label: 'Slack', kind: 'http' };
      if (h === 'docs.google.com') return { icon: '📄', label: 'GDoc', kind: 'http' };
      if (h === 'drive.google.com') return { icon: '📄', label: 'Drive', kind: 'http' };
      if (h.endsWith('atlassian.net')) return { icon: '🎫', label: 'Jira', kind: 'http' };
      if (h.endsWith('fathom.video')) return { icon: '🎥', label: 'Fathom', kind: 'http' };
      if (h.endsWith('figma.com')) return { icon: '🎨', label: 'Figma', kind: 'http' };
      if (h.endsWith('miro.com')) return { icon: '🗺', label: 'Miro', kind: 'http' };
      return { icon: '🔗', label: host || 'link', kind: 'http' };
    }
    if (u.startsWith('#')) return { icon: '🔗', label: 'view', kind: 'hash' };
    return { icon: '📁', label: 'file', kind: 'file' };
  },

  linkChips(links, opts = {}) {
    const list = (Array.isArray(links) ? links : (links ? [links] : []))
      .map(l => (typeof l === 'string' ? { url: l } : (l && l.url ? l : null)))
      .filter(l => l && String(l.url).trim());
    if (!list.length) return '';
    const max = Number(opts.max) > 0 ? Number(opts.max) : list.length;
    const shown = list.slice(0, max);
    const chips = shown.map(({ url, label }) => {
      const info = Comp._linkInfo(url);
      const text = label || info.label;
      const icon = `<span class="link-chip-ic">${info.icon}</span>`;
      if (info.kind === 'file') {
        /* relative repo path -> Drawer (markdown renders; other text files
           degrade gracefully through the same viewer) */
        return `<button class="link-chip" data-drawer-path="${U.esc(url)}"` +
          ` data-drawer-title="${U.esc(label || url)}" title="${U.esc(url)}">${icon}${U.esc(text)}</button>`;
      }
      if (info.kind === 'hash') {
        return `<a class="link-chip" href="${U.esc(url)}" title="${U.esc(url)}">${icon}${U.esc(text)}</a>`;
      }
      return `<a class="link-chip" href="${U.esc(url)}" target="_blank" rel="noopener"` +
        ` title="${U.esc(url)}">${icon}${U.esc(text)}</a>`;
    }).join('');
    const more = list.length > shown.length
      ? `<span class="link-chip link-chip--more">+${list.length - shown.length}</span>` : '';
    return `<span class="link-chips">${chips}${more}</span>`;
  },

  donut(segments, { size = 96, label, legend } = {}) {
    const segs = (Array.isArray(segments) ? segments : [])
      .map(s => ({
        label: (s && s.label) || '',
        value: Math.max(0, Number(s && s.value) || 0),
        kind: Comp._chartKind(s && s.kind),
      }))
      .filter(s => s.value > 0);
    const total = segs.reduce((a, s) => a + s.value, 0);
    const sz = Math.max(48, Math.round(Number(size) || 96));
    const sw = 10;                              /* thin ring (dataviz spec) */
    const r = (sz - sw) / 2;
    const c = sz / 2;
    const circ = 2 * Math.PI * r;
    const fsTotal = Math.max(13, Math.round(sz * 0.19));
    const fsSub = Math.max(9, Math.round(sz * 0.1));

    let rings;
    if (!total) {
      rings = `<circle cx="${c}" cy="${c}" r="${r}" fill="none" stroke="rgba(255,255,255,0.08)" stroke-width="${sw}"/>`;
    } else {
      const gap = segs.length > 1 ? 2 : 0;      /* 2px surface gap */
      let offset = 0;
      rings = segs.map(s => {
        const len = (s.value / total) * circ;
        const dash = Math.max(0.5, len - gap);
        const el = `<circle cx="${c}" cy="${c}" r="${r}" fill="none"` +
          ` stroke="${Comp._kindVar(s.kind)}" stroke-width="${sw}"` +
          ` stroke-dasharray="${dash.toFixed(2)} ${Math.max(0, circ - dash).toFixed(2)}"` +
          ` stroke-dashoffset="${(-(offset + gap / 2)).toFixed(2)}">` +
          `<title>${U.esc(`${s.label} — ${s.value}`)}</title></circle>`;
        offset += len;
        return el;
      }).join('');
    }
    const totalY = label ? c - 4 : c;
    const svg = `<svg width="${sz}" height="${sz}" viewBox="0 0 ${sz} ${sz}" role="img"` +
      ` aria-label="${U.esc(label ? `${label}: ${total} total` : `${total} total`)}">` +
      `<g transform="rotate(-90 ${c} ${c})">${rings}</g>` +
      `<text x="${c}" y="${totalY}" class="donut-total" font-size="${fsTotal}"` +
      ` text-anchor="middle" dominant-baseline="central">${total}</text>` +
      (label ? `<text x="${c}" y="${c + fsSub + 3}" class="donut-sublabel" font-size="${fsSub}"` +
        ` text-anchor="middle" dominant-baseline="central">${U.esc(label)}</text>` : '') +
      `</svg>`;
    const wantLegend = legend !== false && segs.length >= 2;
    return `<div class="donut-wrap">${svg}${wantLegend ? Comp._legend(segs) : ''}</div>`;
  },

  distBar(items, { label } = {}) {
    const list = (Array.isArray(items) ? items : [])
      .map(s => ({
        label: (s && s.label) || '',
        value: Math.max(0, Number(s && s.value) || 0),
        kind: Comp._chartKind(s && s.kind),
      }))
      .filter(s => s.value > 0);
    const total = list.reduce((a, s) => a + s.value, 0);
    if (!total) return '';
    const segs = list.map(s => {
      const pct = (s.value / total) * 100;
      return `<span class="dist-seg ${Comp._kindCls(s.kind)}" style="--pct:${pct.toFixed(2)}"` +
        ` title="${U.esc(`${s.label} — ${s.value} (${Math.round(pct)}%)`)}"></span>`;
    }).join('');
    return `<div class="dist-wrap">` +
      (label ? `<div class="dist-label">${U.esc(label)}</div>` : '') +
      `<div class="dist-bar" role="img" aria-label="${U.esc(label || 'distribution')}">${segs}</div>` +
      Comp._legend(list) +
      `</div>`;
  },

  miniBars(points, { w = 140, h = 40, label, kind = 'cat-1' } = {}) {
    const pts = (Array.isArray(points) ? points : []).map(p =>
      (p && typeof p === 'object')
        ? { label: p.label || '', value: Math.max(0, Number(p.value) || 0) }
        : { label: '', value: Math.max(0, Number(p) || 0) });
    if (!pts.length) return '';
    const fill = Comp._kindVar(kind);
    const W = Math.max(24, Number(w) || 140);
    const H = Math.max(16, Number(h) || 40);
    const gap = 2;                              /* surface gap between bars */
    const slot = (W - gap * (pts.length - 1)) / pts.length;
    const barW = Math.max(2, Math.min(24, slot));
    const max = Math.max(1, ...pts.map(p => p.value));
    const rects = pts.map((p, i) => {
      const x = i * (slot + gap);
      const bh = p.value > 0 ? Math.max(1.5, (p.value / max) * (H - 2)) : 0;
      const tip = U.esc(p.label ? `${p.label} — ${p.value}` : String(p.value));
      /* transparent slot rect = full-height hover target; <title> on the
         group covers both, so tiny bars stay hoverable */
      return `<g><title>${tip}</title>` +
        `<rect x="${x.toFixed(1)}" y="0" width="${slot.toFixed(1)}" height="${H}" fill="transparent"/>` +
        (bh > 0 ? `<rect x="${(x + (slot - barW) / 2).toFixed(1)}" y="${(H - bh).toFixed(1)}"` +
          ` width="${barW.toFixed(1)}" height="${bh.toFixed(1)}" rx="1.5" fill="${fill}"/>` : '') +
        `</g>`;
    }).join('');
    return `<svg class="mini-bars" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}"` +
      ` role="img" aria-label="${U.esc(label || 'bar series')}">${rects}</svg>`;
  },

  backButton({ href, label = 'Back' } = {}) {
    if (href) return `<a class="back-btn" href="${U.esc(href)}">← ${U.esc(label)}</a>`;
    return `<button class="back-btn" data-back>← ${U.esc(label)}</button>`;
  },

  /* ── v4 builders (E2 visual foundation) ──────────────────────── */

  _uid: 0,   /* per-render unique suffix for SVG gradient ids */

  /* Catmull-Rom -> cubic bezier path through [x,y] coords (trendCard) */
  _smoothPath(coords) {
    if (!coords || coords.length < 2) return '';
    let d = `M${coords[0][0].toFixed(1)},${coords[0][1].toFixed(1)}`;
    for (let i = 0; i < coords.length - 1; i++) {
      const p0 = coords[Math.max(0, i - 1)], p1 = coords[i];
      const p2 = coords[i + 1], p3 = coords[Math.min(coords.length - 1, i + 2)];
      d += `C${(p1[0] + (p2[0] - p0[0]) / 6).toFixed(1)},${(p1[1] + (p2[1] - p0[1]) / 6).toFixed(1)} ` +
        `${(p2[0] - (p3[0] - p1[0]) / 6).toFixed(1)},${(p2[1] - (p3[1] - p1[1]) / 6).toFixed(1)} ` +
        `${p2[0].toFixed(1)},${p2[1].toFixed(1)}`;
    }
    return d;
  },

  /* 🤖 kick off an AI run — POST /api/ai-task {kind, ref}; delegated
     wiring + the shared poller live in components.js (see AI below).
     Re-render safe: if this {kind, ref} already has a tracked run,
     render its CURRENT pill instead of a fresh button. */
  aiButton({ kind, ref, label = '🤖 AI, work on it' } = {}) {
    const existing = AI.activeFor(kind, ref);
    if (existing) return AI.pillHtml(existing);
    return `<button class="ai-btn" data-ai-kind="${U.esc(kind ?? '')}"` +
      ` data-ai-ref="${U.esc(ref ?? '')}" title="Serahin ke AI (POST /api/ai-task)">${U.esc(label)}</button>`;
  },

  /* pill for a PRE-EXISTING run (e.g. GET /api/ai-task?list=1). A run
     that is still running is adopted by the shared poller — deliberate
     side effect so a page reload keeps tracking in-flight runs. */
  aiResultPill({ run } = {}) {
    const r = run || {};
    const id = r.id ?? r.run_id;
    if (id == null || id === '') return '';
    const st = AI._normState(r);
    const known = AI.runs.get(String(id));
    if (!known) {
      const t0 = Date.parse(r.started_at || r.created_at || r.ts || '') || Date.now();
      AI.track(id, r.kind, r.ref, st, r, t0);
    } else if (!known.run) {
      known.run = r;
    }
    return AI.pillHtml(AI.runs.get(String(id)));
  },

  /* 🎫 internal ticket chip — Work tab can highlight via data-ticket-id */
  ticketChip(ticketId) {
    const id = String(ticketId ?? '').trim();
    if (!id) return '';
    return `<a class="ticket-chip" href="#work" data-ticket-id="${U.esc(id)}"` +
      ` title="Ticket ${U.esc(id)} — buka di Work tab">🎫 ${U.esc(id)}</a>`;
  },

  /* one RGB bezier connector lane between two harness-map columns */
  _hmapLane(nextLabel) {
    const gid = `hmapg-${++Comp._uid}`;
    /* three strands converge gently from the left column's spine into the
       next column's center — a TIGHT y-spread (95/120/145 of 240) keeps
       the curves reading left→right even when the lane is stretched to a
       tall column's height; dash-flow animates the direction */
    const strands = [
      'M0,95 C15,95 20,120 38,120',
      'M0,120 C14,120 24,120 38,120',
      'M0,145 C15,145 20,120 38,120',
    ].map(d =>
      `<path class="hmap-flow-glow" d="${d}" pathLength="100" stroke="url(#${gid})" vector-effect="non-scaling-stroke"/>` +
      `<path class="hmap-flow" d="${d}" pathLength="100" stroke="url(#${gid})" vector-effect="non-scaling-stroke"/>`
    ).join('');
    return `<div class="hmap-lane" aria-hidden="true"${nextLabel ? ` title="flows into ${U.esc(nextLabel)}"` : ''}>
      <svg viewBox="0 0 38 240" preserveAspectRatio="none">
        <defs><linearGradient id="${gid}" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" class="hmap-g1"/><stop offset="0.55" class="hmap-g2"/><stop offset="1" class="hmap-g3"/>
        </linearGradient></defs>${strands}
      </svg>
    </div>`;
  },

  /* 🗺 Harness Map v2 — SVG-connected node-card columns. Feed
     /api/harness-map's {groups} payload (bare array accepted). Node
     click contract is UNCHANGED: class="map-node" + data-node-id, so
     tab-system's existing delegated listener keeps working. */
  harnessMap(mapData) {
    const groups = Array.isArray(mapData) ? mapData : ((mapData && mapData.groups) || []);
    if (!groups.length) return Comp.emptyState({ icon: '🗺', title: 'No harness map data' });
    const KNOWN_ST = ['ok', 'warn', 'fail', 'idle', 'gated'];
    const cols = groups.map((g, i) => {
      const nodes = (g.nodes || []).map(n => {
        const st = KNOWN_ST.includes(n.status) ? n.status : 'idle';
        const stLabel = st === 'gated' ? '🔒 gated' : st;
        return `<button class="map-node hmap-node hmap-node--${st}" data-node-id="${U.esc(n.id)}"` +
          ` title="${U.esc(n.desc || n.label || '')}">
          <span class="hmap-dot" aria-hidden="true"></span>
          <span class="hmap-node-main">
            <span class="hmap-node-top">
              <span class="hmap-node-label">${U.esc(n.label || n.id)}</span>
              <span class="hmap-node-st">${U.esc(stLabel)}</span>
            </span>
            ${n.desc ? `<span class="hmap-node-desc">${U.esc(n.desc)}</span>` : ''}
          </span>
        </button>`;
      }).join('');
      const col = `<div class="hmap-col hmap-col--${(i % 5) + 1}">
        <div class="hmap-col-head">${U.esc(g.label || g.key || '')}</div>
        <div class="hmap-nodes">${nodes || `<div class="row-subtext">(no nodes)</div>`}</div>
      </div>`;
      const lane = i < groups.length - 1 ? Comp._hmapLane(groups[i + 1].label || '') : '';
      return col + lane;
    }).join('');
    return `<div class="hmap">${cols}</div>`;
  },

  /* 📈 momentum chart card — big total + 7d-vs-previous-7d delta +
     gradient area chart. kind: categorical/priority only (dataviz rule);
     good:'down' secondarys delta coloring for bad-when-rising metrics. */
  trendCard({ title, icon, points, total, unit, kind = 'cat-1', good = 'up' } = {}) {
    const pts = (Array.isArray(points) ? points : []).map(p =>
      (p && typeof p === 'object')
        ? { label: p.label || '', value: Math.max(0, Number(p.value) || 0) }
        : { label: '', value: Math.max(0, Number(p) || 0) });
    const sum = pts.reduce((a, p) => a + p.value, 0);
    const totalVal = total == null ? sum : total;

    let deltaHtml = '';
    if (pts.length >= 8) {
      const cur = pts.slice(-7).reduce((a, p) => a + p.value, 0);
      const prev = pts.slice(-14, -7).reduce((a, p) => a + p.value, 0);
      const delta = cur - prev;
      const dirGood = good === 'down' ? delta < 0 : delta > 0;
      const cls = delta === 0 ? '' : (dirGood ? ' trend-delta--good' : ' trend-delta--warn');
      const arrow = delta > 0 ? '▲' : (delta < 0 ? '▼' : '·');
      const txt = delta === 0 ? 'same as the 7 days before' : `${Math.abs(delta)} vs the 7 days before`;
      deltaHtml = `<div class="trend-delta${cls}">${arrow} ${U.esc(txt)}</div>`;
    }

    let chart = '';
    if (pts.length >= 2) {
      const W = 220, H = 64, padX = 4, padT = 9, padB = 4;
      const max = Math.max(1, ...pts.map(p => p.value));
      const step = (W - padX * 2) / (pts.length - 1);
      const coords = pts.map((p, i) => [padX + i * step, padT + (1 - p.value / max) * (H - padT - padB)]);
      const line = Comp._smoothPath(coords);
      const area = `${line}L${W - padX},${H}L${padX},${H}Z`;
      const gid = `tcg-${++Comp._uid}`;
      const slots = pts.map((p, i) => {
        const x0 = Math.max(0, padX + i * step - step / 2);
        return `<rect x="${x0.toFixed(1)}" y="0" width="${step.toFixed(1)}" height="${H}" fill="transparent">` +
          `<title>${U.esc(p.label ? `${p.label} — ${p.value}` : String(p.value))}</title></rect>`;
      }).join('');
      const [lx, ly] = coords[coords.length - 1];
      chart = `<svg class="trend-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${U.esc(title || 'trend')}">
        <defs><linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" class="trend-area-top"/><stop offset="1" class="trend-area-bot"/>
        </linearGradient></defs>
        <path class="trend-area" d="${area}" fill="url(#${gid})"/>
        <path class="trend-line" d="${line}"/>
        <circle class="trend-dot-halo" cx="${lx.toFixed(1)}" cy="${ly.toFixed(1)}" r="6"/>
        <circle class="trend-dot" cx="${lx.toFixed(1)}" cy="${ly.toFixed(1)}" r="2.8"/>
        ${slots}</svg>`;
    }
    const num = Number(totalVal);
    const tickAttr = Number.isFinite(num) ? ` data-tick="${num}"` : '';
    return `<div class="trend-card" data-key="trend:${U.esc(title || '')}" style="--tc:${Comp._kindVar(kind)}">
      <div class="trend-head">${icon ? `<span>${U.esc(icon)}</span>` : ''}<span>${U.esc(title || '')}</span></div>
      <div class="trend-total"><span class="trend-num num"${tickAttr}>${U.esc(String(totalVal))}</span>${unit ? `<span class="trend-unit">${U.esc(unit)}</span>` : ''}</div>
      ${deltaHtml}
      ${chart}
    </div>`;
  },

  drawer() {
    return `<div class="drawer-overlay"></div>
      <aside class="drawer" role="dialog" aria-modal="true">
        <div class="drawer-head">
          <div class="drawer-title"></div>
          <button class="drawer-close" aria-label="Close">✕</button>
        </div>
        <div class="drawer-body"></div>
      </aside>`;
  },
};

const Drawer = {
  root: null,

  init() {
    this.root = document.getElementById('drawer-root');
    if (!this.root) return;
    this.root.innerHTML = Comp.drawer();
    this.root.querySelector('.drawer-overlay').addEventListener('click', () => this.close());
    this.root.querySelector('.drawer-close').addEventListener('click', () => this.close());
    document.addEventListener('keydown', e => { if (e.key === 'Escape') this.close(); });
  },

  /* shared shell painter: title + body HTML + slide in. Returns the body
     element (or null if there's no #drawer-root to mount into).
     `wide` toggles the ~72%-width variant; open/openHtml always reset it
     so a normal open after openWide snaps back (back-compatible). */
  _paint(title, bodyHtml, wide = false) {
    if (!this.root) this.init();
    if (!this.root) return null;
    this.root.querySelector('.drawer').classList.toggle('drawer--wide', !!wide);
    const tEl = this.root.querySelector('.drawer-title');
    tEl.textContent = title;
    tEl.title = title;
    const body = this.root.querySelector('.drawer-body');
    body.innerHTML = bodyHtml;
    this.root.classList.add('is-open');
    return body;
  },

  async open(title, relPath) {
    const body = this._paint(title || relPath,
      `<div class="skeleton"><div class="skeleton-line"></div><div class="skeleton-line w-80"></div><div class="skeleton-line w-60"></div></div>`);
    if (!body) return;
    try {
      const d = await U.fetchJSON(`/api/file/${encodeURI(relPath)}`);
      body.innerHTML = `<div class="md">${U.mdToHtml(d.content || '')}</div>`;
    } catch (err) {
      body.innerHTML = `<div class="load-error">Could not load ${U.esc(relPath)}: ${U.esc(err.message)}</div>`;
    }
  },

  /* paint arbitrary trusted HTML (build it from Comp.* calls) into the same
     shell — identical overlay/✕/ESC close wiring, no fetch */
  openHtml(title, html) {
    this._paint(title || '', html || '');
  },

  /* same shell at ~72% width — the initiative slide-over. Trusted HTML,
     same overlay/✕/ESC close wiring. */
  openWide(title, html) {
    this._paint(title || '', html || '', true);
  },

  close() {
    if (this.root) this.root.classList.remove('is-open');
  },
};

/* ═══════════════════════════════════════════════════════════════════
   AI — the ONE shared poller behind Comp.aiButton / Comp.aiResultPill.
   A single interval serves every tracked run (never one per button);
   it starts when the first running run is tracked and stops when none
   are left. Poll: GET /api/ai-task?id=<id> every 5s, 8 min budget per
   run. All server-shape reads are defensive — the endpoint is being
   built in parallel ({run:{…}} wrapper or bare run object both work;
   unknown status strings keep polling until the budget runs out).
   ═══════════════════════════════════════════════════════════════════ */
const AI = {
  POLL_MS: 5000,
  MAX_MS: 8 * 60 * 1000,
  runs: new Map(),          /* id -> {id, kind, ref, state, run, startedAt, announced} */
  _timer: null,

  _key(kind, ref) { return `${kind ?? ''}␟${ref ?? ''}`; },

  /* newest tracked run for a {kind, ref} pair (Map keeps insertion order) */
  activeFor(kind, ref) {
    const k = this._key(kind, ref);
    let found = null;
    for (const r of this.runs.values()) if (this._key(r.kind, r.ref) === k) found = r;
    return found;
  },

  track(id, kind, ref, state = 'running', run = null, startedAt = null) {
    if (id == null || id === '') return;
    this.runs.set(String(id), {
      id: String(id), kind: kind ?? '', ref: ref ?? '',
      state, run, startedAt: startedAt || Date.now(),
      announced: state !== 'running',   /* pre-finished runs never re-toast */
    });
    if (state === 'running') this._ensureTimer();
  },

  /* seed from GET /api/ai-task?list=1 ({runs:[…]}, newest-first): adopt
     every run not already tracked, iterating oldest-first so activeFor()
     resolves to the NEWEST run per {kind, ref}. Done/error runs render as
     finished pills via Comp.aiButton and never re-toast; still-running
     ones resume the shared poller — so a page reload keeps both finished
     results and in-flight runs visible on their rows. app.js calls this
     in the refresh batch; safe to call repeatedly (known ids are skipped). */
  adoptList(runs) {
    for (const r of [...(runs || [])].reverse()) {
      const id = r && (r.id ?? r.run_id);
      if (id == null || id === '' || this.runs.has(String(id))) continue;
      const t0 = Date.parse(r.started_wib || r.started_at || r.created_at || '') || Date.now();
      this.track(id, r.kind, r.ref, this._normState(r), r, t0);
    }
  },

  _ensureTimer() {
    if (this._timer) return;
    this._timer = setInterval(() => this._tick(), this.POLL_MS);
  },

  _maybeStop() {
    if (!this._timer) return;
    for (const r of this.runs.values()) if (r.state === 'running') return;
    clearInterval(this._timer);
    this._timer = null;
  },

  /* defensive status vocabulary — anything unknown keeps polling */
  _normState(run) {
    const s = String((run && (run.status ?? run.state)) ?? '').toLowerCase();
    if (['done', 'ok', 'success', 'succeeded', 'completed', 'complete', 'finished'].includes(s)) return 'done';
    if (['error', 'err', 'fail', 'failed', 'dead'].includes(s)) return 'error';
    return 'running';
  },

  _unwrap(d) {
    return (d && typeof d === 'object' && d.run && typeof d.run === 'object') ? d.run : d;
  },

  async _tick() {
    for (const rec of [...this.runs.values()]) {
      if (rec.state !== 'running') continue;
      if (Date.now() - rec.startedAt > this.MAX_MS) {
        rec.state = 'timeout';
        this._paint(rec);
        Comp.toast(`AI run ${rec.id}: 8 minutes with no result — polling stopped, check the log`, false);
        continue;
      }
      try {
        const d = await U.fetchJSON(`/api/ai-task?id=${encodeURIComponent(rec.id)}`);
        const run = this._unwrap(d);
        if (run && typeof run === 'object') rec.run = run;
        const st = this._normState(run);
        if (st === 'running') continue;
        rec.state = st;
        this._paint(rec);
        if (!rec.announced) {
          rec.announced = true;
          if (st === 'done') {
            Comp.toast(`AI finished: ${rec.kind || rec.id}`, true);
            window.dispatchEvent(new CustomEvent('psb:ai-done',
              { detail: { id: rec.id, kind: rec.kind, ref: rec.ref, run: rec.run } }));
          } else {
            Comp.toast(`AI run failed: ${rec.kind || rec.id}`, false);
          }
        }
      } catch { /* transient poll error — keep trying until the budget ends */ }
    }
    this._maybeStop();
  },

  pillHtml(rec) {
    if (!rec) return '';
    const meta = ` data-ai-id="${U.esc(rec.id)}" data-ai-kind="${U.esc(rec.kind ?? '')}"` +
      ` data-ai-ref="${U.esc(rec.ref ?? '')}"`;
    if (rec.state === 'done') {
      return `<button class="ai-pill ai-pill--done"${meta} title="Open the result of run ${U.esc(rec.id)}">✅ Done · see result</button>`;
    }
    if (rec.state === 'error') {
      return `<button class="ai-pill ai-pill--err"${meta} title="Open the log of run ${U.esc(rec.id)}">⛔ Failed · see log</button>`;
    }
    if (rec.state === 'timeout') {
      return `<button class="ai-pill ai-pill--err"${meta} title="Polling stopped after 8 minutes — the run may still be going">⏱ Timeout · see log</button>`;
    }
    return `<span class="ai-pill ai-pill--running"${meta}>⏳ AI running…</span>`;
  },

  /* swap every rendered pill for this run to its current state */
  _paint(rec) {
    document.querySelectorAll(`.ai-pill[data-ai-id="${CSS.escape(rec.id)}"]`)
      .forEach(el => { el.outerHTML = this.pillHtml(rec); });
  },

  /* wide drawer: status row + log tail + (when result_path) the draft */
  async openResult(id) {
    const rid = String(id ?? '');
    if (!rid) return;
    const rec = this.runs.get(rid) || { id: rid, kind: '', ref: '', state: 'done', run: null };
    const title = `AI run — ${[rec.kind, rec.ref].filter(Boolean).join(' · ') || rid}`;
    const skel = `<div class="skeleton"><div class="skeleton-line"></div><div class="skeleton-line w-80"></div><div class="skeleton-line w-60"></div></div>`;
    Drawer.openWide(title, skel);
    let run = rec.run;
    if (!run) {
      try {
        run = this._unwrap(await U.fetchJSON(`/api/ai-task?id=${encodeURIComponent(rid)}`));
        rec.run = run;
      } catch (err) {
        Drawer.openWide(title, `<div class="load-error">Could not load run ${U.esc(rid)}: ${U.esc(err.message)}</div>`);
        return;
      }
    }
    run = (run && typeof run === 'object') ? run : {};
    const st = rec.state === 'timeout' ? 'timeout' : this._normState(run);
    const stBadge =
      st === 'done' ? Comp.badge('good', 'done') :
      st === 'error' ? Comp.badge('serious', 'error') :
      st === 'timeout' ? Comp.badge('warn', 'poll timeout') : Comp.badge('warn', 'running');
    const resPath = String(run.result_path || run.resultPath || run.result_file || '');
    const head = `<div class="row" data-key="airun:${U.esc(rid)}">
        <span class="row-icon">🤖</span>
        <span class="row-title" title="${U.esc(rid)}">${U.esc(rid)}</span>
        <span class="row-badges">${stBadge}${rec.kind ? Comp.badge('muted', rec.kind) : ''}</span>
        ${rec.ref ? `<span class="row-meta" title="${U.esc(rec.ref)}">${U.esc(rec.ref)}</span>` : ''}
      </div>`;
    const tail = run.tail ?? run.log_tail ?? run.log ?? run.output ?? [];
    const hb = run.finished_at ?? run.updated_at ?? run.ts ?? null;
    Drawer.openWide(title, `<div class="stack">${head}` +
      Comp.logPanel({ job: `ai:${rec.kind || run.kind || 'task'}`, tail, last_heartbeat: hb }) +
      (resPath ? `<div class="ai-result-slot">${skel}</div>` : '') +
      `</div>`);
    if (!resPath) return;
    const slot = Drawer.root && Drawer.root.querySelector('.drawer-body .ai-result-slot');
    if (!slot) return;
    try {
      const f = await U.fetchJSON(`/api/file/${encodeURI(resPath)}`);
      slot.innerHTML = `<div class="section-label">Draft hasil — ${U.esc(resPath)}</div>` +
        `<div class="md">${U.mdToHtml(f.content || '')}</div>`;
    } catch (err) {
      slot.innerHTML = `<div class="load-error">Could not load ${U.esc(resPath)}: ${U.esc(err.message)}</div>`;
    }
  },
};

/* ═══════════════════════════════════════════════════════════════════
   DELEGATED CLICK WIRING — the ONLY document-level click listener in
   components.js. It captures, in order, exactly these selectors (all
   matched narrowly via closest()):
     1. .chase-btn            (button — preventDefault: may sit in <summary>)
     2. .make-ticket-btn      (button — preventDefault: may sit in <summary>)
     3. .ai-btn               (button — preventDefault: POST /api/ai-task,
                               swaps itself into the AI status pill)
     4. .ai-pill[data-ai-id]  (button when done/error/timeout — preventDefault
                               + AI.openResult; the ⏳ running pill is a
                               non-interactive <span> and is skipped)
     5. .action-save          (button — no preventDefault needed)
     6. .back-btn[data-back]  (button — history.back(); the <a href> variant
                               of Comp.backButton never carries data-back and
                               navigates natively)
     7. [data-drawer-path]    (preventDefault + Drawer.open — BUT a plain
                               <a href> nested INSIDE the opener always wins:
                               we bail before preventDefault so breadcrumbs,
                               link chips, jira chips etc. are never swallowed)
   NOTHING here preventDefaults a bare anchor. If your click dies, check
   whether an ancestor you rendered carries data-drawer-path.
   ═══════════════════════════════════════════════════════════════════ */
document.addEventListener('click', async e => {
  /* ⏱ Chase -> POST /api/waiting-add, toast, psb:waiting-added */
  const chase = e.target.closest('.chase-btn');
  if (chase) {
    e.preventDefault();               /* may sit inside a <summary> */
    if (chase.disabled) return;
    chase.disabled = true;
    const payload = {
      owner: chase.dataset.owner || '',
      what: chase.dataset.what || '',
      sla_hours: Number(chase.dataset.slaHours) || 24,
    };
    if (chase.dataset.sourceUrl) payload.source_url = chase.dataset.sourceUrl;
    try {
      const res = await U.fetchJSON('/api/waiting-add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      Comp.toast(`Masuk watchdog: ${(res && res.id) || payload.owner || 'ok'}`, true);
      chase.textContent = '✓ Chasing';
      window.dispatchEvent(new CustomEvent('psb:waiting-added',
        { detail: { id: res && res.id, owner: payload.owner, what: payload.what } }));
    } catch (err) {
      Comp.toast(`Chase failed: ${err.message}`, false);
      chase.disabled = false;
    }
    return;
  }

  /* → Jadiin ticket -> POST /api/action create, toast, psb:ticket-saved */
  const mk = e.target.closest('.make-ticket-btn');
  if (mk) {
    e.preventDefault();
    if (mk.disabled) return;
    mk.disabled = true;
    const text = mk.dataset.aiText || '';
    /* commitment id of the row that asked for this ticket (row carries
       data-commitment-id) — echoed back in psb:ticket-saved so listeners
       (app.js) can link commitment→ticket without guessing which row fired */
    const cRow = mk.closest('[data-commitment-id]');
    const commitmentId = (cRow && cRow.dataset.commitmentId) || '';
    const payload = {
      action: 'create',
      title: text.slice(0, 120),
      priority: 'P1',
      project: 'Other',
      note: `From meeting: ${mk.dataset.aiRef || ''}`,
    };
    try {
      const res = await U.fetchJSON('/api/action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const id = (res && res.ticket && res.ticket.id) || (res && res.id) || '';
      Comp.toast(`Ticket dibuat${id ? `: ${id}` : ''}`, true);
      mk.textContent = '✓ Ticket';
      window.dispatchEvent(new CustomEvent('psb:ticket-saved',
        { detail: { id, ticket: res && res.ticket, commitment_id: commitmentId } }));
    } catch (err) {
      Comp.toast(`Could not create the ticket: ${err.message}`, false);
      mk.disabled = false;
    }
    return;
  }

  /* 🤖 AI, work on it -> POST /api/ai-task, swap to status pill, track in AI */
  const aiBtn = e.target.closest('.ai-btn');
  if (aiBtn) {
    e.preventDefault();               /* may sit inside a <summary> */
    if (aiBtn.disabled) return;
    aiBtn.disabled = true;
    const kind = aiBtn.dataset.aiKind || '';
    const ref = aiBtn.dataset.aiRef || '';
    const orig = aiBtn.textContent;
    aiBtn.textContent = '⏳ ngirim…';
    try {
      const res = await U.fetchJSON('/api/ai-task', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind, ref }),
      });
      const id = res && (res.id ?? (res.run && res.run.id));
      if (!res || res.ok === false || id == null) {
        throw new Error((res && (res.error || res.detail)) || 'no run id in the response');
      }
      AI.track(id, kind, ref);
      aiBtn.outerHTML = AI.pillHtml(AI.runs.get(String(id)));
    } catch (err) {
      const msg = /^HTTP 409/.test(err.message || '') ? 'a run for this is still going' : err.message;
      Comp.toast(`AI task failed: ${msg}`, false);
      aiBtn.disabled = false;
      aiBtn.textContent = orig;
    }
    return;
  }

  /* finished AI pill -> wide drawer with tail + draft (running = inert span) */
  const aiPill = e.target.closest('.ai-pill[data-ai-id]');
  if (aiPill) {
    if (aiPill.classList.contains('ai-pill--running')) return;
    e.preventDefault();
    AI.openResult(aiPill.dataset.aiId);
    return;
  }

  const saveBtn = e.target.closest('.action-save');
  if (saveBtn) {
    const bar = saveBtn.closest('.action-bar');
    if (!bar) return;
    const payload = { id: bar.dataset.ticketId };
    bar.querySelectorAll('[data-field]').forEach(el => {
      if (el.value) payload[el.dataset.field] = el.value;
    });
    saveBtn.disabled = true;
    try {
      const res = await U.fetchJSON('/api/action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      Comp.toast(`Saved ${payload.id}`, true);
      window.dispatchEvent(new CustomEvent('psb:ticket-saved', { detail: { id: payload.id, ticket: res.ticket } }));
    } catch (err) {
      Comp.toast(`Save failed: ${err.message}`, false);
    } finally {
      saveBtn.disabled = false;
    }
    return;
  }
  /* ← Back (href-less Comp.backButton variant) */
  const back = e.target.closest('.back-btn[data-back]');
  if (back) {
    history.back();
    return;
  }

  const opener = e.target.closest('[data-drawer-path]');
  if (opener) {
    /* narrowness guard: a real <a href> nested inside the opener wins —
       never swallow plain anchors (breadcrumb crumbs, link chips, …) */
    const innerLink = e.target.closest('a[href]');
    if (innerLink && innerLink !== opener && opener.contains(innerLink)) return;
    e.preventDefault();
    Drawer.open(opener.dataset.drawerTitle || opener.dataset.drawerPath, opener.dataset.drawerPath);
  }
});

/* count-up wiring: whenever a render inserts [data-tick] (statTile tick:true),
   animate it via U.tickUp — but only when the value CHANGED since the last
   render for that data-key (60s refresh must not replay motion: motion = signal). */
const _tickSeen = new Map();
function _runTicks(node) {
  if (!node || node.nodeType !== 1) return;
  const els = [];
  if (node.matches && node.matches('[data-tick]')) els.push(node);
  if (node.querySelectorAll) els.push(...node.querySelectorAll('[data-tick]'));
  for (const el of els) {
    const v = Number(el.dataset.tick);
    if (!Number.isFinite(v)) continue;
    const keyHost = el.closest('[data-key]');
    const key = keyHost ? keyHost.dataset.key : null;
    if (key != null) {
      if (_tickSeen.get(key) === v) continue;   /* unchanged -> stay static */
      _tickSeen.set(key, v);
    }
    U.tickUp(el, v);
  }
}
new MutationObserver(muts => {
  for (const m of muts) for (const n of m.addedNodes) _runTicks(n);
}).observe(document.documentElement, { childList: true, subtree: true });
