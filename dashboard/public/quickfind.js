/* quickfind.js — global ticket / ledger quick-find in the header, and the
   #find/<ID> deep link that backs every clickable ledger ID the owner is handed.

   Two entry points:
     • the header box — type an ID (COM-0284, WAIT-0111, DEC-0003) or free text;
       matches across the three JSON ledgers render as collapsed rows.
     • http://localhost:3737/#find/WAIT-0223 — a permalink to ONE record. The
       exact match renders as an already-open card (nothing to click), so a link
       pasted into a briefing, a report, or a chat reply lands on the definition
       of the item rather than on a search box.

   A bare Jira-style key (ABC-123, MSP-…, STOR-…, MPS-…) also offers a Work Jira
   browse deep-link; COM-/WAIT-/DEC- never do, those are local records.
   Read-only: /api/ledger-find never mutates.
   Shortcuts: `/` or Ctrl/Cmd+K focus the box; Enter searches now; Esc blurs. */
(() => {
  'use strict';

  const input = document.getElementById('quickfind-input');

  let timer = null;
  let lastQuery = '';

  /* map a ledger status string to one of Comp.badge's semantic kinds */
  function statusKind(status) {
    const s = (status || '').toLowerCase();
    if (s === 'breached') return 'critical';
    if (s === 'open' || s === 'pending' || s === '') return 'warn';
    if (s === 'answered' || s === 'closed' || s === 'done' || s === 'decided') return 'good';
    return 'muted';
  }

  const KIND_ICON = { commitments: '✅', waiting_on: '⏳', decisions: '⚖️' };
  const KIND_WORD = {
    commitments: 'Commitment — the owner owes this',
    waiting_on: 'Waiting-on — someone else owes the owner',
    decisions: 'Decision — a call that has to be made',
  };

  /* a source link: repo-relative path -> Drawer opener; http(s) -> new tab */
  function sourceLink(link) {
    if (!link) return '';
    if (/^https?:\/\//i.test(link)) {
      return `<a class="doc-link" href="${U.esc(link)}" target="_blank" rel="noopener">🔗 source</a>`;
    }
    return `<button class="prep-link" data-drawer-path="${U.esc(link)}" ` +
      `data-drawer-title="${U.esc(link.split('/').pop())}">📄 source</button>`;
  }

  /* one label:value line inside the detail card; skipped when value empty */
  function detailLine(label, value, cls) {
    if (!value) return '';
    return `<div class="qf-d-line${cls ? ' ' + cls : ''}">` +
      `<span class="qf-d-key">${U.esc(label)}</span>` +
      `<span class="qf-d-val">${U.esc(value)}</span></div>`;
  }

  /* 120 -> "120h (5d)" — the SLA is the whole reason a waiting-on breaches, so
     it reads in both units rather than as a bare number */
  function slaText(hours) {
    if (typeof hours !== 'number' || !isFinite(hours) || hours <= 0) return '';
    const days = hours / 24;
    const d = days >= 1 ? ` (${Number.isInteger(days) ? days : days.toFixed(1)}d)` : '';
    return `${Number.isInteger(hours) ? hours : hours.toFixed(1)}h${d}`;
  }

  /* the detail body: what the item is, who owns it, how it is defined, and its
     timeline. Shared by the collapsed rows and the deep-linked card. */
  function detailBody(r) {
    const kindLabel = { commitments: "the owner owes", waiting_on: 'Waiting on', decisions: 'Decision' }[r.kind] || '';
    const created = r.created_wib ? `${r.created_wib} (${r.created_ago})` : '';
    let followup = r.followup_wib ? `${r.followup_wib} (${r.followup_ago})` : 'never nudged';
    if (r.nudge_count) followup += ` · ${r.nudge_count}× nudged`;
    const breached = r.breached_wib ? `${r.breached_wib} (${r.breached_ago})` : '';
    const closed = r.closed_wib
      ? `${r.closed_wib} (${r.closed_ago})${r.closed_by ? ' by ' + r.closed_by : ''}` : '';
    const scope = [r.initiative_id, r.portfolio].filter(Boolean).join(' · ');

    /* waiting_on derives both title and context from `what`, so on those records
       the two are the same string — print it once */
    const context = (r.context && r.context.trim() !== (r.title || '').trim()) ? r.context : '';

    const lines = [
      context ? `<div class="qf-d-context">${U.esc(context)}</div>` : '',
      r.decision ? `<div class="qf-d-context"><strong>Decided:</strong> ${U.esc(r.decision)}</div>` : '',
      detailLine(kindLabel || 'PIC', r.owner || '—'),
      detailLine('Status', r.status || 'open'),
      /* Where this sits in the work tree. 'unfiled' is a real state, not a
         missing value, so it prints rather than being hidden: it means the
         record needs a home and is waiting on the owner to say which. */
      detailLine('Work tree', r.node === 'unfiled'
        ? 'unfiled — needs a node'
        : (r.node_path || r.node || '')),
      detailLine('Project', r.project),
      detailLine('Scope', scope),
      detailLine('Opened', created),
      detailLine('Due', r.due),
      detailLine('SLA', slaText(r.sla_hours)),
      detailLine('Escalate to', r.escalate_to),
      detailLine('Last follow-up', followup),
      detailLine('Breached', breached, 'qf-d-breach'),
      detailLine('Closed', closed),
      detailLine('Superseded by', r.superseded_by),
      detailLine('Logged from', [r.source_type, r.confidence ? `${r.confidence} confidence` : '']
        .filter(Boolean).join(' · ')),
    ].filter(Boolean).join('');

    const notes = r.notes && r.notes.length
      ? `<div class="qf-d-notes"><div class="qf-d-key">Notes / timeline</div>` +
        r.notes.map(n => `<div class="qf-d-note">• ${U.esc(n)}</div>`).join('') + `</div>`
      : '';

    const src = r.link ? `<div class="qf-d-src">${sourceLink(r.link)}</div>` : '';

    return `<div class="qf-detail">${lines}${notes}${src}</div>`;
  }

  /* the deep-linked record: header + body, open on arrival, plus the permalink
     to hand back to anyone who needs to point at this item again */
  function heroCard(r) {
    const badges = [Comp.badge(statusKind(r.status), r.status || 'open')];
    if (r.priority) badges.unshift(Comp.badge('p0', 'priority'));
    return `<div class="qf-hero">
      <div class="qf-hero-head">
        <span class="qf-hero-icon">${KIND_ICON[r.kind] || '🎫'}</span>
        <span class="qf-hero-id">${U.esc(r.id)}</span>
        ${badges.join('')}
      </div>
      <div class="qf-hero-kind">${U.esc(KIND_WORD[r.kind] || '')}</div>
      <div class="qf-hero-title">${U.esc(r.title || '(no title)')}</div>
      ${detailBody(r)}
      <div class="qf-hero-perma">
        <a class="doc-link" href="#find/${encodeURIComponent(r.id)}">🔗 link to this record</a>
      </div>
    </div>`;
  }

  function resultRow(r) {
    const badges = [Comp.badge(statusKind(r.status), r.status || 'open')];
    if (r.priority) badges.unshift(Comp.badge('p0', 'priority'));
    // compact meta on the summary line: PIC · due · last follow-up
    const metaBits = [
      r.owner,
      r.due ? `due ${r.due}` : '',
      r.followup_wib ? `nudged ${r.followup_ago}` : '',
    ].filter(Boolean).map(U.esc).join(' · ');
    return Comp.listRow({
      key: `qf:${r.id}`,
      icon: KIND_ICON[r.kind] || '🎫',
      title: `${r.id} — ${r.title || '(no title)'}`,
      badges,
      meta: metaBits,
      expandBody: detailBody(r),
    });
  }

  function renderResults(data, deep) {
    const q = data.query || '';
    const ql = q.toLowerCase();
    const results = Array.isArray(data.results) ? data.results : [];
    const parts = [];

    if (data.jira) {
      parts.push(
        `<div class="qf-jira">Jira: ` +
        `<a class="doc-link" href="${U.esc(data.jira.url)}" target="_blank" rel="noopener">` +
        `↗ open ${U.esc(data.jira.key)} in Work Jira</a></div>`
      );
    }

    /* a deep link is a permalink to one record: show it open, not as a hit list */
    const exact = deep ? results.find(r => (r.id || '').toLowerCase() === ql) : null;
    const rest = exact ? results.filter(r => r !== exact) : results;

    if (exact) parts.push(heroCard(exact));

    if (!results.length) {
      const local = /^(com|wait|dec)-\d+$/i.test(q);
      parts.push(Comp.emptyState({
        icon: '🔍', title: `No ledger item matches "${U.esc(q)}"`,
        hint: local
          ? 'That is a local ledger ID with no record behind it — either the ID is wrong, or this checkout is behind origin. Run: python3 .agent/scripts/ledger_sync.py refresh'
          : (data.jira ? 'Not in the local ledgers — try the Jira link above.'
            : 'Try a full ID (COM-0284) or a keyword (ExampleVendor, OTP).'),
      }));
    } else if (rest.length) {
      const label = exact
        ? `${rest.length} related match${rest.length === 1 ? '' : 'es'}`
        : `${rest.length} match${rest.length === 1 ? '' : 'es'}`;
      parts.push(`<div class="section-label">${label}</div>`);
      parts.push(`<div class="rows">${rest.map(resultRow).join('')}</div>`);
    }

    const title = exact ? `🎫 ${exact.id}` : `🔎 Find: ${U.esc(q)}`;
    Drawer.openHtml(title, parts.join(''));
  }

  async function run(q, deep) {
    q = (q || '').trim();
    if (!q) return;
    lastQuery = q;
    try {
      const data = await U.fetchJSON(`/api/ledger-find?q=${encodeURIComponent(q)}`);
      if (q !== lastQuery) return;   // a newer query superseded this one
      renderResults(data, !!deep);
    } catch (err) {
      Drawer.openHtml(`🔎 Find: ${U.esc(q)}`,
        `<div class="load-error">${U.esc(err.message || 'search failed')}</div>`);
    }
  }

  /* ── #find/<ID> deep link ──────────────────────────────────────────────
     Called by app.js on boot (after Drawer.init) and on every hashchange.
     Not a tab: whatever tab is showing stays put, the record opens over it. */
  function openFromHash() {
    const m = /^#find\/(.+)$/.exec(location.hash || '');
    if (!m) return false;
    const q = decodeURIComponent(m[1]).trim();
    if (!q) return false;
    if (input) input.value = q;
    run(q, true);
    return true;
  }

  window.QuickFind = { run, openFromHash };
  window.addEventListener('hashchange', openFromHash);

  if (!input) return;   // header box absent: the deep link still works

  input.addEventListener('input', () => {
    const q = input.value.trim();
    clearTimeout(timer);
    if (q.length < 2) return;
    timer = setTimeout(() => run(q), 220);
  });

  input.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); clearTimeout(timer); run(input.value); }
    else if (e.key === 'Escape') { input.blur(); }
  });

  /* global shortcuts: `/` or Ctrl/Cmd+K focus the box (ignored while already
     typing in a field so it never eats a real keystroke) */
  document.addEventListener('keydown', e => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || '')
      || document.activeElement?.isContentEditable;
    if ((e.key === '/' && !typing) ||
        ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K'))) {
      e.preventDefault();
      input.focus();
      input.select();
    }
  });
})();
