/* ledger-hover.js — makes every ledger ID the owner is handed behave like a real
   reference rather than a link out.

   Every COM-/WAIT-/DEC- id written for the owner ships as a link to
   http://localhost:3737/#find/<ID> (CLAUDE.md, "Every id the owner sees is a link").
   Those are absolute same-origin URLs, so clicking one inside the dashboard
   used to reload the whole page just to move the hash. Reading three ids in a
   briefing meant three full reloads and losing the tab you were on.

   Two behaviours replace that, and both are delegated so they work on anything
   painted later (drawer bodies, markdown, tab panels, the Dashboard.md render):

     • HOVER  → after a short delay, a floating card with the record's
                definition: what it is, who owns it, the work-tree node, the
                dates, the SLA. Read-only, dismissed by leaving or Esc.
     • CLICK  → opens the SAME right-hand Drawer the deep link would have
                opened, with no navigation and no reload. The hash is still
                updated (replaceState) so the permalink stays copyable.

   The popover is a preview, not a second copy of the record: it stops at the
   fields that answer "what is this and whose is it". The Drawer remains the
   full card, and every popover carries a line saying so.

   Read-only throughout: /api/ledger-find never mutates. */
(() => {
  'use strict';

  const OPEN_DELAY = 320;      /* hover intent, so a cursor crossing a link is ignored */
  const CLOSE_DELAY = 180;     /* grace period, so the cursor can travel into the card */
  const CACHE = new Map();     /* id -> {rec, at} — rec null = looked up, not found */
  const HIT_TTL = 60000;       /* a found record can move (status, notes) while the tab is open */
  const MISS_TTL = 5000;       /* a miss is usually a record written seconds ago: never cache it long */

  let pop = null;              /* the single popover element, created on first use */
  let openTimer = null;
  let closeTimer = null;
  let currentId = null;
  let currentAnchor = null;

  /* ── which anchors are ledger links ─────────────────────────────────────
     Matches both forms in circulation: the absolute URL written into
     briefings and Dashboard.md, and the bare in-page hash quickfind emits. */
  function ledgerIdFrom(a) {
    if (!a) return null;
    const raw = a.getAttribute('href') || '';
    if (!raw) return null;
    /* bare hash, same page */
    let m = /^#find\/(.+)$/.exec(raw);
    if (m) return decodeURIComponent(m[1]).trim();
    /* absolute or root-relative, but only on THIS origin — an id pointing at
       another host is somebody else's dashboard and is left alone */
    try {
      const u = new URL(raw, location.href);
      if (u.origin !== location.origin) return null;
      m = /^#find\/(.+)$/.exec(u.hash || '');
      if (m) return decodeURIComponent(m[1]).trim();
    } catch { /* not a parseable URL: not ours */ }
    return null;
  }

  function isLedgerId(id) {
    return /^(COM|WAIT|DEC)-\d+$/i.test(id || '');
  }

  /* ── data ───────────────────────────────────────────────────────────── */
  async function lookup(id) {
    /* A miss used to stick for the life of the page, so an id looked up before
       its record was written kept reading "not in the local ledgers" until a
       full reload. Both hits and misses expire now, misses fast. */
    const hit = CACHE.get(id);
    if (hit && (Date.now() - hit.at) < (hit.rec ? HIT_TTL : MISS_TTL)) return hit.rec;
    const data = await U.fetchJSON(`/api/ledger-find?q=${encodeURIComponent(id)}`);
    const results = Array.isArray(data.results) ? data.results : [];
    const rec = results.find(r => (r.id || '').toLowerCase() === id.toLowerCase()) || null;
    CACHE.set(id, { rec, at: Date.now() });
    return rec;
  }

  /* ── rendering ──────────────────────────────────────────────────────── */
  const KIND_ICON = { commitments: '✅', waiting_on: '⏳', decisions: '⚖️' };
  const KIND_WORD = {
    commitments: 'Commitment · the owner owes this',
    waiting_on: 'Waiting-on · someone owes the owner',
    decisions: 'Decision · a call to be made',
  };
  const PIC_LABEL = { commitments: 'Owed to', waiting_on: 'Waiting on', decisions: 'Decider' };

  function statusKind(status) {
    const s = (status || '').toLowerCase();
    if (s === 'breached') return 'critical';
    if (s === 'open' || s === 'pending' || s === '') return 'warn';
    if (s === 'answered' || s === 'closed' || s === 'done' || s === 'decided') return 'good';
    return 'muted';
  }

  function slaText(hours) {
    if (typeof hours !== 'number' || !isFinite(hours) || hours <= 0) return '';
    const days = hours / 24;
    const d = days >= 1 ? ` (${Number.isInteger(days) ? days : days.toFixed(1)}d)` : '';
    return `${Number.isInteger(hours) ? hours : hours.toFixed(1)}h${d}`;
  }

  function row(label, value, cls) {
    if (!value) return '';
    return `<div class="lh-row${cls ? ' ' + cls : ''}">` +
      `<span class="lh-k">${U.esc(label)}</span>` +
      `<span class="lh-v">${U.esc(value)}</span></div>`;
  }

  /* trim the long body text so the popover stays a preview */
  function clamp(s, n) {
    s = (s || '').trim();
    if (s.length <= n) return s;
    return s.slice(0, n).replace(/\s+\S*$/, '') + '…';
  }

  function cardHtml(r) {
    const badge = (window.Comp && Comp.badge)
      ? Comp.badge(statusKind(r.status), r.status || 'open')
      : `<span class="badge">${U.esc(r.status || 'open')}</span>`;

    /* waiting_on derives title and context from the same field, so printing
       both would repeat the sentence back at the reader */
    const context = (r.context && r.context.trim() !== (r.title || '').trim()) ? r.context : '';
    const body = r.decision || context || '';

    const created = r.created_wib ? `${r.created_wib} (${r.created_ago})` : '';
    const breached = r.breached_wib ? `${r.breached_wib} (${r.breached_ago})` : '';
    const closed = r.closed_wib
      ? `${r.closed_wib} (${r.closed_ago})${r.closed_by ? ' by ' + r.closed_by : ''}` : '';
    let followup = r.followup_wib ? `${r.followup_wib} (${r.followup_ago})` : 'never nudged';
    if (r.nudge_count) followup += ` · ${r.nudge_count}× nudged`;

    /* 'unfiled' is a real state, not a missing value: it means the record has
       no home in the work tree and is waiting on the owner to say which node */
    const node = r.node === 'unfiled'
      ? 'unfiled · needs a node'
      : (r.node_path || r.node || '');

    return `
      <div class="lh-head">
        <span class="lh-icon">${KIND_ICON[r.kind] || '🎫'}</span>
        <span class="lh-id">${U.esc(r.id)}</span>
        ${badge}
      </div>
      <div class="lh-kind">${U.esc(KIND_WORD[r.kind] || '')}</div>
      <div class="lh-title">${U.esc(r.title || '(no title)')}</div>
      ${body ? `<div class="lh-body">${r.decision ? '<strong>Decided:</strong> ' : ''}${U.esc(clamp(body, 320))}</div>` : ''}
      <div class="lh-rows">
        ${row(PIC_LABEL[r.kind] || 'PIC', r.owner || '—')}
        ${row('Work tree', node, r.node === 'unfiled' ? 'lh-warn' : '')}
        ${row('Project', r.project)}
        ${row('Opened', created)}
        ${row('Due', r.due)}
        ${row('SLA', slaText(r.sla_hours))}
        ${row('Escalate to', r.escalate_to)}
        ${row('Last follow-up', followup)}
        ${row('Breached', breached, 'lh-warn')}
        ${row('Closed', closed)}
      </div>
      <div class="lh-foot">Click to open the full record in the panel</div>`;
  }

  /* ── popover element + positioning ──────────────────────────────────── */
  function ensurePop() {
    if (pop) return pop;
    pop = document.createElement('div');
    pop.className = 'lh-pop';
    pop.setAttribute('role', 'tooltip');
    /* keep it open while the cursor is inside, so long text can be read */
    pop.addEventListener('mouseenter', () => clearTimeout(closeTimer));
    pop.addEventListener('mouseleave', scheduleHide);
    document.body.appendChild(pop);
    return pop;
  }

  /* place beside the anchor, secondaryping above/left when the viewport runs out.
     Measured after paint so the real height is known, not guessed. */
  function place(anchor) {
    const el = ensurePop();
    const a = anchor.getBoundingClientRect();
    const p = el.getBoundingClientRect();
    const gap = 8, margin = 10;

    let top = a.bottom + gap;
    if (top + p.height > window.innerHeight - margin) {
      const above = a.top - gap - p.height;
      top = above >= margin ? above : Math.max(margin, window.innerHeight - margin - p.height);
    }

    let left = a.left;
    if (left + p.width > window.innerWidth - margin) left = window.innerWidth - margin - p.width;
    if (left < margin) left = margin;

    el.style.top = `${Math.round(top)}px`;
    el.style.left = `${Math.round(left)}px`;
  }

  function hide() {
    clearTimeout(openTimer);
    clearTimeout(closeTimer);
    currentId = null;
    currentAnchor = null;
    if (pop) pop.classList.remove('is-open');
  }

  function scheduleHide() {
    clearTimeout(closeTimer);
    closeTimer = setTimeout(hide, CLOSE_DELAY);
  }

  async function show(anchor, id) {
    const el = ensurePop();
    currentId = id;
    currentAnchor = anchor;

    el.innerHTML = `<div class="lh-loading">${U.esc(id)} …</div>`;
    el.classList.add('is-open');
    place(anchor);

    let rec;
    try {
      rec = await lookup(id);
    } catch (err) {
      if (currentId !== id) return;
      el.innerHTML = `<div class="lh-empty">Could not load ${U.esc(id)}: ${U.esc(err.message || 'lookup failed')}</div>`;
      place(anchor);
      return;
    }
    if (currentId !== id) return;   /* the cursor moved on while we fetched */

    el.innerHTML = rec
      ? cardHtml(rec)
      : `<div class="lh-empty"><strong>${U.esc(id)}</strong> is not in the local ledgers.` +
        ` Either the id is wrong, or this checkout is behind origin.</div>`;
    place(anchor);
  }

  /* ── events, all delegated off document ─────────────────────────────── */
  document.addEventListener('mouseover', e => {
    const a = e.target.closest && e.target.closest('a[href]');
    if (!a) return;
    const id = ledgerIdFrom(a);
    if (!id || !isLedgerId(id)) return;
    if (a === currentAnchor && pop && pop.classList.contains('is-open')) {
      clearTimeout(closeTimer);
      return;
    }
    clearTimeout(openTimer);
    clearTimeout(closeTimer);
    openTimer = setTimeout(() => show(a, id), OPEN_DELAY);
  });

  document.addEventListener('mouseout', e => {
    const a = e.target.closest && e.target.closest('a[href]');
    if (!a) return;
    if (!ledgerIdFrom(a)) return;
    clearTimeout(openTimer);
    scheduleHide();
  });

  /* click: open the panel in place. Never navigate, never reload. */
  document.addEventListener('click', e => {
    if (e.defaultPrevented) return;
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;   /* let "open in new tab" work */
    if (e.button !== 0) return;
    const a = e.target.closest && e.target.closest('a[href]');
    if (!a) return;
    const id = ledgerIdFrom(a);
    if (!id || !isLedgerId(id)) return;
    if (!window.QuickFind || typeof QuickFind.run !== 'function') return;  /* fall back to the link */

    e.preventDefault();
    hide();
    /* keep the permalink copyable from the address bar without firing
       hashchange, which would run the same lookup a second time */
    const target = `#find/${encodeURIComponent(id)}`;
    if (location.hash !== target) history.replaceState(null, '', target);
    QuickFind.run(id, true);
  });

  document.addEventListener('keydown', e => { if (e.key === 'Escape') hide(); });

  /* Scroll follows the anchor rather than dismissing the card. Capture-phase
     scroll fires for ANY scrollable element on the page, so a tab lazily
     painting its rows used to snatch the popover away mid-sentence. Only a
     link that has actually left the viewport closes it. */
  function reflow() {
    if (!pop || !pop.classList.contains('is-open') || !currentAnchor) return;
    if (!currentAnchor.isConnected) { hide(); return; }
    const r = currentAnchor.getBoundingClientRect();
    const offscreen = r.bottom < 0 || r.top > window.innerHeight || r.right < 0 || r.left > window.innerWidth;
    if (offscreen) hide(); else place(currentAnchor);
  }
  window.addEventListener('scroll', reflow, true);
  window.addEventListener('resize', reflow);

  /* exposed for tests and for any tab that wants to warm the cache */
  window.LedgerHover = { lookup, hide, _cache: CACHE };
})();
