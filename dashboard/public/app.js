/* ═══════════════════════════════════════════════════════════════════
   app.js — boot, hash router, UI state, refresh loop, Drawer wiring,
   and the complete ⭐ Today tab. Other tabs are owned by tab-work.js /
   tab-meetings.js / tab-system.js registering on window.Tabs; if a
   module is missing, its panel renders a calm "under construction"
   EmptyState instead of erroring.
   ═══════════════════════════════════════════════════════════════════ */
'use strict';

window.Tabs = window.Tabs || {};

const $id = id => document.getElementById(id);

/* ── UI state: expansion memory (openKeys/closedKeys + sessionStorage) ── */
const UI = {
  openKeys: new Set(),    // keys the user explicitly opened
  closedKeys: new Set(),  // keys the user explicitly closed (beats defaultOpen)

  load() {
    try {
      const raw = JSON.parse(sessionStorage.getItem('psb.open') || '{}');
      UI.openKeys = new Set(raw.o || []);
      UI.closedKeys = new Set(raw.c || []);
    } catch { /* fresh session */ }
  },

  save() {
    try {
      sessionStorage.setItem('psb.open',
        JSON.stringify({ o: [...UI.openKeys], c: [...UI.closedKeys] }));
    } catch { /* storage blocked — expansion just won't persist */ }
  },

  /* Comp._open delegates here: explicit user state wins, else the default */
  isOpen(key, def) {
    if (UI.openKeys.has(key)) return true;
    if (UI.closedKeys.has(key)) return false;
    return !!def;
  },
};
window.UI = UI;

/* record every <details data-key> toggle ('toggle' doesn't bubble → capture) */
document.addEventListener('toggle', e => {
  const el = e.target;
  if (!(el instanceof HTMLDetailsElement) || !el.dataset.key) return;
  if (el.open) { UI.openKeys.add(el.dataset.key); UI.closedKeys.delete(el.dataset.key); }
  else { UI.openKeys.delete(el.dataset.key); UI.closedKeys.add(el.dataset.key); }
  UI.save();
}, true);

/* ── App state ── */
const App = {
  overview: null,
  overviewError: null,
  lastFetched: null,
  progress: null,          // /api/progress — momentum band; null = absent (fetch failed or not yet loaded)
  briefing: null,          // /api/briefing — briefing card; null = absent
  calendar: null,
  calendarState: 'idle',   // idle | loading | ready | error
  calendarError: null,
  calendarAt: 0,
  activeTab: 'today',
  filter: null,
};

/* focus guard: never re-render a container the user is typing in */
function canRender(container) {
  const a = document.activeElement;
  return !(a && container && container.contains(a) && a.matches('input, textarea, select'));
}

/* ── Router: #today (default) | #inbox | #work[/filter] | #meetings | #system
   Plus #find/<ID>, which is NOT a tab: it is the permalink to one ledger
   record, owned by quickfind.js, which opens it in the Drawer over whatever
   tab is already showing. Routing it as a tab would throw the owner back to Today
   every time he clicked a WAIT- link from another tab. ── */
const TAB_NAMES = ['today', 'inbox', 'work', 'meetings', 'hours', 'system'];

function parseHash() {
  const h = (location.hash || '#today').replace(/^#/, '');
  const [tab, ...rest] = h.split('/');
  if (tab === 'find') {
    return { tab: App.activeTab || 'today', filter: App.filter, isFind: true };
  }
  return {
    tab: TAB_NAMES.includes(tab) ? tab : 'today',
    filter: rest.join('/') || null,
  };
}

let routed = false;   // has a tab ever been painted?

function applyRoute() {
  const { tab, filter, isFind } = parseHash();
  /* a #find deep link changes nothing about the tab layout — quickfind.js
     already reacted to the same hashchange and is painting the Drawer. On the
     FIRST load, though, no panel is active yet: bail out here and the record
     card floats over an empty shell, so fall through and paint the default tab
     underneath it. */
  if (isFind && routed) return;
  routed = true;
  App.activeTab = tab;
  App.filter = filter;
  document.querySelectorAll('.tab-btn').forEach(b =>
    b.classList.toggle('is-active', b.dataset.tab === tab));
  document.querySelectorAll('.tab-panel').forEach(p =>
    p.classList.toggle('is-active', p.id === `tab-${tab}`));
  loadActiveTab();
}

function loadActiveTab() {
  if (App.activeTab === 'today') {
    renderToday();
    ensureCalendar();
    return;
  }
  const mod = window.Tabs[App.activeTab];
  const panel = $id(`tab-${App.activeTab}`);
  if (mod && typeof mod.load === 'function') {
    try {
      mod.load(App.filter);
    } catch (err) {
      panel.innerHTML = `<div class="load-error">Tab failed to render: ${U.esc(err.message)}</div>`;
    }
  } else {
    panel.innerHTML = Comp.emptyState({
      icon: '🚧',
      title: `${App.activeTab[0].toUpperCase()}${App.activeTab.slice(1)} tab is under construction`,
      hint: 'Its module has not shipped yet — everything else keeps working.',
    });
  }
}

/* ── Refresh loop: overview + progress + briefing + AI runs + active tab,
   60s, paused when hidden. progress/briefing are fetched in the SAME batch
   as overview (Promise.allSettled: one endpoint failing never blocks the
   others) — the momentum band and briefing card just render absent on
   failure, no chrome. The ai-task list seeds AI.adoptList so rows built
   with Comp.aiButton (e.g. Today's action items) show the done pill for a
   completed run even after a full page reload. */
async function refreshOverview(manual = false) {
  if (document.hidden && !manual) return;
  const btn = $id('btn-refresh');
  btn.classList.add('is-busy');
  const [ovRes, progRes, briefRes, aiRes, cqRes] = await Promise.allSettled([
    U.fetchJSON('/api/overview'),
    U.fetchJSON('/api/progress'),
    U.fetchJSON('/api/briefing'),
    U.fetchJSON('/api/ai-task?list=1'),
    U.fetchJSON('/api/command-queue'),
  ]);
  if (aiRes.status === 'fulfilled') AI.adoptList(aiRes.value && aiRes.value.runs);
  App.commandQueue = cqRes.status === 'fulfilled' ? cqRes.value : null;
  if (ovRes.status === 'fulfilled') {
    App.overview = ovRes.value;
    App.overviewError = null;
    App.lastFetched = new Date();
  } else {
    App.overviewError = ovRes.reason?.message || String(ovRes.reason);
    if (manual) Comp.toast(`Refresh failed: ${App.overviewError}`, false);
  }
  App.progress = progRes.status === 'fulfilled' ? progRes.value : null;
  App.briefing = briefRes.status === 'fulfilled' ? briefRes.value : null;
  btn.classList.remove('is-busy');
  updateChrome();
  loadActiveTab();
}

/* header date/updated-at + System tab red dot.
   Red-dot choice (documented): overview.heartbeat.fail > 0 — the latest
   status per scheduled job from the heartbeat log; acked failures are
   excluded server-side (they render muted on the Routines card, so they
   never pin the dot). Freshness 'dead' alone is NOT used: one
   knowingly-dead source would pin the dot on forever. */
function updateChrome() {
  const now = new Date();
  $id('app-date').textContent = now.toLocaleDateString('en-GB',
    { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
  $id('app-updated').textContent = App.lastFetched
    ? `updated ${App.lastFetched.toLocaleTimeString('en-GB')}`
    : (App.overviewError ? 'fetch failed' : 'loading…');
  const failing = (App.overview?.heartbeat?.fail || 0) > 0;
  document.querySelector('.tab-btn[data-tab="system"]')
    ?.classList.toggle('has-alert', failing);
}

/* ═══════════════════════════════════════════
   ⭐ TODAY TAB
   ═══════════════════════════════════════════ */

/* project name → fixed categorical slot (never cycled) */
function projCat(project) {
  const p = (project || '').toLowerCase();
  if (p.includes('marketplace')) return 'cat-1';
  if (p.includes('b2c') || p.includes('superapp') || p.includes('super app')) return 'cat-2';
  if (p.includes('pim')) return 'cat-3';
  if (p.includes('seller')) return 'cat-4';
  if (p.includes('ExampleProgram') || p.includes('exampleco')) return 'cat-5';
  if (p.includes('work id')) return 'cat-6';
  if (p.includes('ecom')) return 'cat-7';
  return 'cat-8';
}
window.projCat = projCat;   // Stage 2 tabs reuse the same project→slot map

function freshnessFor(label) {
  return (App.overview?.freshness || []).find(f => f.label === label) || {};
}

function skeletonBlock(lines = 3) {
  let out = '<div class="skeleton">';
  const widths = ['', 'w-80', 'w-60'];
  for (let i = 0; i < lines; i++) out += `<div class="skeleton-line ${widths[i % 3]}"></div>`;
  return out + '</div>';
}

function renderToday() {
  const panel = $id('tab-today');
  if (!canRender(panel)) return;
  if (!App.overview) {
    panel.innerHTML = App.overviewError
      ? `<div class="load-error">Could not load overview: ${U.esc(App.overviewError)}</div>`
      : `<div class="hero-row">${skeletonBlock(2).repeat(5)}</div>${skeletonBlock(4)}`;
    return;
  }
  const ov = App.overview;
  panel.innerHTML = [
    heroTiles(ov),
    momentumBand(),
    briefingCard(),
    escalationStrip(ov),
    `<div id="today-approvals">${approvalsCard()}${failuresCard()}</div>`,
    actionItemsCard(ov),
    `<div class="two-col">
       <div class="stack">
         <div id="today-next-meeting">${nextMeetingStrip()}</div>
         <div id="today-meetings">${meetingsCard(ov)}</div>
       </div>
       <div id="today-tickets">${topTicketsCard(ov)}</div>
     </div>`,
    activityCard(ov),
  ].join('\n');
  paintActivitySpark();     // reinsert cached spark into the header slot (no-op first render)
  ensureActivitySpark();    // kicks off the one-time fetch; no-op once already fetched
}

/* WIB hour (0-23) from a server-generated ISO timestamp (e.g. overview's
   generated_wib), computed via UTC fields + fixed +7 offset — NEVER via
   Date#getHours(), which would read the BROWSER's local timezone instead
   of WIB. Falls back to null (caller decides the safe default) when the
   timestamp is missing/unparseable. */
function wibHourFromIso(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : (d.getUTCHours() + 7) % 24;
}

/* 📊 Momentum band — 4 trendCards from /api/progress, directly under the
   hero tiles (the owner: "nice charts, so watching progress feels good"). Fetch
   failure (App.progress stays null) -> band absent, no chrome at all. */
function momentumBand() {
  const p = App.progress;
  if (!p) return '';
  const t = p.totals || {};
  const pts = arr => (Array.isArray(arr) ? arr : []).map(x => ({ label: (x.date || '').slice(5), value: x.count || 0 }));
  const cards = [
    Comp.trendCard({ title: 'Tickets done', icon: '🎯', points: pts(p.done_tickets), total: t.done_7d, unit: 'done · 7d', kind: 'cat-2' }),
    Comp.trendCard({ title: 'Meetings', icon: '🎥', points: pts(p.meetings), total: t.meetings_7d, unit: 'meetings · 7d', kind: 'cat-1' }),
    Comp.trendCard({ title: 'Docs created', icon: '📄', points: pts(p.docs_created), total: t.docs_7d, unit: 'docs · 7d', kind: 'cat-5' }),
    Comp.trendCard({ title: 'Commitments closed', icon: '🤝', points: pts(p.commitments_closed), total: t.commitments_closed_7d, unit: 'closed · 7d', kind: 'cat-3' }),
  ];
  return `<div class="momentum-band">${cards.join('')}</div>`;
}

/* 🗞️ Briefing card — newest Pagi/Malam section from /api/briefing. Collapsed
   by default, EXCEPT a still-fresh morning briefing before 15:00 WIB (opened
   so the owner sees it without an extra click). "See the previous one" opens
   `other` (the other kind's most recent section) in the Drawer. Empty/failed
   fetch -> card absent. */
function briefingCard() {
  const b = App.briefing;
  if (!b || !b.latest) return '';
  const kindEmoji = b.latest.kind === 'pagi' ? '🌅' : '🌙';
  const rawTitle = b.latest.title || '';
  /* the raw Dashboard.md header USUALLY already carries its own 🌅/🌙 (kind
     was detected off it) — only prefix ours when it's genuinely missing, so
     the kind indicator never doubles up */
  const count = /[🌅🌙]/.test(rawTitle) ? rawTitle : `${kindEmoji} ${rawTitle}`.trim();
  const hour = wibHourFromIso(App.overview?.generated_wib);
  const defaultOpen = b.latest.kind === 'pagi' && hour != null && hour < 15;
  const otherBtn = b.other
    ? `<button class="prep-link briefing-other-btn">See the previous one</button>` : '';
  const body = `<div class="md">${U.mdToHtml(b.latest.markdown || '')}</div>` +
    (otherBtn ? `<p class="row-note">${otherBtn}</p>` : '');
  return Comp.card({
    key: 'briefing', icon: '🗞️', title: 'Briefing (Pagi/Malam)',
    count, body, open: defaultOpen,
  });
}

/* "See the previous one" -> open the OTHER briefing section in the Drawer
   (rendered markdown) — not a repo file, so Drawer.openHtml not Drawer.open */
document.addEventListener('click', e => {
  const btn = e.target.closest('.briefing-other-btn');
  if (!btn) return;
  e.preventDefault();
  const other = App.briefing?.other;
  if (!other) return;
  const emoji = other.kind === 'pagi' ? '🌅' : '🌙';
  Drawer.openHtml(`${emoji} ${other.title || 'Briefing'}`, `<div class="md">${U.mdToHtml(other.markdown || '')}</div>`);
});

function heroTiles(ov) {
  const t = ov.tracker.counts || {};
  const w = ov.waiting.counts || {};
  const d = ov.decisions.counts || {};
  const c = ov.commitments.counts || {};
  const tiles = [
    Comp.statTile({
      key: 'overdue', icon: '⏰', label: 'Overdue', value: t.overdue ?? 0,
      sub: t.overdue ? 'open past due' : 'all clear',
      status: t.overdue ? 'serious' : 'good', href: '#work/overdue', tick: true,
    }),
    Comp.statTile({
      key: 'due-today', icon: '📅', label: 'Due today', value: t.due_today ?? 0,
      sub: t.due_today ? 'on the clock' : 'nothing due',
      status: t.due_today ? 'warn' : 'good', href: '#work/due-today', tick: true,
    }),
    /* stays on Today: breached items already render in the escalation
       strip below — a delegated click handler smooth-scrolls to it */
    Comp.statTile({
      key: 'sla', icon: '🚨', label: 'SLA breached', value: w.breached ?? 0,
      sub: w.breached ? 'chase below' : `${w.open ?? 0} inside SLA`,
      status: w.breached ? 'serious' : 'good', href: '#today', tick: true,
    }),
    Comp.statTile({
      key: 'decisions', icon: '⚖️', label: 'Decisions due', value: d.due ?? 0,
      sub: d.overdue ? `${d.overdue} overdue` : `${d.open ?? 0} open total`,
      status: d.overdue ? 'serious' : (d.due ? 'warn' : 'good'),
      href: '#work/decisions', tick: true,
    }),
    Comp.statTile({
      key: 'commitments', icon: '🤝', label: 'Commitments due', value: c.due ?? 0,
      sub: c.overdue ? `${c.overdue} overdue` : `${c.open ?? 0} open total`,
      status: c.overdue ? 'serious' : (c.due ? 'warn' : 'good'),
      href: '#work/commitments', tick: true,
    }),
  ];
  return `<div class="hero-row">${tiles.join('')}</div>`;
}

function escalationStrip(ov) {
  const items = ov.waiting.escalations || [];
  if (!items.length) return '';
  const rows = items.map(it => Comp.listRow({
    key: `esc:${it.id}`,
    icon: '⏳',
    title: it.what || it.id,
    meta: it.owner ? `waiting on ${it.owner}` : '',
    right: Comp.slaCountdown({ remainingHours: it.remaining_hours }) +
      (it.escalate_to ? Comp.badge('muted', `→ ${it.escalate_to}`) : '') +
      draftEscalationButton(it) + escalationActionButtons(it),
  }));
  return `<div class="escalation-strip" id="today-escalations">
    <div class="escalation-title">⏳ Escalations — chase these</div>
    <div class="rows">${rows.join('')}</div>
  </div>`;
}

/* small pill button carrying everything buildEscalationDraft() needs as
   data-* — same pattern components.js uses for chase-btn/make-ticket-btn */
function draftEscalationButton(it) {
  return `<button class="prep-link draft-escalation-btn"
    data-what="${U.esc(it.what || it.id || '')}"
    data-owner="${U.esc(it.owner || '')}"
    data-escalate-to="${U.esc(it.escalate_to || '')}"
    data-sla-hours="${Number.isFinite(Number(it.sla_hours)) ? it.sla_hours : ''}"
    data-remaining-hours="${it.remaining_hours != null ? it.remaining_hours : ''}"
    data-status="${U.esc(it.status || '')}"
    title="Draft an escalation message">✍ Draft escalation</button>`;
}

/* ✓ Done / 👋 Nudged — resolve or nudge a WAIT item straight from the
   escalation strip via POST /api/waiting-close. Not in components.js's
   delegated inventory (that endpoint didn't exist yet when it was written),
   so wired here the same way app.js already owns .draft-escalation-btn /
   .draft-copy-btn. */
function escalationActionButtons(it) {
  return `<button class="prep-link esc-close-btn" data-id="${U.esc(it.id)}" title="Mark resolved">✓ Done</button>
    <button class="prep-link esc-touch-btn" data-id="${U.esc(it.id)}" title="Mark as nudged">👋 Nudged</button>`;
}

document.addEventListener('click', async e => {
  const btn = e.target.closest('.esc-close-btn, .esc-touch-btn');
  if (!btn) return;
  e.preventDefault();
  if (btn.disabled) return;
  const id = btn.dataset.id;
  if (!id) return;
  const action = btn.classList.contains('esc-close-btn') ? 'close' : 'touch';
  btn.disabled = true;
  try {
    await U.fetchJSON('/api/waiting-close', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id, action }),
    });
    /* close is reversible (mis-click safety): the toast carries an Undo that
       POSTs action:'reopen' — same endpoint, watchdog CLI restores the item */
    const undo = action === 'close' ? {
      label: 'Undo',
      onClick: async () => {
        await U.fetchJSON('/api/waiting-close', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id, action: 'reopen' }),
        });
        Comp.toast(`Reopened: ${id}`, true);
        refreshOverview(true);
      },
    } : null;
    Comp.toast(action === 'close' ? `Marked resolved: ${id}` : `Nudge recorded: ${id}`, true, undo);
    refreshOverview(true);
  } catch (err) {
    Comp.toast(`Failed: ${err.message}`, false);
    btn.disabled = false;
  }
});

/* plain-prose escalation draft in the owner's voice: flowing sentences, no
   emoji, no bullet lists. NEVER a send path — Drawer + Copy button only. */
function buildEscalationDraft(it) {
  const what = it.what || 'this item';
  const owner = it.owner || 'the owner';
  const slaH = Number(it.sla_hours);
  const remH = Number(it.remaining_hours);
  const hasSla = Number.isFinite(slaH);
  const hasRem = Number.isFinite(remH);
  const elapsedH = (hasSla && hasRem) ? (slaH - remH) : null;
  const xh = elapsedH != null ? U.fmtAge(elapsedH) : 'a while';
  const yh = hasSla ? U.fmtAge(slaH) : null;
  const breached = it.status === 'breached' || (hasRem && remH < 0);
  let slaClause = 'past its SLA';
  if (yh && breached) slaClause = `past the ${yh} SLA`;
  else if (yh) slaClause = `with only ${U.fmtAge(Math.max(0, remH))} left on the ${yh} SLA`;

  if (it.escalate_to) {
    return `Hi ${it.escalate_to}, flagging that ${what} has been waiting on ${owner} for ${xh} now, ${slaClause}. Could you help push this along?`;
  }
  return `Hi ${owner}, quick nudge on ${what} — it has been waiting for ${xh} now, ${slaClause}. Any update would help.`;
}

function escalationDraftBody(draftText) {
  return `<div class="stack">
    <p class="row-note">Plain-prose draft — review before sending anywhere. Nothing here sends on its own.</p>
    <textarea class="draft-area" rows="6">${U.esc(draftText)}</textarea>
    <button class="prep-link draft-copy-btn">Copy</button>
  </div>`;
}

/* SLA-breached hero tile: don't leave Today — the breached items are the
   escalation strip right below the hero row, so scroll there instead */
document.addEventListener('click', e => {
  const tile = e.target.closest('#tab-today .stat-tile[data-key="sla"]');
  if (!tile) return;
  e.preventDefault();
  const strip = document.getElementById('today-escalations');
  if (strip) strip.scrollIntoView({ behavior: 'smooth', block: 'start' });
});

/* ✍ Draft escalation -> build the plain-prose draft, open it in the drawer */
document.addEventListener('click', e => {
  const btn = e.target.closest('.draft-escalation-btn');
  if (!btn) return;
  e.preventDefault();
  const it = {
    what: btn.dataset.what,
    owner: btn.dataset.owner,
    escalate_to: btn.dataset.escalateTo,
    sla_hours: btn.dataset.slaHours !== '' ? Number(btn.dataset.slaHours) : null,
    remaining_hours: btn.dataset.remainingHours !== '' ? Number(btn.dataset.remainingHours) : null,
    status: btn.dataset.status,
  };
  const draft = buildEscalationDraft(it);
  Drawer.openHtml(`Draft escalation — ${it.what || it.owner || ''}`, escalationDraftBody(draft));
});

/* Copy -> navigator.clipboard + toast (never a send path) */
document.addEventListener('click', async e => {
  const copyBtn = e.target.closest('.draft-copy-btn');
  if (!copyBtn) return;
  e.preventDefault();
  const ta = copyBtn.closest('.drawer-body')?.querySelector('.draft-area');
  const text = ta ? ta.value : '';
  try {
    await navigator.clipboard.writeText(text);
    Comp.toast('Draft copied', true);
  } catch (err) {
    Comp.toast(`Copy failed: ${err.message}`, false);
  }
});

/* meeting-local action-item sources are relative repo paths, not URLs
   (Fathom ones are full https:// links) — Comp.actionItemRow always renders
   the source as a plain <a href> "source ↗" link, so a relative path would
   404 in a new tab. Intercept that case here and open it in the Drawer
   (rendered markdown) instead — Fathom's real https:// links pass through
   untouched. */
document.addEventListener('click', e => {
  const link = e.target.closest('.action-item-row a.prep-link');
  if (!link) return;
  const href = link.getAttribute('href') || '';
  if (/^https?:\/\//i.test(href)) return;
  e.preventDefault();
  const label = link.closest('.action-item-row')?.querySelector('.row-title')?.getAttribute('title') || 'Meeting notes';
  Drawer.open(label, href);
});

/* 📋 Commands awaiting approval — command-queue workers that finished and left a
   draft for the owner to review. Empty (no chrome) when nothing is pending. Each row
   opens the draft in the Drawer (rendered markdown) and can be ack'd to clear it. */
function approvalsCard() {
  const review = App.commandQueue?.review || [];
  if (!review.length) return '';
  const rows = review.map(r => `
    <div class="row" data-cq-row="${U.esc(r.key)}">
      <div class="row-main">
        <span class="row-title" title="${U.esc(r.command || '')}">
          <strong>${U.esc(r.ticket_id || '')}</strong> ${U.esc((r.command || '').slice(0, 90))}
        </span>
        <span class="row-meta">${U.esc(r.category || '')}${r.model ? ' · ' + U.esc(r.model) : ''}</span>
      </div>
      <span class="row-right">
        <button class="prep-link" data-drawer-path="${U.esc(r.draft_path || '')}"
          data-drawer-title="${U.esc((r.ticket_id || '') + ' — draft')}">📄 review draft</button>
        <button class="prep-link cq-ack-btn" data-cq-key="${U.esc(r.key)}" title="Mark reviewed">✓ done</button>
      </span>
    </div>`).join('');
  return Comp.card({
    key: 'cmd-approvals', icon: '📋', title: 'Commands awaiting your approval',
    count: `${review.length}`, open: true,
    body: `<div class="rows">${rows}</div>`,
  });
}

/* 🚨 Commands that produced nothing — workers that exited cleanly without leaving
   the deliverable they declared, or that found no usable backend at all. The server
   marks these 'error', but until now the page only ever drew the 'review' list, so
   they sat in the payload and never on the screen. An alarm nobody can see is the
   same as no alarm. Deliberately not dismissible: these clear by being fixed. */
function failuresCard() {
  const errors = App.commandQueue?.errors || [];
  if (!errors.length) return '';
  const rows = errors.map(r => {
    const rc = (r.rc === null || r.rc === undefined) ? '' : ` · rc=${U.esc(String(r.rc))}`;
    const when = r.finished_wib ? ` · ${U.esc(r.finished_wib)}` : '';
    const log = r.log
      ? `<button class="prep-link" data-drawer-path="${U.esc(r.log)}"
           data-drawer-title="${U.esc((r.ticket_id || 'job') + ' — log')}">📄 log</button>`
      : '';
    return `
    <div class="row" data-cq-row="${U.esc(r.key)}">
      <div class="row-main">
        <span class="row-title" title="${U.esc(r.command || '')}">
          <strong>${U.esc(r.ticket_id || '')}</strong> ${U.esc((r.command || '').slice(0, 90))}
        </span>
        <span class="row-meta">${U.esc(r.reason || 'unknown')}${rc}${when}</span>
      </div>
      <span class="row-right">${log}</span>
    </div>`;
  }).join('');
  return Comp.card({
    key: 'cmd-failures', icon: '🚨', title: 'Commands that produced nothing',
    count: `${errors.length}`, open: true,
    body: `<div class="rows">${rows}</div>`,
  });
}

/* ack a reviewed command-queue draft -> clears it from the approval list */
document.addEventListener('click', async e => {
  const btn = e.target.closest('.cq-ack-btn');
  if (!btn) return;
  e.preventDefault();
  e.stopPropagation();
  const key = btn.getAttribute('data-cq-key');
  btn.disabled = true;
  try {
    await U.fetchJSON('/api/command-queue-ack', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key }),
    });
    btn.closest('[data-cq-row]')?.remove();
    Comp.toast('Draft acknowledged', true);
  } catch (err) {
    btn.disabled = false;
    Comp.toast(`Ack failed: ${err.message}`, false);
  }
});

/* today's meetings: calendar (slow, separate fetch) joined with prep cards */
function meetingsCard(ov) {
  const cards = ov.premeeting?.cards || [];
  let body;
  if (App.calendarState === 'loading' || App.calendarState === 'idle') {
    body = skeletonBlock(3);
  } else if (App.calendarState === 'error') {
    body = `<div class="load-error">Calendar unavailable: ${U.esc(App.calendarError || 'unknown error')}</div>`
      + (cards.length ? prepOnlyRows(cards) : '');
  } else {
    const events = (App.calendar?.events || []).filter(e => e.isToday);
    if (!events.length) {
      body = Comp.emptyState({
        icon: '🌤', title: 'No meetings today',
        hint: cards.length
          ? `${cards.length} prep card${cards.length > 1 ? 's exist' : ' exists'} for today — listed below`
          : 'No pre-meeting cards generated either. Enjoy the focus time.',
      }) + (cards.length ? prepOnlyRows(cards) : '');
    } else {
      body = `<div class="rows">${events.map(ev => meetingRow(ev, cards)).join('')}</div>`;
    }
  }
  const n = (App.calendar?.events || []).filter(e => e.isToday).length;
  return Comp.card({
    key: 'meetings-today', icon: '🎥', title: "Today's meetings",
    count: App.calendarState === 'ready' ? `${n} today` : '…',
    body, open: true,
  });
}

function normTitle(s) {
  return (s || '').toLowerCase().replace(/[^a-z0-9 ]+/g, ' ').replace(/\s+/g, ' ').trim();
}

function matchPrep(ev, cards) {
  const evTime = (ev.timeRange || '').slice(0, 5);
  const evTitle = normTitle(ev.summary);
  return cards.find(c => {
    if (c.time_wib && evTime && String(c.time_wib).slice(0, 5) === evTime) return true;
    const ct = normTitle(c.title);
    return ct && evTitle && (ct.includes(evTitle) || evTitle.includes(ct));
  });
}

function meetingRow(ev, cards) {
  const prep = matchPrep(ev, cards);
  const time = ev.isAllDay ? 'all day' : (ev.timeRange || '').slice(0, 5);
  const prepBtn = prep
    ? `<button class="prep-link" data-drawer-path="${U.esc(prep.relPath)}" data-drawer-title="${U.esc(prep.title)}">📋 prep</button>`
    : '';
  return `<div class="row" data-key="mtg:${U.esc(ev.summary)}">
    <span class="time-pill">${U.esc(time)}</span>
    <span class="row-title" title="${U.esc(ev.summary)}">${U.esc(ev.summary)}</span>
    ${Comp.badge('muted', ev.account || 'cal')}
    <span class="row-right">${prepBtn}</span>
  </div>`;
}

/* absolute instant (Date) for a today event's WIB start time, built from its
   'HH:MM'-prefixed timeRange + calendar 'date' — comparable to any other
   Date/epoch regardless of the BROWSER's own timezone, since Date math
   always operates on the absolute instant, never local calendar fields. */
function parseWibEventStart(ev) {
  const hhmm = (ev.timeRange || '').slice(0, 5);
  if (!ev.date || !/^\d{2}:\d{2}$/.test(hhmm)) return null;
  const d = new Date(`${ev.date}T${hhmm}:00+07:00`);
  return Number.isNaN(d.getTime()) ? null : d;
}

/* ⏭ Next-meeting strip — above Today's meetings card. Absent while the
   calendar hasn't resolved yet or on error (nothing to show), and absent
   when there's no more timed meeting today. 'now' comes from the overview's
   server-generated WIB timestamp (falls back to the browser clock only if
   that's missing) so this stays correct even if the browser isn't set to
   WIB. */
function nextMeetingStrip() {
  if (App.calendarState !== 'ready' || !App.calendar) return '';
  const nowIso = App.overview?.generated_wib;
  const now = nowIso ? new Date(nowIso) : new Date();
  const upcoming = (App.calendar.events || [])
    .filter(ev => ev.isToday && !ev.isAllDay)
    .map(ev => ({ ev, start: parseWibEventStart(ev) }))
    .filter(x => x.start && x.start.getTime() > now.getTime())
    .sort((a, b) => a.start - b.start);
  if (!upcoming.length) return '';
  const { ev, start } = upcoming[0];
  const diffMin = Math.max(0, Math.round((start.getTime() - now.getTime()) / 60000));
  const inText = diffMin >= 60 ? `${Math.floor(diffMin / 60)}h ${diffMin % 60}m` : `${diffMin}m`;
  const cards = App.overview?.premeeting?.cards || [];
  const prep = matchPrep(ev, cards);
  const prepBtn = prep
    ? `<button class="prep-link" data-drawer-path="${U.esc(prep.relPath)}" data-drawer-title="${U.esc(prep.title)}">📋 Prep</button>`
    : '';
  const time = (ev.timeRange || '').slice(0, 5);
  return `<div class="row next-meeting-strip" data-key="next-meeting">
    <span class="row-icon">⏭</span>
    <span class="row-title">Next: <b>${U.esc(time)} WIB</b> ${U.esc(ev.summary)} · in ${U.esc(inText)}</span>
    <span class="row-right">${prepBtn}</span>
  </div>`;
}

function prepOnlyRows(cards) {
  return `<div class="section-label">Prep cards</div><div class="rows">` +
    cards.map(c => `<div class="row" data-key="prep:${U.esc(c.file)}">
      <span class="time-pill">${U.esc(c.time_wib ? String(c.time_wib).slice(0, 5) : '—')}</span>
      <span class="row-title" title="${U.esc(c.title)}">${U.esc(c.title)}</span>
      <span class="row-right"><button class="prep-link" data-drawer-path="${U.esc(c.relPath)}" data-drawer-title="${U.esc(c.title)}">📋 open</button></span>
    </div>`).join('') + '</div>';
}

/* 🎯 Action items from meetings — open commitments sourced from a meeting
   (Fathom or local recorder), last 7d, server-capped at 6. Hidden entirely
   when empty rather than showing EmptyState chrome: Today already has the
   hero row + meetings/tickets cards on screen, so an empty-state block here
   would just be redundant noise. (If ever surfaced on a truly bare screen,
   the copy would be "No new action items from meetings".)

   v2: hand-built row (not Comp.actionItemRow — that builder always renders
   the "→ Jadiin ticket" button with no room for a ticket chip or an AI
   button) reusing the SAME documented classes/attributes Comp.actionItemRow
   uses (.action-item-row, .make-ticket-btn, .prep-link) so the existing
   delegated click wiring in components.js still fires — plus
   Comp.ticketChip when a ticket is already linked, and Comp.aiButton for
   the AI draft action. */
function actionItemRowV2(it) {
  const txt = it.text || '(empty action item)';
  const sourceLabel = it.source_type === 'fathom' ? 'Fathom' : 'MOM lokal';
  const permalink = it.permalink || it.source_ref;
  const link = permalink
    ? `<a class="prep-link" href="${U.esc(permalink)}" target="_blank" rel="noopener">source ↗</a>` : '';
  const ticketOrBtn = it.ticket_id
    ? Comp.ticketChip(it.ticket_id)
    : `<button class="make-ticket-btn"
        data-ai-text="${U.esc(txt)}"
        data-ai-ref="${U.esc(it.source_ref || '')}">→ Jadiin ticket</button>`;
  const aiBtn = Comp.aiButton({ kind: 'commitment', ref: it.id, label: '🤖 AI draftin' });
  return `<div class="row action-item-row" data-key="ai:${U.esc(it.id ?? txt.slice(0, 40))}" data-commitment-id="${U.esc(it.id ?? '')}">
    <span class="row-icon">☑️</span>
    <span class="row-title" title="${U.esc(txt)}">${U.esc(txt)}</span>
    <span class="row-badges">${Comp.badge('muted', sourceLabel)}</span>
    <span class="row-right">${link}${ticketOrBtn}${aiBtn}</span>
  </div>`;
}

function actionItemsCard(ov) {
  const items = (ov.meeting_actions || []).slice(0, 6);
  if (!items.length) return '';
  const rows = items.map(actionItemRowV2).join('');
  return Comp.card({
    key: 'meeting-actions', icon: '🎯', title: 'Action items from meetings',
    count: `${items.length} baru`, body: `<div class="rows">${rows}</div>`, open: true,
  });
}

/* Link a just-created ticket back to the commitment row that asked for it.
   components.js's `.make-ticket-btn` handler reads the triggering row's
   data-commitment-id (actionItemRowV2 above + Comp.actionItemRow both set
   it) and echoes it as `commitment_id` in the psb:ticket-saved detail, so
   correlation is exact per event — no click-time bookkeeping. ActionBar
   saves reuse the same event name WITHOUT commitment_id and are ignored
   here. */
window.addEventListener('psb:ticket-saved', async e => {
  const commitmentId = e.detail?.commitment_id;
  const newTicketId = e.detail?.id;
  if (!commitmentId || !newTicketId) return;
  try {
    await U.fetchJSON('/api/commitment-link', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ commitment_id: commitmentId, ticket_id: newTicketId }),
    });
    refreshOverview(true);   // re-fetch so the row now shows Comp.ticketChip instead of the button
  } catch (err) {
    Comp.toast(`Commitment→ticket link failed: ${err.message}`, false);
  }
});

/* top tickets (server-computed ≤8: overdue oldest-first, due-today, P0) */
function topTicketsCard(ov) {
  const top = ov.tracker.top || [];
  const counts = ov.tracker.counts || {};
  const body = top.length
    ? `<div class="rows">${top.map(t => ticketRow(t, ov.today)).join('')}</div>`
    : Comp.emptyState({ icon: '✨', title: 'Nothing urgent', hint: 'No overdue, due-today, or open P0 tickets.' });
  const card = Comp.card({
    key: 'top-tickets', icon: '🎯', title: 'Top tickets',
    count: `${counts.open ?? 0} open · ${counts.overdue ?? 0} overdue`,
    status: counts.overdue ? 'serious' : (counts.due_today ? 'warn' : null),
    body, open: true,
  });
  const fr = freshnessFor('tickets.json (tracker)');
  return Comp.staleWrap({ state: fr.state, ageH: fr.age_h, inner: card });
}

function ticketRow(t, today) {
  const fd = U.fmtDue(t.due, today);
  const dueBadge = !fd.text ? '' :
    fd.state === 'overdue' ? Comp.badge('serious', fd.text) :
    fd.state === 'today' ? Comp.badge('warn', fd.text) :
    Comp.badge('muted', fd.text);
  const comments = (t.comments || []).slice(-3).map(c =>
    `<div class="comment-item"><b>${U.esc(c.by || '?')}</b> ${U.esc(c.change || '')}${c.text ? ` — ${U.esc(c.text)}` : ''} <span>· ${U.esc((c.ts_wib || '').slice(0, 16).replace('T', ' '))}</span></div>`).join('');
  const refs = Comp.linkChips(t.links);
  const expandBody =
    (t.note ? `<p class="row-note">${U.esc(t.note)}</p>` : '') +
    (refs ? `<p class="row-note">Referensi: ${refs}</p>` : '') +
    `<p class="comment-item"><b>${U.esc(t.id)}</b> · owner: <b>${U.esc(t.owner || '—')}</b> · ${U.esc(t.kind || '')} · ${U.esc(t.status || '')}</p>` +
    (comments ? `<div class="comment-list">${comments}</div>` : '') +
    Comp.actionBar(t);
  return Comp.listRow({
    key: `t:${t.id}`,
    icon: t.kind === 'delegated' ? '👤' : t.kind === 'outbound' ? '📨' : '🎫',
    title: t.title,
    badges: [
      Comp.badge((t.priority || 'p2').toLowerCase(), t.priority || '—'),
      Comp.badge(projCat(t.project), t.project || 'Other'),
    ],
    meta: t.owner && t.owner !== 'the owner' ? t.owner : '',
    right: dueBadge,
    expandBody,
  });
}

/* Today spark: last-14-day activity counts as tiny bars in the "Recent
   activity" card header — fetched once, lazily, AFTER first paint (never
   blocks/reflows the hero row above it). renderToday() rebuilds #tab-today's
   innerHTML on every refresh, so paintActivitySpark() re-inserts the cached
   markup on each render; ensureActivitySpark() only ever fetches once. */
let activitySparkHtml = null;      // null = not fetched yet, '' = fetched empty
let activitySparkLoading = false;

async function ensureActivitySpark() {
  if (activitySparkHtml !== null || activitySparkLoading) return;
  activitySparkLoading = true;
  try {
    const d = await U.fetchJSON('/api/activity-spark');
    const points = (d.days || []).map(x => ({ label: (x.date || '').slice(5), value: x.count || 0 }));
    activitySparkHtml = Comp.miniBars(points, { w: 96, h: 22, label: 'events last 14d' }) || '';
  } catch {
    activitySparkHtml = '';
  } finally {
    activitySparkLoading = false;
  }
  paintActivitySpark();
}

function paintActivitySpark() {
  if (!activitySparkHtml) return;
  const summary = document.querySelector('#tab-today details[data-key="activity"] > summary');
  if (!summary || summary.querySelector('.activity-spark-slot')) return;
  const count = summary.querySelector('.card-count');
  const slot = document.createElement('span');
  slot.className = 'activity-spark-slot';
  slot.innerHTML = activitySparkHtml;
  if (count) count.insertAdjacentElement('afterend', slot);
  else summary.appendChild(slot);
}

function activityCard(ov) {
  const events = ov.activity || [];
  const icons = { ticket_edit: '✏️', ticket_create: '➕', doc_create: '📄', doc_update: '📝', harness_build: '🔧' };
  const body = events.length
    ? `<div class="rows">${events.map(e => Comp.listRow({
        key: `act:${e.event_id}`,
        icon: icons[e.action] || '·',
        title: e.summary || e.action,
        badges: e.project && e.project !== 'harness' ? [Comp.badge(projCat(e.project), e.project)] : [],
        meta: `${e.actor || ''} · ${U.fmtAge((Date.now() - Date.parse(e.ts_wib)) / 3600000)} ago`,
      })).join('')}</div>`
    : Comp.emptyState({ icon: '🌙', title: 'No recent activity' });
  return Comp.card({
    key: 'activity', icon: '📈', title: 'Recent activity',
    count: `last ${events.length}`, body, open: false,
  });
}

/* calendar is SLOW (~15s: Google + subprocess) → separate fetch + skeleton,
   never blocks first paint; refetched at most every 5 minutes */
async function ensureCalendar() {
  if (App.calendarState === 'loading') return;
  if (App.calendarState !== 'idle' && Date.now() - App.calendarAt < 5 * 60 * 1000) return;
  App.calendarState = 'loading';
  renderMeetingsSlot();
  try {
    App.calendar = await U.fetchJSON('/api/calendar', { timeoutMs: 60000 });
    App.calendarState = 'ready';
    App.calendarError = null;
  } catch (err) {
    App.calendarState = 'error';
    App.calendarError = err.message;
  }
  App.calendarAt = Date.now();
  renderMeetingsSlot();
}

function renderMeetingsSlot() {
  const slot = $id('today-meetings');
  if (slot && App.overview && canRender(slot)) slot.innerHTML = meetingsCard(App.overview);
  const nextSlot = $id('today-next-meeting');
  if (nextSlot && canRender(nextSlot)) nextSlot.innerHTML = nextMeetingStrip();
}

/* ── boot ── */
function boot() {
  UI.load();
  Drawer.init();
  updateChrome();

  $id('tab-nav').addEventListener('click', e => {
    const btn = e.target.closest('.tab-btn');
    if (btn) location.hash = `#${btn.dataset.tab}`;
  });

  $id('btn-refresh').addEventListener('click', () => {
    App.calendarAt = 0;                       // force calendar refetch too
    refreshOverview(true);
    if (App.activeTab === 'today') ensureCalendar();
  });

  /* ActionBar saves (components.js emits this) → targeted refetch */
  window.addEventListener('psb:ticket-saved', () => refreshOverview(true));

  /* ⏱ Chase (components.js emits this) → refetch: escalations may now
     include the new watchdog item; toast is already handled by the button */
  window.addEventListener('psb:waiting-added', () => refreshOverview(true));

  window.addEventListener('hashchange', applyRoute);
  applyRoute();
  /* a page opened straight on #find/<ID> — quickfind.js registers its own
     hashchange listener, but the FIRST paint needs Drawer.init() to have run,
     which only happens here */
  if (window.QuickFind) QuickFind.openFromHash();
  refreshOverview(true);
  setInterval(() => refreshOverview(false), 60000);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refreshOverview(false);
  });
}

boot();
