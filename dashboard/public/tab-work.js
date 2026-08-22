/* ═══════════════════════════════════════════════════════════════════
   tab-work.js — 📋 Work tab (Stage 2 + v2 drill). Owns #tab-work only.
   Sections: Tracker (chips + create + ActionBar, jiraChip, subtask
   rollups) / Portfolio (per-team glanceable cards, initiative rows link
   to the drill) / Decisions / Commitments (stale-aware) / People.
   Drill: `#work/init/<id>` -> load(filter) sees "init/<id>" -> breadcrumb
   + health/summary + Blockers card (chaseButton) + Tasks card (taskRow
   hierarchy; note/comments/ActionBar fused into each top-level ticket row
   via taskRow's expandBody slot).
   Uses ONLY Comp, U, Drawer (per components.js) + the CSS classes
   documented at the top of components.js, plus window.projCat
   (documented in app.js as shared with Stage 2 tabs). No new fetch
   helpers, no setInterval.
   ═══════════════════════════════════════════════════════════════════ */
'use strict';

window.Tabs = window.Tabs || {};

(function () {
  const state = {
    filter: null,
    creatingTicket: false,
    tracker: null, trackerError: null,
    portfolio: null, portfolioError: null,
    decisions: null, decisionsError: null,
    commitments: null, commitmentsError: null,
    aiRuns: null, aiRunsError: null,
    stakeholders: null, stakeholdersError: null,
    initiativeId: null, initiativeDetail: null, initiativeDetailError: null,
    workTree: null, workTreeError: null,
    wtOpen: new Set(['root-ecom', 'old', 'exampleprogram', 'd2', 'root-b2c']),
    wtSelected: 'root-ecom',
    wtFilter: 'all',
  };

  const JIRA_KEY_RE = /^[A-Z]+-\d+$/;

  const TRACKER_FILTERS = [
    { key: '', label: 'All' },
    { key: 'overdue', label: 'Overdue' },
    { key: 'due-today', label: 'Due today' },
    { key: 'p0', label: 'P0' },
    { key: 'blocked', label: 'Blocked' },
    { key: 'waiting', label: 'Waiting' },
    { key: 'done', label: 'Done recently' },
  ];

  const KIND_ICON = { delegated: '👤', delegate: '👤', outbound: '📨', followup: '🔁' };
  const COMMITMENT_SOURCE_LABEL = {
    fathom: '🎥 Fathom', 'meeting-local': '🎙 Meeting', slack: '💬 Slack', manual: '✍ Manual',
  };
  const OPEN_STATES = ['todo', 'in_progress', 'blocked', 'waiting'];
  const HEALTH_KIND = { on_track: 'good', at_risk: 'warn', blocked: 'serious', planning: 'muted' };
  const HEALTH_LABEL = { on_track: 'on track', at_risk: 'at risk', blocked: 'blocked', planning: 'planning' };

  const isOpenTicket = t => OPEN_STATES.includes(t.status);
  const lastTs = t => { const c = t.comments || []; return c.length ? (c[c.length - 1].ts_wib || '') : ''; };

  /* consistent "Referensi:" line wrapping Comp.linkChips — renders nothing
     when there are no links (no-chrome-when-empty; linkChips itself already
     returns '' for empty input, this just adds the shared label) */
  function refsLine(links) {
    const chips = Comp.linkChips(links);
    return chips ? `<p><b>Referensi:</b> ${chips}</p>` : '';
  }

  function canRender(container) {
    const a = document.activeElement;
    return !(a && container && container.contains(a) && a.matches('input, textarea, select'));
  }

  function dueBadgeFor(dateStr, today) {
    const fd = U.fmtDue(dateStr, today);
    if (!fd.text) return '';
    if (fd.state === 'overdue') return Comp.badge('serious', fd.text);
    if (fd.state === 'today') return Comp.badge('warn', fd.text);
    return Comp.badge('muted', fd.text);
  }

  function filterTickets(tickets, filter, today) {
    switch (filter) {
      case 'overdue':
        return tickets.filter(t => isOpenTicket(t) && t.due && t.due < today)
          .slice().sort((a, b) => (a.due || '').localeCompare(b.due || ''));
      case 'due-today':
        return tickets.filter(t => isOpenTicket(t) && t.due === today);
      case 'p0':
        return tickets.filter(t => isOpenTicket(t) && t.priority === 'P0');
      case 'blocked':
        return tickets.filter(t => t.status === 'blocked');
      case 'waiting':
        return tickets.filter(t => t.status === 'waiting');
      case 'done':
        return tickets.filter(t => t.status === 'done')
          .slice().sort((a, b) => lastTs(b).localeCompare(lastTs(a))).slice(0, 15);
      default:
        return tickets.filter(isOpenTicket);
    }
  }

  /* ── Tracker ── */

  /* shared row-expand content: note + last 2 comments + ActionBar, plus a
     chaseButton when the ticket is blocked (sourceUrl = its first link, if
     any). Used by flat ticketRow, hierarchy notes rows and the drill view —
     "same pattern as Tracker" everywhere a ticket expands. */
  function ticketExpandBody(t) {
    const comments = (t.comments || []).slice(-2).map(c =>
      `<p><b>${U.esc(c.by || '?')}</b> ${U.esc(c.change || '')}${c.text ? ` — ${U.esc(c.text)}` : ''} <span>· ${U.esc((c.ts_wib || '').slice(0, 16).replace('T', ' '))}</span></p>`
    ).join('');
    const chase = t.status === 'blocked'
      ? `<p>${Comp.chaseButton({ owner: t.owner, what: t.title, sourceUrl: (Array.isArray(t.links) && t.links[0]) || '' })}</p>`
      : '';
    return (t.note ? `<p>${U.esc(t.note)}</p>` : '') + refsLine(t.links) + comments + Comp.actionBar(t) + chase;
  }

  function ticketRow(t, today) {
    const badges = [
      Comp.badge((t.priority || 'p2').toLowerCase(), t.priority || '—'),
      Comp.badge(window.projCat(t.project), t.project || 'Other'),
    ];
    if (t.jira_key) badges.push(Comp.jiraChip(t.jira_key));
    return Comp.listRow({
      key: `t:${t.id}`,
      icon: KIND_ICON[t.kind] || '🎫',
      title: t.title,
      badges,
      meta: t.owner && t.owner !== 'the owner' ? t.owner : '',
      right: dueBadgeFor(t.due, today),
      expandBody: ticketExpandBody(t),
    });
  }

  /* a top-level ticket that HAS children (subtasks, joined by parent_id)
     renders as Comp.taskRow's hierarchy block (row + rollup + indented
     subtasks) with the note/comments/ActionBar expand fused INTO the row
     itself via taskRow's expandBody slot — one row per ticket. */
  function hierarchyTicketRow(t, kids, today) {
    return Comp.taskRow({ ticket: t, children: kids, today, expandBody: ticketExpandBody(t) });
  }

  /* flat {id, name, team} list from the already-fetched /api/portfolio
     payload — no extra fetch needed for the new-ticket initiative select */
  function flattenInitiatives() {
    const teams = (state.portfolio && state.portfolio.teams) || [];
    const out = [];
    teams.forEach(team => (team.initiatives || []).forEach(it =>
      out.push({ id: it.id, name: it.name || it.id, team: team.name })));
    return out;
  }

  function newTicketForm() {
    /* project options = real free-text values seen in tickets.json (what
       round-trips through /api/action), not the 8 categorical slot names */
    const initOptions = `<option value="">No initiative</option>` +
      flattenInitiatives().map(it =>
        `<option value="${U.esc(it.id)}">${U.esc(it.name)}${it.team ? ` (${U.esc(it.team)})` : ''}</option>`).join('');
    /* parent: simple datalist of open ticket ids+titles (searchable-ish) */
    const openTix = ((state.tracker && state.tracker.tickets) || []).filter(isOpenTicket);
    const parentOptions = openTix.map(t =>
      `<option value="${U.esc(t.id)}">${U.esc(t.title)}</option>`).join('');
    return `<div class="action-bar">
      <input type="text" id="work-new-title" placeholder="Ticket title…" />
      <select id="work-new-priority" aria-label="priority">
        <option value="P0">P0</option>
        <option value="P1" selected>P1</option>
        <option value="P2">P2</option>
      </select>
      <select id="work-new-project" aria-label="project">
        <option>Marketplace</option>
        <option>Platform</option>
        <option>B2C Super App</option>
        <option>E-Commerce Solution</option>
        <option>Other</option>
      </select>
      <select id="work-new-initiative" aria-label="initiative">${initOptions}</select>
      <input type="text" id="work-new-jira" placeholder="Jira key (e.g. ABC-123, optional)" />
      <input type="text" id="work-new-parent" list="work-new-parent-list" placeholder="Parent ticket id (optional)" />
      <datalist id="work-new-parent-list">${parentOptions}</datalist>
      <input type="text" id="work-new-note" placeholder="Note (optional)…" />
      <button class="prep-link" data-action="create-ticket">+ Create</button>
    </div>`;
  }

  async function submitNewTicket(panel) {
    const val = id => (panel.querySelector(id) || {}).value || '';
    const title = val('#work-new-title').trim();
    if (!title) { Comp.toast('Title required', false); return; }
    const jiraKey = val('#work-new-jira').trim().toUpperCase();
    if (jiraKey && !JIRA_KEY_RE.test(jiraKey)) {
      Comp.toast(`Invalid Jira key "${jiraKey}" — expected e.g. ABC-123`, false);
      return;
    }
    const initiativeId = val('#work-new-initiative');
    const parentId = val('#work-new-parent').trim();
    const btn = panel.querySelector('[data-action="create-ticket"]');
    if (btn) btn.disabled = true;
    try {
      const payload = {
        title,
        priority: val('#work-new-priority') || 'P1',
        project: val('#work-new-project') || 'Other',
        note: val('#work-new-note'),
      };
      if (initiativeId) payload.initiative_id = initiativeId;
      if (jiraKey) payload.jira_key = jiraKey;
      if (parentId) payload.parent_id = parentId;
      const res = await U.fetchJSON('/api/action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      Comp.toast(`Created ${(res.ticket && res.ticket.id) || title}`, true);
      state.creatingTicket = false;
      render();
      window.dispatchEvent(new CustomEvent('psb:ticket-saved',
        { detail: { id: res.ticket && res.ticket.id, ticket: res.ticket } }));
    } catch (err) {
      Comp.toast(`Create failed: ${err.message}`, false);
      if (btn) btn.disabled = false;
    }
  }

  /* compact chart strip, client-side from the already-fetched tracker
     payload: distBar of OPEN tickets by priority + donut of OPEN tickets by
     project (top 5 + an "Other" rollup — merged into a literal "Other"
     project bucket if one already exists, so the label never doubles up). */
  function trackerChartStrip(allTickets) {
    const open = allTickets.filter(isOpenTicket);
    if (!open.length) return '';

    const prioCounts = { P0: 0, P1: 0, P2: 0 };
    open.forEach(t => {
      const p = String(t.priority || 'P2').toUpperCase();
      if (prioCounts[p] === undefined) prioCounts.P2++; else prioCounts[p]++;
    });
    const prioItems = ['P0', 'P1', 'P2']
      .filter(p => prioCounts[p] > 0)
      .map(p => ({ label: p, value: prioCounts[p], kind: p.toLowerCase() }));
    const distHtml = Comp.distBar(prioItems, { label: 'Open by priority' });

    const byProject = new Map();
    open.forEach(t => {
      const name = t.project || 'Other';
      byProject.set(name, (byProject.get(name) || 0) + 1);
    });
    const sorted = [...byProject.entries()].sort((a, b) => b[1] - a[1]);
    const top5 = sorted.slice(0, 5);
    const restTotal = sorted.slice(5).reduce((sum, [, v]) => sum + v, 0);
    const segByLabel = new Map(top5);
    if (restTotal > 0) segByLabel.set('Other', (segByLabel.get('Other') || 0) + restTotal);
    const donutSegs = [...segByLabel.entries()].map(([name, v]) =>
      ({ label: name, value: v, kind: window.projCat(name) }));
    const donutHtml = Comp.donut(donutSegs, { size: 96, label: 'by project' });

    if (!distHtml && !donutHtml) return '';
    return `<div class="two-col">${distHtml}${donutHtml}</div>`;
  }

  function trackerCard() {
    if (state.trackerError) return `<div class="load-error">Tracker unavailable: ${U.esc(state.trackerError)}</div>`;
    const trk = state.tracker || {};
    const counts = trk.counts || {};
    const today = trk.today || new Date().toISOString().slice(0, 10);
    const allTickets = trk.tickets || [];
    const byParent = {};
    allTickets.forEach(t => { if (t.parent_id) (byParent[t.parent_id] = byParent[t.parent_id] || []).push(t); });
    const filterKey = state.filter || '';
    const activeFilter = TRACKER_FILTERS.some(f => f.key === filterKey) ? filterKey : '';
    /* subtasks (parent_id set) render nested under their parent's taskRow,
       not as their own flat row */
    const rows = filterTickets(allTickets, activeFilter, today).filter(t => !t.parent_id);

    const chips = TRACKER_FILTERS.map(f =>
      `<button class="chip${f.key === activeFilter ? ' is-active' : ''}" data-filter="${U.esc(f.key)}">${U.esc(f.label)}</button>`
    ).join('') + `<button class="prep-link" data-action="toggle-new-ticket">${state.creatingTicket ? '✕ Cancel' : '+ New ticket'}</button>`;

    const rowsHtml = rows.length
      ? `<div class="rows">${rows.map(t => {
          const kids = byParent[t.id];
          return kids && kids.length ? hierarchyTicketRow(t, kids, today) : ticketRow(t, today);
        }).join('')}</div>`
      : Comp.emptyState({ icon: '✨', title: 'Nothing here', hint: 'No tickets match this filter.' });

    const status = (counts.overdue || 0) > 0 ? 'serious' : ((counts.blocked || 0) > 0 ? 'warn' : null);
    return Comp.card({
      key: 'tracker', icon: '🎫', title: 'Tracker',
      count: `${counts.open ?? 0} open · ${counts.overdue ?? 0} overdue · ${counts.blocked ?? 0} blocked`,
      status,
      body: `${trackerChartStrip(allTickets)}<div class="chips">${chips}</div>${state.creatingTicket ? newTicketForm() : ''}${rowsHtml}`,
      open: true,
    });
  }

  /* ── Portfolio ── */

  /* initiative row: opens the drill in a Drawer.openWide slide-over so the
     Portfolio grid stays visible behind it (progressive disclosure, not a
     page jump) — same icon/title/badges/meta shape as Comp.listRow's inner
     markup, button-wrapped so the whole row is clickable. The full-page
     #work/init/<id> route is still reachable via the slide-over's own
     "⛶ Open full page" link (see drillBodyHtml/openInitiativeSlideOver
     below) for deep links (Today tiles etc). Full name (no truncation):
     2-line clamp on .row-title is CSS's job. */
  function initiativeLinkRow(it) {
    const tc = it.ticket_counts || { open: 0, blocked: 0, total: 0 };
    const kind = HEALTH_KIND[it.health] || 'muted';
    const label = HEALTH_LABEL[it.health] || it.health || '—';
    const title = it.name || it.id;
    /* compact glyph form (same convention as card counts) — the long
       "N blockers · o/t tickets" phrasing ellipsized inside 300px team cards */
    const meta = `${it.blocker_count || 0} ⛔ · ${tc.open || 0}/${tc.total || 0} open`;
    return `<button type="button" class="row" data-key="init:${U.esc(it.id)}"
        data-init-id="${U.esc(it.id)}" data-init-name="${U.esc(title)}">
      <span class="row-icon">${U.esc(it.status === 'planning' ? '🗓' : '▸')}</span>
      <span class="row-title" title="${U.esc(title)}">${U.esc(title)}</span>
      <span class="row-badges">${Comp.badge(kind, label)}</span>
      <span class="row-meta" title="${U.esc(meta)}">${U.esc(meta)}</span>
    </button>`;
  }

  function teamCard(team) {
    const inits = team.initiatives || [];
    const sc = team.summary_counts || {};
    let totalTickets = 0, openTickets = 0;
    const rows = inits.map(it => {
      const tc = it.ticket_counts || { open: 0, blocked: 0, total: 0 };
      totalTickets += tc.total || 0;
      openTickets += tc.open || 0;
      return initiativeLinkRow(it);
    });
    const done = Math.max(0, totalTickets - openTickets);
    const pct = totalTickets ? Math.round((done / totalTickets) * 100) : 0;
    const healthKind = HEALTH_KIND[team.health] || 'muted';
    const healthLabel = HEALTH_LABEL[team.health] || team.health || '—';
    const body = `<p>${Comp.badge(healthKind, healthLabel)}</p>
      <p>${U.esc(sc.active ?? 0)}/${U.esc(sc.total ?? 0)} initiatives active · ${U.esc(sc.blockers ?? 0)} blockers · ${U.esc(sc.tickets_open ?? 0)} tickets open</p>
      <p>${Comp.progress({ pct, label: `${done}/${totalTickets} tickets done` })}</p>
      <div class="rows">${rows.join('') || ''}</div>`;
    return Comp.card({
      key: `team:${team.id}`, icon: '🧩', title: team.name || team.id,
      count: `${sc.active ?? 0}/${sc.total ?? 0} active`, body, open: false,
    });
  }

  function portfolioCard() {
    if (state.portfolioError) return `<div class="load-error">Portfolio unavailable: ${U.esc(state.portfolioError)}</div>`;
    const teams = (state.portfolio && state.portfolio.teams) || [];
    const atRisk = teams.filter(t => t.health === 'at_risk' || t.health === 'blocked').length;
    const body = teams.length
      ? `<div class="grid-cards">${teams.map(teamCard).join('')}</div>`
      : Comp.emptyState({ icon: '🗂', title: 'No portfolio data yet', hint: (state.portfolio && state.portfolio.note) || '' });
    const status = teams.some(t => t.health === 'blocked') ? 'serious'
      : teams.some(t => t.health === 'at_risk') ? 'warn' : (teams.length ? 'good' : null);
    return Comp.card({
      key: 'portfolio', icon: '🗂', title: 'Portfolio',
      count: `${teams.length} teams · ${atRisk} at risk`, status, body, open: false,
    });
  }

  /* ══ Work Tree (domain-first hierarchy, /api/work-tree) ══════════════
     Structure validated 30 Jul 2026: Domain > World > Client (optional) >
     Drop/Initiative > Item > Thread. Depth is deliberately NOT fixed, so
     everything here recurses on `children` and never assumes a level count.
     Left = persistent outline, right = detail for the selected node. Every
     fact carries its provenance: `refs` auto-link by prefix, `sources` are
     explicit meeting/doc/Slack links. */

  const WT_STATUS_KIND = { critical: 'serious', risk: 'warn', ok: 'good', plan: 'muted' };
  const WT_STATUS_LABEL = { critical: 'blocked', risk: 'at risk', ok: 'on track', plan: 'planning' };
  const WT_FILTERS = [
    { key: 'all', label: 'All' },
    { key: 'owner', label: 'Needs the owner' },
    { key: 'blocked', label: 'Blocked' },
    { key: 'moved', label: 'Moved' },
  ];

  /* ledger IDs have no URL — they deep-link into quickfind instead */
  const WT_LEDGER_RE = /^(WAIT|COM|DEC|CR)-\d+$/;
  function wtRefUrl(ref) {
    if (WT_LEDGER_RE.test(ref)) return null;
    if (/^(MP|MPS)-\d+$/.test(ref)) return `https://yourcompany.atlassian.net/browse/${ref}`;
    if (/^(MSP|MBA|STOR)-\d+$/.test(ref)) return `https://examplevendor.atlassian.net/browse/${ref}`;
    return null;
  }

  function wtRefChips(refs) {
    if (!refs || !refs.length) return '';
    return refs.map(r => {
      const url = wtRefUrl(r);
      if (url) return `<a class="wt-ref" href="${U.esc(url)}" target="_blank" rel="noopener">${U.esc(r)}</a>`;
      return `<button type="button" class="wt-ref wt-ref-ledger" data-wt-find="${U.esc(r)}" title="Find ${U.esc(r)}">${U.esc(r)}</button>`;
    }).join(' ');
  }

  function wtSourceChips(sources) {
    if (!sources || !sources.length) return '';
    const icon = { fathom: '🎥', slack: '💬', doc: '📄', jira: '🎫', mom: '📝' };
    return `<div class="wt-sources"><span class="wt-sources-label">Source</span>${sources.map(s =>
      `<a class="wt-src" href="${U.esc(s.url)}" target="_blank" rel="noopener">${icon[s.kind] || '🔗'} ${U.esc(s.label)}</a>`
    ).join('')}</div>`;
  }

  function wtIndex() {
    const nodes = {}, parent = {};
    (function walk(list, p) {
      (list || []).forEach(n => {
        nodes[n.id] = n; parent[n.id] = p;
        walk(n.children, n.id);
      });
    })((state.workTree && state.workTree.roots) || [], null);
    return { nodes, parent };
  }

  function wtMatch(n) {
    const f = state.wtFilter;
    if (f === 'all') return true;
    if (f === 'owner') return !!n.owner;
    if (f === 'blocked') return n.status === 'critical';
    if (f === 'moved') return !!n.moved;
    return true;
  }
  function wtVisible(n) {
    if (state.wtFilter === 'all') return true;
    if (n.kind === 'thread') return wtMatch(n);
    return (n.children || []).some(wtVisible);
  }

  function wtTreeRows(list, depth) {
    return (list || []).filter(wtVisible).map(n => {
      const kids = (n.children || []).filter(wtVisible);
      const isOpen = state.wtOpen.has(n.id);
      const r = n.roll || { threads: 0, attn: 0 };
      const badge = n.kind === 'thread'
        ? (n.owner ? '<span class="wt-attn">B</span>' : '')
        : (r.attn ? `<span class="wt-attn">${r.attn}</span>`
          : (r.threads ? `<span class="wt-cnt">${r.threads}</span>` : ''));
      return `<div class="wt-node">
        <button type="button" class="wt-row${state.wtSelected === n.id ? ' is-sel' : ''}"
            data-wt-go="${U.esc(n.id)}" style="padding-left:${8 + depth * 13}px">
          <span class="wt-twist${kids.length ? (isOpen ? ' is-open' : '') : ' is-leaf'}"
                data-wt-tw="${U.esc(n.id)}">▶</span>
          <span class="wt-dot wt-${U.esc(n.status || 'plan')}"></span>
          <span class="wt-label">${U.esc(n.label)}</span>
          ${badge}
        </button>
        ${kids.length && isOpen ? `<div class="wt-kids">${wtTreeRows(kids, depth + 1)}</div>` : ''}
      </div>`;
    }).join('');
  }

  function wtCrumbs(id, parent, nodes) {
    const chain = []; let c = id;
    while (c) { chain.unshift(c); c = parent[c]; }
    return chain.map((cid, i) => i === chain.length - 1
      ? `<span class="wt-crumb-here">${U.esc(nodes[cid].label)}</span>`
      : `<button type="button" class="wt-crumb" data-wt-go="${U.esc(cid)}">${U.esc(nodes[cid].label)}</button><span class="wt-crumb-sep">/</span>`
    ).join(' ');
  }

  function wtThreadRow(t) {
    const badges = [
      Comp.badge(WT_STATUS_KIND[t.status] || 'muted', WT_STATUS_LABEL[t.status] || t.status),
      t.owner ? '<span class="wt-badge-b">Needs the owner</span>' : '',
      t.moved ? '<span class="wt-moved">↑ moved</span>' : '',
    ].filter(Boolean).join(' ');
    return `<tr class="wt-trow">
      <td><div class="wt-th-name"><b>${U.esc(t.label)}</b><span class="wt-th-tags">${badges}${wtRefChips(t.refs)}</span></div></td>
      <td>${U.esc(t.problem || '')}${t.branch ? `<div class="wt-branch">↳ ${U.esc(t.branch)}</div>` : ''}</td>
      <td>${U.esc(t.progress || '')}</td>
      <td class="${t.blocker ? 'wt-blocked' : 'wt-clear'}">${t.blocker ? U.esc(t.blocker) : 'None'}</td>
      <td>${U.esc(t.next || '')}${wtSourceChips(t.sources)}</td>
    </tr>`;
  }

  function wtKidCards(kids) {
    return `<div class="wt-kids-grid">${kids.map(k => {
      const r = k.roll || { threads: 0, attn: 0 };
      return `<button type="button" class="wt-kid" data-wt-go="${U.esc(k.id)}">
        <span class="wt-kid-top">${Comp.badge(WT_STATUS_KIND[k.status] || 'muted', WT_STATUS_LABEL[k.status] || k.status)}
          <span class="wt-kind">${U.esc(k.kind)}</span></span>
        <span class="wt-kid-name">${U.esc(k.label)}</span>
        ${k.summary ? `<span class="wt-kid-sum">${U.esc(k.summary)}</span>` : ''}
        <span class="wt-kid-foot">${r.threads ? `${r.threads} threads` : 'no threads'}${r.attn ? ` · <b>${r.attn} need attention</b>` : ''}</span>
      </button>`;
    }).join('')}</div>`;
  }

  function wtDetail(nodes, parent) {
    const n = nodes[state.wtSelected] || nodes['root-ecom'];
    if (!n) return '';
    const kids = (n.children || []).filter(wtVisible);
    const threads = kids.filter(k => k.kind === 'thread');
    const groups = kids.filter(k => k.kind !== 'thread');
    let body = '';

    if (n.kind === 'thread') {
      body = `<div class="wt-fields">
        <div class="wt-field"><span class="wt-flabel wt-f-problem">Problem</span><span>${U.esc(n.problem || '')}</span></div>
        <div class="wt-field"><span class="wt-flabel wt-f-blocker">Blocker</span><span class="${n.blocker ? 'wt-blocked' : 'wt-clear'}">${n.blocker ? U.esc(n.blocker) : 'None'}</span></div>
        <div class="wt-field"><span class="wt-flabel wt-f-progress">Progress this week</span><span>${U.esc(n.progress || '')}</span></div>
        <div class="wt-field"><span class="wt-flabel wt-f-next">Next</span><span>${U.esc(n.next || '')}</span></div>
      </div>${n.branch ? `<div class="wt-branch">↳ ${U.esc(n.branch)}</div>` : ''}`;
    } else {
      const parts = [];
      if (groups.length) parts.push(`<div class="wt-sec-label">Inside this ${U.esc(n.kind)} · ${groups.length}</div>${wtKidCards(groups)}`);
      if (threads.length) parts.push(`<div class="wt-sec-label">Threads · ${threads.length}</div>
        <div class="wt-tablewrap"><table class="wt-table">
          <thead><tr><th>Thread</th><th>Problem</th><th>Progress this week</th><th>Blocker</th><th>Next</th></tr></thead>
          <tbody>${threads.map(wtThreadRow).join('')}</tbody></table></div>`);
      if (!parts.length) parts.push(`<p class="wt-empty">Nothing tracked below this node${state.wtFilter !== 'all' ? ' for the current filter' : ''}.</p>`);
      body = parts.join('');
    }

    return `<div class="wt-detail">
      <div class="wt-crumbs">${wtCrumbs(n.id, parent, nodes)}</div>
      <h4 class="wt-title">${U.esc(n.label)}</h4>
      <div class="wt-tags">
        ${Comp.badge(WT_STATUS_KIND[n.status] || 'muted', WT_STATUS_LABEL[n.status] || n.status)}
        <span class="wt-kind">${U.esc(n.kind)}</span>
        ${n.owner ? '<span class="wt-badge-b">Needs the owner</span>' : ''}
        ${n.moved ? '<span class="wt-moved">↑ moved this week</span>' : ''}
        ${n.updated_wib ? `<span class="wt-moved">✎ ${U.esc(n.updated_wib.slice(0, 10))}</span>` : ''}
        ${wtRefChips(n.refs)}
      </div>
      ${n.summary ? `<p class="wt-sum">${U.esc(n.summary)}</p>` : ''}
      ${wtSourceChips(n.sources)}
      ${body}
    </div>`;
  }

  /* Freshness of the tree as a whole. work_tree.json has exactly one writer
     (evening-update step 5c, narrow) and one full regenerator (/weekly-report),
     so it CAN silently freeze: it sat at 30 Jul for five days while SAIB shipped
     a BRD revision, and the tab read as live the whole time. `refreshed_wib` is
     the nightly narrow pass; `generated_wib` is the last full rebuild. Show the
     newer of the two, and warn once it stops being today's. */
  function wtFreshness(tree) {
    const stamp = (tree && (tree.refreshed_wib || tree.generated_wib)) || null;
    if (!stamp) return { text: 'no refresh stamp', days: 99, stale: true };
    const t = Date.parse(stamp);
    if (Number.isNaN(t)) return { text: `as of ${stamp}`, days: 99, stale: true };
    /* compare WIB calendar days, not elapsed hours: a 21:30 write and a 09:00
       read are the same working day and must not read as "1d old" */
    const wibDay = ms => Math.floor((ms + 7 * 3600000) / 86400000);
    const days = wibDay(Date.now()) - wibDay(t);
    const when = stamp.slice(0, 10);
    if (days <= 0) return { text: 'refreshed today', days: 0, stale: false };
    if (days === 1) return { text: `as of yesterday, ${when}`, days, stale: false };
    return { text: `as of ${when}, ${days}d old`, days, stale: true };
  }

  function workTreeCard() {
    if (state.workTreeError) return `<div class="load-error">Work tree unavailable: ${U.esc(state.workTreeError)}</div>`;
    const roots = (state.workTree && state.workTree.roots) || [];
    if (!roots.length) {
      return Comp.card({
        key: 'work-tree', icon: '🌳', title: 'Work Tree',
        body: Comp.emptyState({ icon: '🌳', title: 'No work tree yet', hint: (state.workTree && state.workTree.note) || 'Expected journal/state/work_tree.json' }),
        open: false,
      });
    }
    const { nodes, parent } = wtIndex();
    const totals = roots.reduce((a, r) => {
      const q = r.roll || {};
      a.threads += q.threads || 0; a.attn += q.attn || 0; return a;
    }, { threads: 0, attn: 0 });

    const chips = WT_FILTERS.map(f =>
      `<button type="button" class="chip${state.wtFilter === f.key ? ' is-active' : ''}" data-wt-filter="${f.key}">${U.esc(f.label)}</button>`
    ).join('');

    const body = `<div class="wt-toolbar">${chips}
        <span class="wt-toolbar-sp"></span>
        <button type="button" class="chip" data-wt-expand="1">Expand all</button>
        <button type="button" class="chip" data-wt-collapse="1">Collapse</button>
      </div>
      <div class="wt-split">
        <div class="wt-tree">${wtTreeRows(roots, 0)}</div>
        ${wtDetail(nodes, parent)}
      </div>`;

    const fresh = wtFreshness(state.workTree);
    return Comp.card({
      key: 'work-tree', icon: '🌳', title: 'Work Tree',
      count: `${totals.threads} threads · ${totals.attn} need attention · ${fresh.text}`,
      status: fresh.stale ? 'warn' : (totals.attn ? 'warn' : 'good'),
      body, open: true,
    });
  }

  /* Surgical work-tree update — NEVER call render() for a work-tree click.
     render() replaces #tab-work's innerHTML, so `.wt-tree` (max-height
     560px; overflow-y:auto) is rebuilt from scratch at scrollTop 0. Only
     the outline, the detail pane and the filter chips can change here; the
     card header (count/status) comes from roots[].roll, which no click
     mutates. Keeping the `.wt-tree` ELEMENT alive lets us restore its
     scrollTop — swapping innerHTML collapses its content height, so the
     browser clamps to 0 regardless. Returns false (caller falls back to
     render()) when the card isn't on screen: first paint, load error,
     empty tree.
     `openOnly` marks clicks that touch state.wtOpen but not wtSelected
     (twist, expand-all, collapse); wtDetail() never reads wtOpen, so the
     detail pane is left alone and .wt-tablewrap keeps its scrollLeft. */
  function wtRerender(openOnly) {
    const panel = document.getElementById('tab-work');
    const treeEl = panel && panel.querySelector('.wt-tree');
    const detailEl = panel && panel.querySelector('.wt-detail');
    if (!treeEl || !detailEl) return false;
    const roots = (state.workTree && state.workTree.roots) || [];
    if (!roots.length) return false;

    const { nodes, parent } = wtIndex();
    const detailHtml = openOnly ? '' : wtDetail(nodes, parent);
    if (!openOnly && !detailHtml) return false;

    const treeTop = treeEl.scrollTop;
    treeEl.innerHTML = wtTreeRows(roots, 0);
    treeEl.scrollTop = treeTop;
    if (detailHtml) detailEl.outerHTML = detailHtml;
    panel.querySelectorAll('[data-wt-filter]').forEach(c =>
      c.classList.toggle('is-active', c.dataset.wtFilter === state.wtFilter));

    /* Page scroll is deliberately NOT restored: a thread's detail pane is
       shorter than a domain's table, and once the document shrinks the
       browser has already clamped window.scrollY to the new maximum. That
       position no longer exists, so scrolling back to it is impossible
       rather than merely unimplemented. */
    return true;
  }

  /* ── Decisions ── */
  function contextFields(it) {
    const f = [];
    if (it.decider) f.push(`<p><b>Decider:</b> ${U.esc(it.decider)}</p>`);
    if (it.project) f.push(`<p><b>Project:</b> ${U.esc(it.project)}</p>`);
    if (Array.isArray(it.stakeholder_slugs) && it.stakeholder_slugs.length)
      f.push(`<p><b>Stakeholders:</b> ${U.esc(it.stakeholder_slugs.join(', '))}</p>`);
    if (it.notes) f.push(`<p>${U.esc(it.notes)}</p>`);
    if (Array.isArray(it.sources) && it.sources.length) {
      const linked = it.sources.filter(s => s && s.url).map(s => ({ url: s.url, label: s.label || s.type }));
      f.push(refsLine(linked));
      const unlinked = it.sources.filter(s => s && !s.url).map(s => U.esc(s.label || s.type || '')).filter(Boolean);
      if (unlinked.length) f.push(`<p><b>Sources:</b> ${unlinked.join(', ')}</p>`);
    }
    return f.join('');
  }

  function decisionRow(it, today) {
    return Comp.listRow({
      key: `dec:${it.id}`, icon: '🧭', title: it.title,
      meta: it.decider || '', right: dueBadgeFor(it.deadline, today),
      expandBody: contextFields(it),
    });
  }

  function decidedRow(it) {
    const when = it.decided_at ? new Date(it.decided_at * 1000).toISOString().slice(0, 10) : '';
    return Comp.listRow({
      key: `dec:${it.id}`, icon: it.status === 'superseded' ? '↪' : '✓', title: it.title,
      badges: [Comp.badge(it.status === 'superseded' ? 'muted' : 'good', it.status)],
      meta: when, expandBody: it.decision ? `<p>${U.esc(it.decision)}</p>` : contextFields(it),
    });
  }

  function decisionsCard() {
    if (state.decisionsError) return `<div class="load-error">Decisions unavailable: ${U.esc(state.decisionsError)}</div>`;
    const dec = state.decisions || {};
    const counts = dec.counts || {};
    const today = dec.today || '';
    const items = dec.items || [];
    const open = items.filter(it => it.status === 'open');
    const other = items.filter(it => it.status !== 'open');
    const openRows = open.length
      ? `<div class="rows">${open.map(it => decisionRow(it, today)).join('')}</div>`
      : Comp.emptyState({ icon: '🧭', title: 'No open decisions' });
    const nested = Comp.card({
      key: 'decisions-recent', icon: '✓', title: 'Recently decided', count: `${other.length}`,
      body: other.length
        ? `<div class="rows">${other.slice(0, 10).map(decidedRow).join('')}</div>`
        : Comp.emptyState({ icon: '✓', title: 'Nothing decided yet' }),
      open: false,
    });
    const status = (counts.overdue || 0) > 0 ? 'serious' : ((counts.open || 0) > 0 ? 'warn' : 'good');
    return Comp.card({
      key: 'decisions', icon: '🧭', title: 'Decisions',
      count: `${counts.open ?? 0} open · ${counts.overdue ?? 0} overdue`,
      status, body: openRows + nested, open: state.filter === 'decisions',
    });
  }

  /* ── Commitments ── */

  /* row action bar: close ("Done") / drop ("not a commitment") both go
     through /api/commitment-close via commitment_ledger.py (single writer);
     The AI button is just Comp.aiButton (wiring lives in components.js) */
  function commitmentActionBar(it) {
    return `<div class="action-bar" data-commitment-id="${U.esc(it.id)}">
      <button class="prep-link" data-action="commitment-close" data-id="${U.esc(it.id)}">✓ Done</button>
      <button class="prep-link" data-action="commitment-drop" data-id="${U.esc(it.id)}">✕ Not a commitment</button>
      ${Comp.aiButton({ kind: 'commitment', ref: it.id, label: '🤖 AI, work on it' })}
    </div>`;
  }

  function commitmentExpandBody(it) {
    const parts = [`<p>${U.esc(it.text || it.id)}</p>`];
    if (it.project) parts.push(`<p><b>Project:</b> ${U.esc(it.project)}</p>`);
    const link = it.permalink || (it.source && it.source.ref);
    if (link) parts.push(refsLine([{ url: link, label: it.source && it.source.type }]));
    if (it.confidence) parts.push(`<p><b>Confidence:</b> ${U.esc(it.confidence)}</p>`);
    if (Array.isArray(it.notes) && it.notes.length)
      parts.push(`<p>${it.notes.map(n => U.esc(typeof n === 'string' ? n : JSON.stringify(n))).join('<br>')}</p>`);
    parts.push(commitmentActionBar(it));
    return parts.join('');
  }

  /* the "gak jelas ... share ke siapa?" fix: recipient is either "to <name>"
     or an explicit muted "→ siapa? cek source" (never a silent blank) */
  function commitmentRow(it, today) {
    const badges = [Comp.badge('muted', COMMITMENT_SOURCE_LABEL[(it.source && it.source.type) || ''] || (it.source && it.source.type) || 'unknown')];
    const toTxt = it.to ? `to ${it.to}` : '→ siapa? cek source';
    const ageTxt = it.first_seen ? U.fmtAge((Date.now() / 1000 - it.first_seen) / 3600) : '';
    const meta = [toTxt, ageTxt].filter(Boolean).join(' · ');
    const right = `${it.ticket_id ? Comp.ticketChip(it.ticket_id) : ''}${dueBadgeFor(it.due, today)}`;
    return Comp.listRow({
      key: `com:${it.id}`, icon: '🤝', title: it.text || it.id,
      badges, meta, right,
      expandBody: commitmentExpandBody(it),
    });
  }

  function closedCommitmentRow(it) {
    const when = it.closed_at ? new Date(it.closed_at * 1000).toISOString().slice(0, 10) : '';
    const badges = [Comp.badge('good', 'done')];
    if (it.ticket_id) badges.push(Comp.ticketChip(it.ticket_id));
    return Comp.listRow({
      key: `com:${it.id}`, icon: '✓', title: it.text || it.id,
      badges, meta: when,
    });
  }

  const isOverdueCommitment = (it, today) =>
    !!(it.due && today && U.fmtDue(it.due, today).state === 'overdue');

  /* "never closed" fix: keep collapsed 'Overdue' front-and-center ahead of
     the rest of Open, so a stale-but-open item can't hide inside a flat list */
  function commitmentGroupsHtml(openItems, today) {
    const overdue = openItems.filter(it => isOverdueCommitment(it, today));
    const rest = openItems.filter(it => !isOverdueCommitment(it, today));
    if (!overdue.length && !rest.length) return Comp.emptyState({ icon: '🤝', title: 'No open commitments' });
    const overdueHtml = overdue.length
      ? `<div class="section-label">⏰ Overdue (${overdue.length})</div><div class="rows">${overdue.map(it => commitmentRow(it, today)).join('')}</div>`
      : '';
    const openHtml = rest.length
      ? `<div class="section-label">Open (${rest.length})</div><div class="rows">${rest.map(it => commitmentRow(it, today)).join('')}</div>`
      : '';
    return overdueHtml + openHtml;
  }

  /* "is the whole ledger valid/stale?" fix: header line surfacing the last
     verify-commitments run (from /api/ai-task?list=1) — never-ran state is
     explicit too, never a silent absence */
  function verifyRunLine() {
    const runs = (state.aiRuns && state.aiRuns.runs) || [];
    const run = runs.find(r => r.kind === 'verify-commitments');
    if (!run) return 'never verified';
    const ts = run.finished_wib || run.started_wib;
    const ageH = ts ? (Date.now() - Date.parse(ts)) / 3600000 : null;
    const ageTxt = (ageH !== null && Number.isFinite(ageH)) ? U.fmtAge(ageH) : '?';
    return `terakhir diverifikasi ${U.esc(ageTxt)} lalu · ${Comp.aiResultPill({ run })}`;
  }

  function commitmentsCard() {
    if (state.commitmentsError) return `<div class="load-error">Commitments unavailable: ${U.esc(state.commitmentsError)}</div>`;
    const com = state.commitments || {};
    const counts = com.counts || {};
    const today = com.today || '';
    const items = com.items || [];
    const open = items.filter(it => it.status === 'open');
    const closed = items.filter(it => it.status !== 'open');
    const headerControls = `<p>${Comp.aiButton({ kind: 'verify-commitments', ref: 'all', label: '🔍 AI verify all' })}</p>
      <p class="row-subtext">${verifyRunLine()}</p>`;
    const nested = Comp.card({
      key: 'commitments-recent', icon: '✓', title: 'Recently closed', count: `${closed.length}`,
      body: closed.length
        ? `<div class="rows">${closed.slice(0, 10).map(closedCommitmentRow).join('')}</div>`
        : Comp.emptyState({ icon: '✓', title: 'None closed yet' }),
      open: false,
    });
    const status = (counts.overdue || 0) > 0 ? 'serious' : ((counts.open || 0) > 0 ? 'warn' : 'good');
    const card = Comp.card({
      key: 'commitments', icon: '🤝', title: 'Commitments',
      count: `${counts.open ?? 0} open · ${counts.overdue ?? 0} overdue`,
      status, body: headerControls + commitmentGroupsHtml(open, today) + nested, open: state.filter === 'commitments',
    });
    const ageH = com.last_sweep ? (Date.now() / 1000 - com.last_sweep) / 3600 : null;
    return (ageH !== null && ageH > 12) ? Comp.staleWrap({ state: 'stale', ageH, inner: card }) : card;
  }

  /* refetch just the Commitments slice (items + verify-run list) and
     re-render — used after a close/drop action and after a
     verify-commitments run finishes (items may have been closed) */
  async function reloadCommitmentsSection() {
    const [comR, aiR] = await Promise.allSettled([
      U.fetchJSON('/api/commitments'),
      U.fetchJSON('/api/ai-task?list=1'),
    ]);
    if (comR.status === 'fulfilled') { state.commitments = comR.value; state.commitmentsError = null; }
    else state.commitmentsError = (comR.reason && comR.reason.message) || 'unknown error';
    if (aiR.status === 'fulfilled') state.aiRuns = aiR.value;
    render();
  }

  async function commitmentAction(id, action) {
    try {
      const payload = { id, action };
      if (action === 'close') payload.note = 'closed from dashboard';
      await U.fetchJSON('/api/commitment-close', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      /* close AND drop are reversible (mis-click safety) — Undo POSTs
         action:'reopen' through the same endpoint (ledger CLI single writer) */
      const undo = {
        label: 'Undo',
        onClick: async () => {
          await U.fetchJSON('/api/commitment-close', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id, action: 'reopen', note: 'undo from dashboard' }),
          });
          Comp.toast(`Reopened: ${id}`, true);
          await reloadCommitmentsSection();
        },
      };
      Comp.toast(action === 'close' ? `Done: ${id}` : `Dropped: ${id}`, true, undo);
      await reloadCommitmentsSection();
    } catch (err) {
      Comp.toast(`Could not ${action === 'close' ? 'close' : 'drop'} ${id}: ${err.message}`, false);
    }
  }

  /* verify-commitments can close/drop items in bulk — reload once it's done */
  window.addEventListener('psb:ai-done', e => {
    const panel = document.getElementById('tab-work');
    if (!panel) return;
    if (e.detail && e.detail.kind === 'verify-commitments') reloadCommitmentsSection();
  });

  /* ── People ── */
  function peopleCard() {
    if (state.stakeholdersError) return `<div class="load-error">People unavailable: ${U.esc(state.stakeholdersError)}</div>`;
    const people = (state.stakeholders && state.stakeholders.people) || [];
    const top3 = people.slice(0, 3).map(p => p.name).filter(Boolean);
    const body = people.length
      ? `<div class="chip-grid">${people.map(p => Comp.personChip({
          slug: p.slug, name: p.name, role: p.role,
          counts: { commitments: p.open_commitments, waiting: p.waiting_on, decisions: p.open_decisions },
          relPath: p.relPath,
        })).join('')}</div>`
      : Comp.emptyState({ icon: '👥', title: 'No stakeholders yet', hint: (state.stakeholders && state.stakeholders.note) || '' });
    return Comp.card({
      key: 'people', icon: '👥', title: 'People',
      count: top3.join(' · '), body, open: state.filter === 'people',
    });
  }

  /* ── Drill (#work/init/<id> full page, AND the Portfolio slide-over) ── */
  function blockerRow(b) {
    const ageH = b.since ? (Date.now() - Date.parse(b.since)) / 3600000 : null;
    const ageTxt = (ageH !== null && Number.isFinite(ageH)) ? `${U.fmtAge(ageH)} old` : '';
    return Comp.listRow({
      key: `blocker:${b.owner || ''}:${(b.what || '').slice(0, 40)}`,
      icon: '⛔',
      title: b.what || '(no description)',
      meta: [b.owner, ageTxt].filter(Boolean).join(' · '),
      right: Comp.chaseButton({ owner: b.owner, what: b.what, sourceUrl: '' }),
    });
  }

  /* shared drill body — headline + Blockers card + Tasks card + unlinked
     hint — used by BOTH the full-page route (#work/init/<id>) and the
     Portfolio slide-over (Drawer.openWide) so the two views can never
     drift. Only the surrounding chrome differs per caller: the full page
     wraps it in a back button + breadcrumb; the slide-over wraps it in a
     "⛶ Open full page" link (see openInitiativeSlideOver below). */
  function drillBodyHtml(d) {
    const init = d.initiative || {};
    const counts = d.counts || {};
    const tickets = d.tickets || [];
    const unlinkedHint = d.unlinked_hint || 0;
    const today = new Date().toISOString().slice(0, 10);

    const healthKind = HEALTH_KIND[init.health] || 'muted';
    const healthLabel = HEALTH_LABEL[init.health] || init.health || '—';
    /* plain text after the badge, matching teamCard's own convention for a
       supplementary counts line — .row-meta's nowrap+32%-max-width is meant
       for flex-row secondary text, not a standalone paragraph */
    const headline = `<p>${Comp.badge(healthKind, healthLabel)}
        ${counts.open ?? 0} open · ${counts.blocked ?? 0} blocked · ${counts.done ?? 0} done · ${counts.total ?? 0} total</p>
      ${init.summary ? `<p>${U.esc(init.summary)}</p>` : ''}`;

    const blockers = init.blockers || [];
    const blockersBody = blockers.length
      ? `<div class="rows">${blockers.map(blockerRow).join('')}</div>`
      : Comp.emptyState({ icon: '✓', title: 'No open blockers' });
    const blockersCard = Comp.card({
      key: 'drill-blockers', icon: '⛔', title: 'Blockers',
      count: `${blockers.length}`, status: blockers.length ? 'serious' : 'good',
      body: blockersBody, open: true,
    });

    const tasksBody = tickets.length
      ? `<div class="rows">${tickets.map(t =>
          hierarchyTicketRow(t, Array.isArray(t.children) ? t.children : [], today)).join('')}</div>`
      : Comp.emptyState({ icon: '🗒', title: 'No tickets linked to this initiative yet' });
    const tasksCard = Comp.card({
      key: 'drill-tasks', icon: '🎫', title: 'Tasks',
      count: `${counts.open ?? 0} open · ${tickets.length} top-level`,
      body: tasksBody, open: true,
    });

    /* .row-note = existing muted full-sentence style (already used for ticket
       notes in app.js) — wraps normally, unlike .row-meta which forces
       nowrap+ellipsis for flex-row secondary text. */
    const footer = unlinkedHint > 0
      ? `<p class="row-note">${unlinkedHint} tickets in this project are not linked to an initiative yet (set initiative_id via edit)</p>`
      : '';

    return `${headline}${blockersCard}${tasksCard}${footer}`;
  }

  function renderDrill() {
    const panel = document.getElementById('tab-work');
    if (!panel || !canRender(panel)) return;

    const back = Comp.backButton({ href: '#work', label: 'Kembali ke Portfolio' });

    if (state.initiativeDetailError) {
      const msg = state.initiativeDetailError.message || 'unknown error';
      const is404 = /\b404\b/.test(msg);
      panel.innerHTML = `<div class="stack">
        ${back}
        ${Comp.breadcrumb([{ label: 'Portfolio', href: '#work' }, { label: is404 ? 'Not found' : 'Error' }])}
        ${is404
          ? Comp.emptyState({
              icon: '🔍',
              title: `Initiative "${state.initiativeId || ''}" not found`,
              hint: 'It may have been renamed or removed from portfolio.json. Use the breadcrumb above to go back.',
            })
          : `<div class="load-error">Initiative unavailable: ${U.esc(msg)}</div>`}
      </div>`;
      return;
    }

    const d = state.initiativeDetail || {};
    const init = d.initiative || {};

    /* every crumb except the last is a real link — team crumb goes to the
       Portfolio section too (no per-team filter route exists yet) */
    const crumb = Comp.breadcrumb([
      { label: 'Portfolio', href: '#work' },
      { label: init.team || '', href: '#work' },
      { label: init.name || init.id || state.initiativeId },
    ]);

    panel.innerHTML = `<div class="stack">
      ${back}
      ${crumb}
      ${drillBodyHtml(d)}
    </div>`;
  }

  async function loadDrill(id) {
    const panel = document.getElementById('tab-work');
    if (!panel || !canRender(panel)) return;
    state.initiativeId = id;
    panel.innerHTML = `<div class="skeleton"><div class="skeleton-line"></div><div class="skeleton-line w-80"></div><div class="skeleton-line w-60"></div></div>`;
    try {
      state.initiativeDetail = await U.fetchJSON(`/api/initiative/${encodeURIComponent(id)}`);
      state.initiativeDetailError = null;
    } catch (err) {
      state.initiativeDetail = null;
      state.initiativeDetailError = err;
    }
    renderDrill();
  }

  /* Portfolio initiative rows open THIS instead of navigating away —
     background Portfolio stays visible (progressive disclosure, not a page
     jump). Same drillBodyHtml() as the full page, so the two views can
     never drift. An "⛶ Open full page" link up top still reaches the
     full #work/init/<id> route for deep links (Today tiles etc). `seq`
     guards against a slower earlier fetch overwriting a newer click. */
  let slideOverSeq = 0;
  async function openInitiativeSlideOver(id, name) {
    const seq = ++slideOverSeq;
    const skeleton = `<div class="skeleton"><div class="skeleton-line"></div><div class="skeleton-line w-80"></div><div class="skeleton-line w-60"></div></div>`;
    Drawer.openWide(name || id, skeleton);
    const fullPageLink = `<p><a class="prep-link" data-drawer-nav href="#work/init/${encodeURIComponent(id)}">⛶ Open full page</a></p>`;
    try {
      const d = await U.fetchJSON(`/api/initiative/${encodeURIComponent(id)}`);
      if (seq !== slideOverSeq) return;   // superseded by a newer click
      const title = name || (d.initiative && d.initiative.name) || id;
      Drawer.openWide(title, fullPageLink + drillBodyHtml(d));
    } catch (err) {
      if (seq !== slideOverSeq) return;
      Drawer.openWide(name || id, fullPageLink + `<div class="load-error">Initiative unavailable: ${U.esc(err.message)}</div>`);
    }
  }

  /* "⛶ Open full page" is a real <a href="#work/init/…"> — let it
     navigate natively, just close the slide-over as a courtesy so the full
     page isn't left rendering underneath an open drawer showing the same
     content twice. */
  document.addEventListener('click', e => {
    if (e.target.closest('[data-drawer-nav]')) Drawer.close();
  });

  /* ── render / load ── */
  function render() {
    const panel = document.getElementById('tab-work');
    if (!panel || !canRender(panel)) return;
    /* full teardown: keep the work-tree outline where the user left it, so
       the 60s refresh loop doesn't yank it to the top mid-read */
    const prevTree = panel.querySelector('.wt-tree');
    const treeTop = prevTree ? prevTree.scrollTop : 0;
    panel.innerHTML = `<div class="stack">
      ${trackerCard()}
      ${workTreeCard()}
      ${portfolioCard()}
      ${decisionsCard()}
      ${commitmentsCard()}
      ${peopleCard()}
    </div>`;
    if (treeTop) {
      const tree = panel.querySelector('.wt-tree');
      if (tree) tree.scrollTop = treeTop;
    }
  }

  async function load(filter) {
    const panel = document.getElementById('tab-work');
    if (!panel || !canRender(panel)) return;
    state.filter = filter || null;

    if (filter && filter.startsWith('init/')) {
      const id = decodeURIComponent(filter.slice('init/'.length));
      await loadDrill(id);
      return;
    }

    const [trkR, portR, decR, comR, ppR, aiR, wtR] = await Promise.allSettled([
      U.fetchJSON('/api/tracker'),
      U.fetchJSON('/api/portfolio'),
      U.fetchJSON('/api/decisions'),
      U.fetchJSON('/api/commitments'),
      U.fetchJSON('/api/stakeholders'),
      U.fetchJSON('/api/ai-task?list=1'),
      U.fetchJSON('/api/work-tree'),
    ]);

    state.tracker = trkR.status === 'fulfilled' ? trkR.value : null;
    state.trackerError = trkR.status === 'rejected' ? (trkR.reason && trkR.reason.message) || 'unknown error' : null;
    state.portfolio = portR.status === 'fulfilled' ? portR.value : null;
    state.portfolioError = portR.status === 'rejected' ? (portR.reason && portR.reason.message) || 'unknown error' : null;
    state.decisions = decR.status === 'fulfilled' ? decR.value : null;
    state.decisionsError = decR.status === 'rejected' ? (decR.reason && decR.reason.message) || 'unknown error' : null;
    state.commitments = comR.status === 'fulfilled' ? comR.value : null;
    state.commitmentsError = comR.status === 'rejected' ? (comR.reason && comR.reason.message) || 'unknown error' : null;
    state.stakeholders = ppR.status === 'fulfilled' ? ppR.value : null;
    state.stakeholdersError = ppR.status === 'rejected' ? (ppR.reason && ppR.reason.message) || 'unknown error' : null;
    state.aiRuns = aiR.status === 'fulfilled' ? aiR.value : state.aiRuns;
    state.aiRunsError = aiR.status === 'rejected' ? (aiR.reason && aiR.reason.message) || 'unknown error' : null;
    state.workTree = wtR.status === 'fulfilled' ? wtR.value : null;
    state.workTreeError = wtR.status === 'rejected' ? (wtR.reason && wtR.reason.message) || 'unknown error' : null;

    render();
  }

  /* ── delegated clicks: initiative slide-over + filter chips + new-ticket toggle/create ── */
  document.addEventListener('click', e => {
    const panel = document.getElementById('tab-work');
    if (!panel || !panel.contains(e.target)) return;

    /* work-tree: twist toggles open/closed WITHOUT changing selection, so
       exploring the outline never yanks the detail pane out from under you */
    const wtTw = e.target.closest('[data-wt-tw]');
    if (wtTw) {
      e.stopPropagation();
      const id = wtTw.dataset.wtTw;
      const { nodes } = wtIndex();
      if (nodes[id] && (nodes[id].children || []).length) {
        state.wtOpen.has(id) ? state.wtOpen.delete(id) : state.wtOpen.add(id);
        if (!wtRerender(true)) render();
      }
      return;
    }
    const wtGo = e.target.closest('[data-wt-go]');
    if (wtGo) {
      const id = wtGo.dataset.wtGo;
      const { nodes, parent } = wtIndex();
      if (!nodes[id]) return;
      state.wtSelected = id;
      let p = parent[id];
      while (p) { state.wtOpen.add(p); p = parent[p]; }
      if ((nodes[id].children || []).length) state.wtOpen.add(id);
      if (!wtRerender()) render();
      return;
    }
    const wtFind = e.target.closest('[data-wt-find]');
    if (wtFind) {
      const q = wtFind.dataset.wtFind;
      const input = document.getElementById('quickfind-input');
      if (input) { input.value = q; input.focus(); input.dispatchEvent(new Event('input', { bubbles: true })); }
      return;
    }
    const wtF = e.target.closest('[data-wt-filter]');
    if (wtF) {
      state.wtFilter = wtF.dataset.wtFilter;
      /* a filter swaps the whole row set, so a preserved offset would land on
         an arbitrary slice — go back to the top, as the old full render did */
      if (!wtRerender()) render();
      else { const t = document.querySelector('#tab-work .wt-tree'); if (t) t.scrollTop = 0; }
      return;
    }
    if (e.target.closest('[data-wt-expand]')) {
      const { nodes } = wtIndex();
      Object.values(nodes).forEach(n => { if ((n.children || []).length) state.wtOpen.add(n.id); });
      if (!wtRerender(true)) render(); return;
    }
    if (e.target.closest('[data-wt-collapse]')) {
      state.wtOpen = new Set(((state.workTree && state.workTree.roots) || []).map(r => r.id));
      if (!wtRerender(true)) render(); return;
    }

    const initRow = e.target.closest('[data-init-id]');
    if (initRow) {
      openInitiativeSlideOver(initRow.dataset.initId, initRow.dataset.initName);
      return;
    }

    const chip = e.target.closest('.chip[data-filter]');
    if (chip) {
      const f = chip.dataset.filter;
      location.hash = f ? `#work/${f}` : '#work';
      return;
    }
    const toggleBtn = e.target.closest('[data-action="toggle-new-ticket"]');
    if (toggleBtn) { state.creatingTicket = !state.creatingTicket; render(); return; }

    const createBtn = e.target.closest('[data-action="create-ticket"]');
    if (createBtn) { submitNewTicket(panel); return; }

    const closeBtn = e.target.closest('[data-action="commitment-close"]');
    if (closeBtn) { commitmentAction(closeBtn.dataset.id, 'close'); return; }

    const dropBtn = e.target.closest('[data-action="commitment-drop"]');
    if (dropBtn) { commitmentAction(dropBtn.dataset.id, 'drop'); return; }
  });

  window.Tabs.work = { load };
})();
