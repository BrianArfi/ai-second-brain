/* ═══════════════════════════════════════════════════════════════════
   tab-system.js — ⚙ System tab: harness self-observability.
   Registers Tabs.system per the components.js contract. Wrapped in an
   IIFE so local helpers can't collide with app.js / tab-work.js /
   tab-meetings.js sharing the same classic-script global scope; only
   Comp / U calls + the documented "Other CSS" classes are used directly.
   ═══════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';
  window.Tabs = window.Tabs || {};

  const FRESH_KIND = { fresh: 'good', stale: 'warn', dead: 'serious', missing: 'muted' };
  const SEV_KIND = { fail: 'serious', warn: 'warn', info: 'muted' };

  /* Hardcoded from server.py's _freshness_payload (8 labels, exact strings) — what
     feeds each source + the refresh path, shown only for stale/dead rows. Default
     empty for anything not in this map (e.g. missing / unrecognized labels). */
  const FRESH_CONTEXT = {
    'Dashboard.md': 'Top-line status doc — refreshed by /morning-update or /evening-update',
    'tickets.json (tracker)': 'Ticket tracker state — updated by dashboard actions + enrich_tickets.py',
    'portfolio.json': 'Portfolio rollups — refresh via .agent/scripts/portfolio_render.py',
    'insights.json (meeting takeaways)': 'legacy meeting takeaways; superseded by Meetings tab — safe to ignore',
    'fathom_registry.json': 'Fathom recording index — refresh via scripts/fathom_registry_sync.py',
    'daily_update_morning.md': 'Morning briefing — refreshed by /morning-update (~09:30 WIB)',
    'daily_update_evening.md': 'Evening recap — refreshed by /evening-update (~21:30 WIB)',
    'agy cost summary': 'agy-bridge savings ledger — refreshed by any --task call or probe.py',
  };

  /* Mirrors server.py's JOB_LOG_MAP keys (dashboard/server.py) — used only to render
     a muted "known jobs" hint on a 404, since U.fetchJSON discards the response body
     on a non-OK status (it throws a plain Error before parsing json). */
  const JOB_LOG_KNOWN = ['maintenance', 'dashboard-keepalive', 'vexa-auto', 'mention-ledger',
    'commitment-ledger', 'waiting-watchdog', 'outcomes-loop', 'premeeting-cards', 'harness-health',
    'work-hours'];

  /* Mirrors server.py's JOB_RUN_MAP whitelist — only these jobs get a [▶ Run now]
     button on a failing routine row (everything else still gets [✓ Ack]). */
  const RUNNABLE_JOBS = new Set(['outcomes-loop', 'harness-health', 'commitment-ledger',
    'waiting-watchdog', 'premeeting-cards', 'maintenance', 'work-hours']);

  /* Job-specific "why + what to do" line for a failing routine — shown even before
     the lazy job-log is expanded, so "what can I do about this failure?" has an answer inline. */
  const FAIL_HINTS = {
    'outcomes-loop': 'Metabase session expired → refresh METABASE_SESSION_TOKEN di root .env',
    'harness-health': 'See the findings list in the Harness health card below for what tripped it',
    'vexa-auto': 'Bot health lives on the <a href="#meetings">Meetings tab</a>',
  };

  function val(r) { return r && r.status === 'fulfilled' ? r.value : null; }
  function errMsg(r) { return (r && r.reason && (r.reason.message || String(r.reason))) || 'unknown error'; }
  function errCard(key, icon, title, r) {
    return Comp.card({ key, icon, title, body: `<div class="load-error">${U.esc(errMsg(r))}</div>` });
  }
  function fmtUsd(n) { return n == null ? '—' : `$${Number(n).toFixed(2)}`; }

  /* ── 1. Hero row: jobs failing / docs 7d / tickets done 7d / total AI cost ── */
  function heroSection(mR, cR, tuR) {
    const tiles = [];
    const m = val(mR);
    if (m) {
      /* fail excludes acked failures server-side (ack join); an acked job
         renders as a muted "acked" row in Routines, so it must not light
         this tile either — the tile and the row always agree */
      const fail = m.heartbeat?.fail || 0;
      const ackedN = m.heartbeat?.acked || 0;
      const failingJobs = (m.heartbeat?.jobs || []).filter(j => !j.ok && !j.acked).map(j => j.job);
      /* href + the delegated scroll handler below: a failing count must never
         be a dead end — clicking lands on the Routines card, where failing
         rows sort first and expand into the job-log drill */
      tiles.push(Comp.statTile({
        key: 'sys-jobs-failing', icon: fail ? '🛑' : '✅', label: 'Jobs failing', value: fail,
        sub: fail ? failingJobs.slice(0, 3).join(', ')
          : (ackedN ? `all healthy · ${ackedN} acked` : 'all routines healthy'),
        status: fail > 0 ? 'critical' : 'good', href: '#system', tick: true,
      }));
      tiles.push(Comp.statTile({
        key: 'sys-docs-7d', icon: '📄', label: 'Docs created 7d', value: m.git?.docs_created_7d ?? 0,
        sub: `${m.git?.docs_revised_7d ?? 0} revised · ${m.git?.commits_7d ?? 0} commits`, tick: true,
      }));
      tiles.push(Comp.statTile({
        key: 'sys-tickets-done-7d', icon: '🎫', label: 'Tickets done 7d', value: m.tickets?.done_7d ?? 0,
        sub: `${m.tickets?.created_7d ?? 0} created · ${m.tickets?.overdue ?? 0} overdue now`, tick: true,
      }));
    } else {
      tiles.push(`<div class="load-error">metrics unavailable: ${U.esc(errMsg(mR))}</div>`);
    }
    /* Total AI cost = Claude per-token API-equivalent (token-tracker) + agy-bridge
       actual spend. The old tile showed ONLY the agy half ($1.52), which read as
       "this harness costs a dollar" when Claude inference is ~99.97% of the real
       per-inference bill. Both halves are clamped to the SAME window (the
       token-tracker range) so the sum is not a 30d + all-time mix. */
    const c = val(cR);
    const tu = val(tuR);
    const claudeUsd = tu && tu.totals ? tu.totals.est_cost_usd : null;
    let agyUsd = c && c.totals ? (c.totals.actual_usd ?? null) : null;
    if (c && tu && tu.range_start && tu.range_end) {
      agyUsd = Object.entries(c.by_day || {})
        .filter(([d]) => d >= tu.range_start && d <= tu.range_end)
        .reduce((a, [, s]) => a + (s.actual_usd ?? 0), 0);
    }
    if (claudeUsd != null || agyUsd != null) {
      const win = tu && tu.range_start && tu.range_end
        ? `${tu.range_start} → ${tu.range_end}` : `${(tu && tu.window_days) ?? 30}d`;
      tiles.push(Comp.statTile({
        key: 'sys-ai-cost', icon: '💸',
        label: `AI cost ${(tu && tu.window_days) ?? 30}d (API-equiv)`,
        value: fmtUsd((claudeUsd ?? 0) + (agyUsd ?? 0)),
        sub: `Claude ${fmtUsd(claudeUsd)} · agy ${fmtUsd(agyUsd)} · ${win}`,
        href: '#system', tick: true,
      }));
    } else {
      tiles.push(`<div class="load-error">cost unavailable: ${U.esc(errMsg(cR))}</div>`);
    }
    return `<div class="hero-row">${tiles.join('')}</div>`;
  }

  /* ── 2. 📡 Data freshness: the ONLY place dead sources appear ── */
  function freshnessSection(mR) {
    if (!mR || mR.status !== 'fulfilled') return errCard('freshness', '📡', 'Data freshness', mR);
    const fresh = mR.value.freshness || [];
    const rows = fresh.map(f => {
      const kind = FRESH_KIND[f.state] || 'muted';
      const dim = f.state === 'stale' || f.state === 'dead';
      const age = f.age_h != null ? U.fmtAge(f.age_h) : '—';
      const row = Comp.listRow({
        key: `fresh:${f.label}`, icon: '📄', title: f.label,
        badges: [Comp.badge(kind, f.state)], right: U.esc(age), dim,
      });
      const ctx = dim ? FRESH_CONTEXT[f.label] : null;
      return ctx ? `${row}<div class="row-subtext">${U.esc(ctx)}</div>` : row;
    });
    const deadCount = fresh.filter(f => f.state === 'dead').length;
    return Comp.card({
      key: 'freshness', icon: '📡', title: 'Data freshness',
      count: `${fresh.length} sources${deadCount ? ` · ${deadCount} dead` : ''}`,
      status: deadCount ? 'serious' : null,
      body: `<div class="rows">${rows.join('') || Comp.emptyState({ icon: '📡', title: 'No freshness data' })}</div>`,
      open: true,
    });
  }

  /* ── 3. ⏰ Routines: failing first, every row expands (lazy) to a job-log drill ── */
  const JOB_LOG_LOADING = `<div class="skeleton"><div class="skeleton-line"></div>` +
    `<div class="skeleton-line w-80"></div><div class="skeleton-line w-60"></div></div>`;

  /* Lazy-fetches /api/job-log for a routine's job on first open; caches on the
     <details> node itself via a dataset flag, so a full re-render() (60s refresh /
     tab switch) is the only thing that resets it — reopening the SAME node never
     re-fetches. */
  async function ensureJobLog(details, job) {
    if (details.dataset.jlLoaded) return;
    details.dataset.jlLoaded = '1';
    const slot = details.querySelector('.row-expand');
    if (!slot) return;
    try {
      const d = await U.fetchJSON(`/api/job-log?job=${encodeURIComponent(job)}`);
      const note = d.note ? `<div class="row-subtext">${U.esc(d.note)}</div>` : '';
      /* last_heartbeat passes through verbatim — Comp.logPanel accepts the
         full heartbeat row object and extracts ts_wib itself */
      slot.innerHTML = Comp.logPanel({ job: d.job, tail: d.tail, last_heartbeat: d.last_heartbeat }) + note;
    } catch (err) {
      if (/^HTTP 404/.test(err.message || '')) {
        slot.innerHTML = `<div class="row-subtext">no job-log wired for "${U.esc(job)}" — known: ${U.esc(JOB_LOG_KNOWN.join(', '))}</div>`;
      } else {
        slot.innerHTML = `<div class="load-error">${U.esc(err.message)}</div>`;
      }
    }
  }

  function wireRoutineLogs(panel) {
    panel.querySelectorAll('details.row[data-key^="routine:"]').forEach(details => {
      details.addEventListener('toggle', () => {
        if (!details.open) return;
        ensureJobLog(details, details.dataset.key.slice('routine:'.length));
      });
    });
  }

  /* hbR (/api/heartbeat) fills the gap for FAILING heartbeat-only jobs that
     have no /api/routines entry (e.g. vexa-bots): without it the "Jobs
     failing" tile could point at a Routines card that doesn't contain the
     failing job — a dead end. Their expand shows the full heartbeat summary
     inline (no lazy job-log fetch; key prefix hbjob: keeps wireRoutineLogs
     away from them). ok-but-needs_reauth rows get their OWN 'reauth' badge —
     never folded into 'failing' (same rule as the routines rows below).
     NOTE on acked: registered routines get their acked flag joined
     server-side (per-row r.acked), and /api/routines ALSO exposes the raw
     ack map top-level (acks: {job: epoch seconds}) precisely so
     heartbeat-only jobs can apply the same join client-side: ack epoch at/
     after the failing row's own ts mutes the row to "acked"; a NEWER
     failure makes it live again. If a job like this later gets registered
     as a routine, the `known` filter below drops it from this list
     automatically and routinesSection's own acked-aware badge takes over. */
  function heartbeatOnlyRows(routines, hbR, acks) {
    const known = new Set(routines.map(r => r.job));
    const latest = (hbR && hbR.status === 'fulfilled' && hbR.value.latest) || [];
    return latest
      .filter(j => j.job && !known.has(j.job) &&
        (String(j.status).toLowerCase() !== 'ok' || j.needs_reauth))
      .map(j => {
        const failMs = Date.parse(j.ts_wib);
        const ageH = (Date.now() - failMs) / 3600000;
        const vexa = /^vexa/.test(j.job);
        const isReauth = String(j.status).toLowerCase() === 'ok' && j.needs_reauth;
        /* same ack-vs-fail-ts join the server does for registered routines:
           ack epoch (seconds) at/after this failing row's ts -> muted "acked" */
        const ackTs = (acks || {})[j.job];
        const acked = !isReauth && Number.isFinite(failMs) &&
          typeof ackTs === 'number' && ackTs * 1000 >= failMs;
        const badge = isReauth ? Comp.badge('warn', 'reauth')
          : acked ? Comp.badge('muted', 'acked')
          : Comp.badge('serious', 'failing');
        /* "job vexa is failing, what can I do?" — heartbeat-only rows get no
           [Run now] (no run-map entry exists for them) but DO get [Ack]
           (/api/ack-job takes any job name; the join above mutes the row) and
           the same AI-solve escape hatch as a failing/reauth routine row;
           server's fix-job spec special-cases ref 'vexa-auto'/'vexa-bots'.
           Acked rows drop both — same rule as routinesSection: a human-
           confirmed read needs no re-ack and no AI offer.
           Rendered as an always-visible subtext (same placement routinesSection
           uses for its Run now/Ack/AI solve row) — NOT buried inside the
           collapsed expandBody, so it answers "what can I do" without a click. */
        const actionBtns = acked ? '' : [
          `<button class="prep-link job-ack-btn" data-job="${U.esc(j.job)}">✓ Ack</button>`,
          Comp.aiButton({ kind: 'fix-job', ref: j.job, label: '🤖 AI solve' }),
        ].join(' ');
        const expand = `<p>${U.esc(j.summary || '(no summary in the heartbeat row)')}</p>` +
          `<p class="row-subtext">Heartbeat-only job — no cron-registry entry / job-log wired.` +
          `${vexa ? ' Bot sends & recorder health live on the <a href="#meetings">Meetings tab</a>.' : ''}</p>`;
        return {
          rank: isReauth ? 1.5 : acked ? 3 : 0,
          isFailing: !isReauth && !acked,
          html: Comp.listRow({
            key: `hbjob:${j.job}`, icon: '📟', title: j.job,
            badges: [badge, Comp.badge('muted', 'heartbeat-only')],
            right: Number.isFinite(ageH) ? U.esc(U.fmtAge(ageH)) : '—',
            expandBody: expand,
          }) + (actionBtns ? `<div class="row-subtext">${actionBtns}</div>` : ''),
        };
      });
  }

  function routinesSection(rR, hbR) {
    if (!rR || rR.status !== 'fulfilled') return errCard('routines', '⏰', 'Routines', rR);
    const routines = rR.value.routines || [];
    const now = Date.now();
    const built = routines.map(r => {
      const lr = r.last_run;
      const enabled = r.enabled !== false;
      const meta = r.schedule || '';
      let rank, badge, right = '—', subtext = '';
      if (!enabled) {
        rank = 5; badge = Comp.badge('muted', 'disabled');
      } else if (r.state === 'no-data' || !lr) {
        rank = 4; badge = Comp.badge('muted', 'no runs yet');
      } else {
        const ageH = (now - Date.parse(lr.ts_wib)) / 3600000;
        right = U.esc(U.fmtAge(ageH));
        if (r.state === 'ok') {
          rank = 2; badge = Comp.badge('good', 'ok');
        } else if (r.state === 'reauth') {
          /* NOT counted as failing — status=ok, just needs a human token refresh */
          rank = 1; badge = Comp.badge('warn', 'reauth');
          /* "what can I do?" applies to reauth too — AI can't refresh a token
             itself but CAN diagnose/point at the fix, same fix-job kind as a
             failing job */
          subtext = `<div class="row-subtext">⚠ needs reauth` +
            `${lr.summary ? ` — ${U.esc(lr.summary)}` : ''}. Refresh the token; it clears on the next run.</div>` +
            `<div class="row-subtext">${Comp.aiButton({ kind: 'fix-job', ref: r.job, label: '🤖 AI solve' })}</div>`;
        } else {
          const acked = !!r.acked;
          rank = acked ? 3 : 0;
          badge = acked ? Comp.badge('muted', 'acked') : Comp.badge('serious', 'failing');
          const bits = [];
          /* failure summary WRAPS under the row instead of dying in .row-meta's
             nowrap ellipsis — the why is readable before expanding to the log */
          if (lr.summary) bits.push(`<div class="row-subtext">${U.esc(lr.summary)}</div>`);
          const hint = FAIL_HINTS[r.job];
          if (hint) bits.push(`<div class="row-subtext">${hint}</div>`);
          /* "what can I do about this failure?" — always give an action, never a dead end:
             [Run now] (only for jobs server.py's JOB_RUN_MAP whitelists) + [Ack]
             + AI solve (every unacked failing row — acked ones already have a
             human-confirmed read, no need to offer AI again).
             .prep-link is the shared button-styled class (used elsewhere for
             "open →"); reused here — no dedicated action-button class exists. */
          const canRun = RUNNABLE_JOBS.has(r.job);
          const actionBtns = [
            canRun ? `<button class="prep-link job-run-btn" data-job="${U.esc(r.job)}">▶ Run now</button>` : '',
            acked ? '' : `<button class="prep-link job-ack-btn" data-job="${U.esc(r.job)}">✓ Ack</button>`,
            acked ? '' : Comp.aiButton({ kind: 'fix-job', ref: r.job, label: '🤖 AI solve' }),
          ].filter(Boolean).join(' ');
          if (actionBtns) bits.push(`<div class="row-subtext">${actionBtns}</div>`);
          subtext = bits.join('');
        }
      }
      return {
        rank,
        isFailing: enabled && r.state === 'fail' && !r.acked,
        html: Comp.listRow({
          key: `routine:${r.job}`, icon: '⏰', title: r.name || r.job, badges: [badge], meta, right,
          expandBody: JOB_LOG_LOADING,
        }) + subtext,
      };
    });
    const hbOnly = heartbeatOnlyRows(routines, hbR, rR.value.acks || {});
    built.push(...hbOnly);
    built.sort((a, b) => a.rank - b.rank);
    const okCount = routines.filter(r => r.enabled !== false && r.state === 'ok').length;
    const failCount = built.filter(x => x.isFailing).length;
    return Comp.card({
      key: 'routines', icon: '⏰', title: 'Routines',
      count: `${okCount} ok · ${failCount} failing`,
      status: failCount ? 'serious' : null,
      body: `<div class="rows">${built.map(x => x.html).join('') || Comp.emptyState({ icon: '⏰', title: 'No routines registered' })}</div>`,
      open: true,
    });
  }

  /* ── Run now / Ack — the answer to "what can I do about this failure?" ── */
  async function runJob(btn) {
    if (btn.disabled) return;
    const job = btn.dataset.job;
    btn.disabled = true;
    const orig = btn.textContent;
    btn.textContent = '⏳ Running…';
    try {
      const res = await U.fetchJSON('/api/run-job', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job }),
      });
      Comp.toast(`${job}: rc=${res.rc} (${res.took_s}s)`, res.ok);
      /* patch just this row's expand slot with the fresh tail — no full
         re-render, so we never clobber what we just fetched */
      const details = document.querySelector(`#tab-system details.row[data-key="routine:${job}"]`);
      const slot = details && details.querySelector('.row-expand');
      if (slot) {
        slot.innerHTML = Comp.logPanel({ job, tail: res.tail }) +
          (res.note ? `<div class="row-subtext">${U.esc(res.note)}</div>` : '');
        details.dataset.jlLoaded = '1';
        details.open = true;
      }
    } catch (err) {
      const msg = /^HTTP 409/.test(err.message || '') ? 'already running' : err.message;
      Comp.toast(`${job} run failed: ${msg}`, false);
    } finally {
      btn.disabled = false;
      btn.textContent = orig;
    }
  }

  async function ackJob(btn) {
    if (btn.disabled) return;
    const job = btn.dataset.job;
    btn.disabled = true;
    try {
      await U.fetchJSON('/api/ack-job', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job }),
      });
      Comp.toast(`Acked ${job}`, true);
      render();   // full re-fetch so the row secondarys to muted "acked" + counts update
    } catch (err) {
      Comp.toast(`Ack failed: ${err.message}`, false);
      btn.disabled = false;
    }
  }

  /* ── 4. 🧠 Harness health: clickable findings, last run, report link ──
     (Inventory card + counts line removed — replaced by the Harness Map card,
     which answers "struktur repo & konsep otak/refleks/tangan gak tergambar"
     more usefully than a flat command/skill/script count ever did.) */
  let _harnessFindings = [];

  function harnessFindingRow(f) {
    const sevBadge = Comp.badge(SEV_KIND[f.severity] || 'muted', f.severity || 'info');
    const kindBadge = Comp.badge('muted', f.kind || 'finding');
    const jobMeta = f.job ? `<span class="row-meta">${U.esc(f.job)}</span>` : '';
    /* fix-hint linkChips ONLY when the finding actually carries a path/url —
       most current findings are text-only (detail), so this is usually empty */
    const links = Comp.linkChips([f.path, f.url, f.fix_path, f.ref].filter(Boolean));
    return `<div class="row"><span class="row-badges">${sevBadge}${kindBadge}</span>` +
      `<span class="row-title">${U.esc(f.detail || f.summary || '(no detail)')}</span>${jobMeta}</div>` +
      (links ? `<div class="row-subtext">${links}</div>` : '');
  }

  function openHarnessFindings() {
    const order = { fail: 0, warn: 1, info: 2 };
    const sorted = [..._harnessFindings].sort((a, b) => (order[a.severity] ?? 3) - (order[b.severity] ?? 3));
    const groups = {};
    sorted.forEach(f => { const k = f.severity || 'info'; (groups[k] = groups[k] || []).push(f); });
    const order2 = ['fail', 'warn', 'info'];
    const sevKeys = order2.filter(k => groups[k]).concat(Object.keys(groups).filter(k => !order2.includes(k)));
    const html = sevKeys.map(sev =>
      `<div class="section-label">${U.esc(sev)} (${groups[sev].length})</div>` +
      `<div class="rows">${groups[sev].map(harnessFindingRow).join('')}</div>`
    ).join('') || Comp.emptyState({ icon: '🧠', title: 'No findings' });
    Drawer.openWide('Harness findings', html);
  }

  function harnessSection(hR) {
    if (!hR || hR.status !== 'fulfilled') return errCard('harness', '🧠', 'Harness health', hR);
    const h = hR.value;
    const hr = h.health_review || {};
    const bySev = hr.by_severity || {};
    _harnessFindings = hr.findings || [];
    const rows = [];
    if (Object.keys(bySev).length) {
      /* severity chips are now CLICKABLE — opens the full grouped findings
         list in a wide drawer instead of leaving the owner stuck at a bare count */
      const chips = Object.entries(bySev).map(([sev, n]) =>
        `<button class="sev-chip-btn" title="Open findings">${Comp.badge(SEV_KIND[sev] || 'muted', `${sev} ${n}`)}</button>`).join('');
      rows.push(Comp.listRow({ key: 'harness:findings', icon: '🔎', title: 'Findings by severity', right: chips }));
    } else {
      rows.push(Comp.emptyState({ icon: '🧠', title: hr.note || 'No health review yet' }));
    }
    if (hr.last_run != null) {
      const ageH = (Date.now() / 1000 - hr.last_run) / 3600;
      rows.push(Comp.listRow({ key: 'harness:last-run', icon: '🕒', title: `Last run ${U.fmtAge(ageH)} ago` }));
    }
    if (hr.latest_report) {
      rows.push(Comp.listRow({
        key: 'harness:report', icon: '📄', title: 'Latest health report',
        right: `<button class="prep-link" data-drawer-path="${U.esc(hr.latest_report)}" data-drawer-title="Harness health report">open →</button>`,
      }));
    }
    const findingsTotal = hr.findings_total ?? 0;
    return Comp.card({
      key: 'harness', icon: '🧠', title: 'Harness health',
      count: `${findingsTotal} finding${findingsTotal === 1 ? '' : 's'}`,
      status: bySev.fail ? 'serious' : (bySev.warn ? 'warn' : null),
      body: `<div class="rows">${rows.join('')}</div>`, open: false,
    });
  }

  /* ── 4b. 🗺 Harness Map (replaces Inventory) — Indra/Refleks/Otak/Memori/
     Tangan flow, live status per node from /api/harness-map. Rendering is
     Comp.harnessMap(d) (the SVG-connector version) — fed the payload
     verbatim; the click contract (button.map-node[data-node-id]) is
     unchanged so onMapNodeClick below keeps working untouched. ── */
  let _harnessMapNodes = {};

  function harnessMapSection(hmR) {
    if (!hmR || hmR.status !== 'fulfilled') return errCard('harness-map', '🗺', 'Harness map', hmR);
    const groups = hmR.value.groups || [];
    _harnessMapNodes = {};
    groups.forEach(g => (g.nodes || []).forEach(n => { _harnessMapNodes[n.id] = n; }));
    const totalNodes = groups.reduce((a, g) => a + (g.nodes || []).length, 0);
    const body = Comp.harnessMap(hmR.value) +
      `<div class="row-subtext">Senses collect signals → Reflexes handle the mechanical work every few minutes → ` +
      `Brain (Claude) synthesizes and decides → Memory holds state → Hands execute (always approval-gated)</div>`;
    return Comp.card({
      key: 'harness-map', icon: '🗺', title: 'Harness map', count: `${totalNodes} nodes`, open: true, body,
    });
  }

  function mapRefDrawerHtml(node, ref) {
    if (ref.kind === 'freshness') {
      const ageTxt = ref.age_h != null ? U.fmtAge(ref.age_h) : '—';
      const badge = Comp.badge(FRESH_KIND[ref.state] || 'muted', ref.state || 'unknown');
      return `<div class="rows">${Comp.listRow({ key: `map-fresh:${ref.id}`, icon: '📄', title: ref.id, badges: [badge], right: ageTxt })}</div>` +
        (ref.context ? `<div class="row-subtext">${U.esc(ref.context)}</div>` : '') +
        `<p>${U.esc(node.desc || '')}</p>`;
    }
    return `<p>${U.esc(node.desc || '(no further detail)')}</p>`;
  }

  async function openMapJobLog(label, job) {
    Drawer.openHtml(label, JOB_LOG_LOADING);
    try {
      const d = await U.fetchJSON(`/api/job-log?job=${encodeURIComponent(job)}`);
      const note = d.note ? `<div class="row-subtext">${U.esc(d.note)}</div>` : '';
      Drawer.openHtml(label, Comp.logPanel({ job: d.job, tail: d.tail, last_heartbeat: d.last_heartbeat }) + note);
    } catch (err) {
      Drawer.openHtml(label, `<div class="load-error">${U.esc(err.message)}</div>`);
    }
  }

  function onMapNodeClick(btn) {
    const node = _harnessMapNodes[btn.dataset.nodeId];
    if (!node) return;
    const ref = node.ref || { kind: 'none' };
    if (ref.kind === 'job') openMapJobLog(node.label, ref.id);
    else if (ref.kind === 'file') Drawer.open(node.label, ref.id);
    else Drawer.openHtml(node.label, mapRefDrawerHtml(node, ref));
  }

  /* ── 5. 📉 Activity: small + recessive, collapsed by default ── */
  function activitySection(spR, mR) {
    if (!spR || spR.status !== 'fulfilled') return errCard('activity', '📉', 'Activity', spR);
    const days = spR.value.days || [];
    /* {label, value} objects (spark v2) -> per-day hover tooltip ("2026-07-11 — 6") */
    const points = days.map(d => ({ label: d.date, value: d.count }));
    const m = val(mR);
    const total30 = m?.activity?.total_30d;
    /* header count line — stays on the CARD (count), unrelated to spark's own opts.label */
    const count = total30 != null ? `${total30} events / 30d` : `${days.reduce((a, d) => a + (d.count || 0), 0)} events / 14d`;
    const spark = Comp.spark(points, {
      w: 220, h: 32, label: 'Aktivitas harness — events/hari', showLast: true, showAxis: true,
    });
    return Comp.card({
      key: 'activity', icon: '📉', title: 'Activity', count,
      body: spark || Comp.emptyState({ icon: '🌙', title: 'No activity yet' }),
      open: false,
    });
  }

  /* ── 6. 💸 Cost & Savings: ALWAYS visible (replaces the old collapsed "Cost
     detail"). Ring (overall savings %) + 14-day duo bars w/ legend, then
     by_task (harvest/critic/research) and by_model top 5. Uses the normalized
     spent/saved/savings_pct aliases the /api/agy-cost contract guarantees
     alongside the originals — renders "—" when an alias is null, never
     fabricates a number. ── */
  /* canonical order first, then any OTHER task buckets the ledger grows
     (e.g. 'draft') alphabetically — never silently hide a bucket */
  const TASK_ORDER = ['harvest', 'critic', 'research'];
  const TASK_ICON = { harvest: '🌾', critic: '🔎', research: '🔬', draft: '✍️' };

  function costSection(cR) {
    if (!cR || cR.status !== 'fulfilled') return errCard('cost', '💸', 'agy-bridge Cost & Savings', cR);
    const c = cR.value;
    const t = c.totals || {};
    const hasTotals = t.spent != null || t.saved != null;
    const subTotal = (t.spent ?? 0) + (t.saved ?? 0);

    const ring = Comp.ringStat({
      pct: t.savings_pct, label: 'saved',
      sub: hasTotals ? `${fmtUsd(t.saved)} of ${fmtUsd(subTotal)}` : '—',
    });
    const legend = `<div class="stat-sub">${Comp.badge('cat-1', 'spent')}${Comp.badge('cat-2', 'saved')}</div>`;
    const days = Object.entries(c.by_day || {}).sort((a, b) => a[0].localeCompare(b[0])).slice(-14)
      .map(([date, s]) => ({ date, a: s.spent, b: s.saved }));
    const barsHtml = Comp.duoBars(days);
    const right = `<div class="stack">${legend}${barsHtml || Comp.emptyState({ icon: '📉', title: 'No daily usage yet' })}</div>`;

    const taskKeys = TASK_ORDER.filter(k => (c.by_task || {})[k])
      .concat(Object.keys(c.by_task || {}).filter(k => !TASK_ORDER.includes(k)).sort());
    const taskRows = taskKeys.map(k => {
      const s = c.by_task[k];
      return Comp.listRow({
        key: `cost:task:${k}`, icon: TASK_ICON[k] || '⚙', title: k,
        right: `<span class="num">${fmtUsd(s.spent)} spent · ${fmtUsd(s.saved)} saved · ` +
          `${s.savings_pct == null ? '—' : s.savings_pct + '%'}</span>`,
      });
    });

    const byModel = Object.entries(c.by_model || {})
      .sort((a, b) => (b[1].spent ?? 0) - (a[1].spent ?? 0)).slice(0, 5);
    const modelRows = byModel.map(([name, s]) => Comp.listRow({
      key: `cost:model:${name}`, icon: '🤖', title: name,
      meta: `${s.answers ?? 0} calls`,
      right: `<span class="num">${fmtUsd(s.spent)} · ${s.savings_pct == null ? '—' : s.savings_pct + '%'} saved</span>`,
    }));

    const body = `<div class="two-col">${ring}${right}</div>` +
      `<div class="section-label">By task</div><div class="rows">${taskRows.join('') || Comp.emptyState({ icon: '💸', title: 'No task usage yet' })}</div>` +
      `<div class="section-label">By model (top 5)</div><div class="rows">${modelRows.join('') || Comp.emptyState({ icon: '💸', title: 'No model usage yet' })}</div>`;

    return Comp.card({
      key: 'cost', icon: '💸', title: 'agy-bridge Cost & Savings',
      count: hasTotals ? `${fmtUsd(t.spent)} spent · ${fmtUsd(t.saved)} saved` : (c.note || 'no usage yet'),
      body, open: true,
    });
  }

  /* ── 7. 🧮 Token Usage (Claude): F1's /api/token-usage — a Claude Code
     SUBSCRIPTION-cost ESTIMATE by task type / model / day, distinct from the
     agy-bridge offload savings in Cost & Savings above (the footer note +
     link makes that distinction explicit, per the payload's own honesty
     line). Endpoint is landing concurrently with this card — a missing
     route currently drops the TCP connection with no HTTP response at all
     (fetch rejects), so ANY rejected promise here (network refusal, 404,
     future 5xx) renders the SAME graceful "no token data yet" EmptyState
     rather than the alarm-red .load-error the other System cards use. */
  const TU_TASK_ICON = { harvest: '🌾', critic: '🔎', research: '🔬', draft: '✍️',
    review: '🔍', synthesize: '🧵', strategize: '♟', lookup: '📌' };

  /* rank -> categorical kind: top 6 get their own cat-1..cat-6 (shared between
     the distBar segment and its matching table row's badge dot); everything
     from rank 6 on folds into the SAME 'Other' cat-8 bucket the distBar uses,
     so a row's dot color always matches where it landed in the chart above */
  function tuKind(rank) { return rank < 6 ? `cat-${rank + 1}` : 'cat-8'; }

  /* local compact number formatter (kept inside this file, per the build
     spec) — 1,234,567 -> "1.2M", 48,300 -> "48k", small numbers pass through */
  function fmtCompact(n) {
    const v = Number(n) || 0;
    const a = Math.abs(v);
    if (a >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
    if (a >= 1e3) return `${Math.round(v / 1e3)}k`;
    return String(Math.round(v));
  }

  /* current period/date filter for the Token Usage card. days=null + no
     start/end == the cached default 30d view; days=N == trailing N days;
     start&end == an explicit inclusive WIB-date range. */
  let tuRange = { days: null, start: null, end: null };

  function tuRangeQS() {
    if (tuRange.start && tuRange.end) return `?start=${tuRange.start}&end=${tuRange.end}`;
    if (tuRange.days && tuRange.days !== 30) return `?days=${tuRange.days}`;
    return '';
  }

  /* the period segmented control + custom date range, reflecting tuRange so
     the active button/inputs survive a re-render */
  function tuControls() {
    const isDefault = !tuRange.start && (tuRange.days == null || tuRange.days === 30);
    const active = d => (!tuRange.start && String(tuRange.days ?? 30) === String(d)) ||
      (d === 30 && isDefault) ? ' active' : '';
    const seg = [7, 14, 30, 90]
      .map(d => `<button class="tu-range-btn${active(d)}" data-tu-days="${d}">${d}d</button>`).join('');
    return `<div class="tu-controls">` +
      `<div class="tu-seg">${seg}</div>` +
      `<div class="tu-daterange">` +
      `<input type="date" class="tu-start" value="${tuRange.start || ''}" aria-label="start date">` +
      `<span class="tu-sep">→</span>` +
      `<input type="date" class="tu-end" value="${tuRange.end || ''}" aria-label="end date">` +
      `<button class="tu-apply" data-tu-apply>Apply</button>` +
      `</div></div>`;
  }

  /* re-fetch the token-usage endpoint with the current filter and swap just the
     Token Usage card in place (the delegated listeners live on #tab-system so
     they survive the outerHTML replacement) */
  async function refreshTokenCard() {
    const card = document.querySelector('#tab-system details.card[data-key="token-usage"]');
    let tuR;
    try { tuR = { status: 'fulfilled', value: await U.fetchJSON('/api/token-usage' + tuRangeQS()) }; }
    catch (reason) { tuR = { status: 'rejected', reason }; }
    const html = tokenUsageSection(tuR);
    if (card) { const tmp = document.createElement('div'); tmp.innerHTML = html; card.replaceWith(tmp.firstElementChild); }
  }

  function tokenUsageEmpty(hint) {
    return Comp.card({
      key: 'token-usage', icon: '🧮', title: 'Token Usage (Claude)',
      body: tuControls() + Comp.emptyState({ icon: '🧮', title: 'No token data yet', hint }),
      open: true,
    });
  }

  function tokenUsageSection(tuR) {
    if (!tuR || tuR.status !== 'fulfilled') {
      return tokenUsageEmpty('/api/token-usage is not live yet — try again once the endpoint is deployed');
    }
    const d = tuR.value || {};
    const totals = d.totals || {};
    const byType = Array.isArray(d.by_task_type) ? d.by_task_type.slice() : [];
    const byModel = Array.isArray(d.by_model) ? d.by_model.slice() : [];
    const byDay = Array.isArray(d.by_day) ? d.by_day.slice() : [];
    const windowDays = d.window_days ?? 30;

    if (!byType.length && !byModel.length && !totals.sessions) {
      return tokenUsageEmpty(d.note || `${windowDays}d window — no usage recorded yet`);
    }

    const refreshingSuffix = d.refreshing ? ' · ⏳ refreshing' : '';
    const totalTok = (totals.input_tokens ?? 0) + (totals.output_tokens ?? 0) +
      (totals.cache_read_tokens ?? 0) + (totals.cache_write_tokens ?? 0);
    const rangeLabel = d.range_start && d.range_end
      ? `${d.range_start} → ${d.range_end}` : `${windowDays}d`;
    const count = `${fmtCompact(totalTok)} tokens · ${fmtUsd(totals.est_cost_usd)} est · ${rangeLabel}${refreshingSuffix}`;

    /* top strip: cost-share distBar (top 6 + Other) + a 14-of-30-day cost miniBars */
    const sortedByCost = byType.slice().sort((a, b) => (b.total_cost_usd ?? 0) - (a.total_cost_usd ?? 0));
    const top6 = sortedByCost.slice(0, 6);
    /* round the summed "Other" bucket — raw float addition (0.15 + 0.02 = 0.1699999999999998)
       would otherwise leak into the distBar legend's plain String(value) */
    const otherCost = Math.round(sortedByCost.slice(6).reduce((a, s) => a + (s.total_cost_usd ?? 0), 0) * 100) / 100;
    const distItems = top6.map((s, i) => ({ label: s.type, value: s.total_cost_usd ?? 0, kind: tuKind(i) }));
    if (otherCost > 0) distItems.push({ label: 'Other', value: otherCost, kind: 'cat-8' });
    const distHtml = Comp.distBar(distItems, { label: 'Cost share by task type' });

    const dayPoints = byDay.slice(-14).map(p => ({ label: p.date, value: p.est_cost_usd ?? 0 }));
    const dayLabel = `Est cost — last ${dayPoints.length}d`;
    const barsHtml = Comp.miniBars(dayPoints, { w: 200, h: 36, kind: 'cat-1', label: dayLabel });
    const topStrip = `<div class="two-col">` +
      `${distHtml || Comp.emptyState({ icon: '📊', title: 'No cost-share data yet' })}` +
      `<div class="stack"><div class="section-label">${dayLabel}</div>` +
      `${barsHtml || Comp.emptyState({ icon: '📉', title: 'No daily data yet' })}</div></div>`;

    /* the main table: per task type, sorted by total cost desc — type badge
       (color-matched to its distBar segment) · runs · avg in/out tokens ·
       avg cost · total cost · share% */
    const typeRows = sortedByCost.map((s, i) => {
      const avgTok = fmtCompact(s.avg_total);
      const avgInOut = `${fmtCompact(s.avg_input)} in / ${fmtCompact(s.avg_output)} out`;
      const avgCost = s.avg_cost_usd == null ? '—' : fmtUsd(s.avg_cost_usd);
      const share = s.share_cost_pct == null ? '—' : `${s.share_cost_pct}%`;
      return Comp.listRow({
        key: `tu:type:${s.type}`, icon: TU_TASK_ICON[s.type] || '⚙', title: s.type || '(unknown)',
        badges: [Comp.badge(tuKind(i), `${s.runs ?? 0} runs`)],
        meta: `avg ${avgTok} tok/run (${avgInOut})`,
        right: `<span class="num">${fmtCompact(s.total_tokens)} tok · ${avgCost} avg · ${fmtUsd(s.total_cost_usd)} · ${share}</span>`,
      });
    });

    /* nested "Per model" block — collapsed by default */
    const modelRows = byModel.slice().sort((a, b) => (b.est_cost_usd ?? 0) - (a.est_cost_usd ?? 0)).map(m => Comp.listRow({
      key: `tu:model:${m.model}`, icon: '🤖', title: m.model || '(unknown)',
      meta: `${m.runs ?? 0} runs`,
      right: `<span class="num">${fmtCompact(m.total_tokens)} tok · ${fmtUsd(m.est_cost_usd)}</span>`,
    }));
    const perModelCard = Comp.card({
      key: 'token-usage-by-model', icon: '🤖', title: 'Per model',
      count: `${byModel.length} model${byModel.length === 1 ? '' : 's'}`,
      body: `<div class="rows">${modelRows.join('') || Comp.emptyState({ icon: '🤖', title: 'No per-model data yet' })}</div>`,
      open: false,
    });

    /* footer = the payload's honesty line verbatim, never paraphrased, + a
       pointer to the REAL offload savings shown in Cost & Savings above */
    const footer = d.note
      ? `<div class="row-subtext">${U.esc(d.note)} — <a class="prep-link" href="#system">offload cost riil → Cost & Savings di atas</a></div>`
      : '';

    const body = tuControls() + topStrip +
      `<div class="section-label">By task type</div>` +
      `<div class="rows">${typeRows.join('') || Comp.emptyState({ icon: '🧮', title: 'No task-type data yet' })}</div>` +
      perModelCard + footer;

    return Comp.card({ key: 'token-usage', icon: '🧮', title: 'Token Usage (Claude)', count, body, open: true });
  }

  /* ── 8. 📈 Token Efficiency: /api/token-efficiency — weekly totals +
     offload share (built by .agent/scripts/token_efficiency.py), distinct
     from the 30d Token Usage card above (that one is a rolling window; this
     one is week-over-week trend + hotspots + a changelog of optimizations
     tried, so "did the last change actually help" has an answer). Same
     graceful-empty contract as tokenUsageEmpty above — a missing/empty
     payload never renders as a .load-error. */
  function tokenEfficiencyEmpty(hint) {
    return Comp.card({
      key: 'token-efficiency', icon: '📈', title: 'Token Efficiency',
      body: Comp.emptyState({ icon: '📈', title: 'No efficiency data yet', hint }),
      open: false,
    });
  }

  function pctDelta(curr, prev) {
    if (!Number.isFinite(curr) || !Number.isFinite(prev) || prev === 0) return null;
    return ((curr - prev) / prev) * 100;
  }

  function deltaChip(curr, prev, { lowerIsBetter = true } = {}) {
    const d = pctDelta(curr, prev);
    if (d == null) return '';
    const rounded = Math.round(d);
    const improved = lowerIsBetter ? rounded < 0 : rounded > 0;
    const kind = rounded === 0 ? 'muted' : improved ? 'good' : 'warn';
    const arrow = rounded === 0 ? '→' : rounded > 0 ? '↑' : '↓';
    return Comp.badge(kind, `${arrow} ${Math.abs(rounded)}% vs last wk`);
  }

  function tokenEfficiencySection(teR) {
    if (!teR || teR.status !== 'fulfilled') {
      return tokenEfficiencyEmpty('/api/token-efficiency is not live yet — try again once the endpoint is deployed');
    }
    const d = teR.value || {};
    const eff = d.efficiency;
    const weeks = Array.isArray(eff && eff.weeks) ? eff.weeks.slice() : [];
    const changelog = Array.isArray(d.changelog) ? d.changelog.slice() : [];
    if (!eff || !weeks.length) {
      return tokenEfficiencyEmpty('no weeks in journal/state/token_efficiency.json yet — run token_efficiency.py report');
    }

    const last = weeks[weeks.length - 1];
    const prev = weeks.length > 1 ? weeks[weeks.length - 2] : null;
    const lastTotals = last.totals || {};
    const prevTotals = (prev && prev.totals) || {};

    const count = `${fmtCompact(lastTotals.tokens)} tok · ${fmtUsd(lastTotals.cost_usd)} · ${last.week || 'this wk'}`;

    /* hero chips: this week's totals + week-over-week delta (tokens lower is
       better, offload share higher is better) */
    const chips = [
      `<div class="stat-sub">${fmtCompact(lastTotals.tokens)} tokens · ${lastTotals.runs ?? 0} runs · ${fmtUsd(lastTotals.cost_usd)}</div>`,
      `<div class="stat-sub">${deltaChip(lastTotals.tokens, prevTotals.tokens, { lowerIsBetter: true })}` +
      ` ${deltaChip(lastTotals.offloaded_share_pct, prevTotals.offloaded_share_pct, { lowerIsBetter: false })}</div>`,
    ].join('');

    /* per-week trend: total tokens (a) vs offloaded tokens (b) — same
       duoBars widget Cost & Savings uses for spent/saved, so the offload
       share reads as the visual "b slice" of each week's bar */
    const weekBars = weeks.slice(-10).map(w => ({
      date: w.week,
      a: (w.totals || {}).tokens || 0,
      b: Object.values(w.by_task_type || {}).reduce((a, t) => a + (t.offloaded_tokens || 0), 0),
    }));
    const trendLegend = `<div class="stat-sub">${Comp.badge('cat-1', 'total tokens')}${Comp.badge('cat-2', 'offloaded')}</div>`;
    const trendHtml = `<div class="stack">${trendLegend}${Comp.duoBars(weekBars) || Comp.emptyState({ icon: '📉', title: 'No weekly data yet' })}</div>`;

    /* top-3 hotspots (server pre-ranks; render verbatim, capped at 3 as a
       belt-and-braces in case the payload ever grows) */
    const hotspots = (eff.hotspots || []).slice(0, 3);
    const hotspotRows = hotspots.map((h, i) => Comp.listRow({
      key: `te:hotspot:${h.task_type}`, icon: '🔥', title: h.task_type || '(unknown)',
      badges: [Comp.badge(tuKind(i), fmtCompact(h.tokens))],
      expandBody: h.why ? `<p>${U.esc(h.why)}</p>` : '',
    }));

    /* what-changed: changelog rows newest-first, expand shows the expected
       effect + touched files (mirrors harnessFindingRow's linkChips use) */
    const changeRows = changelog.slice().reverse().slice(0, 20).map(c => {
      const ts = c.ts_wib ? U.esc(c.ts_wib.slice(0, 16).replace('T', ' ')) : '—';
      const files = Comp.linkChips(c.files || []);
      return Comp.listRow({
        key: `te:change:${c.ts_wib || Math.random()}`, icon: '🛠', title: c.what || '(no description)',
        meta: ts,
        expandBody: `${c.expected_effect ? `<p>${U.esc(c.expected_effect)}</p>` : ''}${files}`,
      });
    });

    const body = `<div class="two-col">${chips}${trendHtml}</div>` +
      `<div class="section-label">Top hotspots</div>` +
      `<div class="rows">${hotspotRows.join('') || Comp.emptyState({ icon: '🔥', title: 'No hotspots flagged' })}</div>` +
      `<div class="section-label">What changed</div>` +
      `<div class="rows">${changeRows.join('') || Comp.emptyState({ icon: '🛠', title: 'No optimizations logged yet' })}</div>`;

    return Comp.card({ key: 'token-efficiency', icon: '📈', title: 'Token Efficiency', count, body, open: false });
  }

  /* ── render + registration ── */
  async function render() {
    const panel = document.getElementById('tab-system');
    if (!panel) return;
    const [mR, rR, hR, cR, spR, hbR, hmR, tuR, teR] = await Promise.allSettled([
      U.fetchJSON('/api/metrics'),
      U.fetchJSON('/api/routines'),
      U.fetchJSON('/api/harness'),
      U.fetchJSON('/api/agy-cost'),
      U.fetchJSON('/api/activity-spark'),
      U.fetchJSON('/api/heartbeat'),
      U.fetchJSON('/api/harness-map'),
      U.fetchJSON('/api/token-usage'),
      U.fetchJSON('/api/token-efficiency'),
    ]);
    panel.innerHTML = [
      heroSection(mR, cR, tuR),
      freshnessSection(mR),
      routinesSection(rR, hbR),
      harnessSection(hR),
      harnessMapSection(hmR),
      activitySection(spR, mR),
      costSection(cR),
      tokenUsageSection(tuR),
      tokenEfficiencySection(teR),
    ].join('\n');
    wireRoutineLogs(panel);
  }

  /* Jobs-failing hero tile: stay on System — the detail IS the Routines card
     right below (failing rows sort first, each expands to its job-log), so
     scroll there and make sure it's open. Same pattern as Today's SLA tile. */
  document.addEventListener('click', e => {
    const tile = e.target.closest('#tab-system .stat-tile[data-key="sys-jobs-failing"]');
    if (!tile) return;
    e.preventDefault();
    const card = document.querySelector('#tab-system details.card[data-key="routines"]');
    if (!card) return;
    card.open = true;
    card.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });

  /* Run now / Ack / severity-chip / map-node — all System-tab-local delegated
     clicks (parallel to app.js/components.js's own document-level listeners;
     narrowly scoped to #tab-system so nothing here can steal a click meant
     for a different tab's identically-named class). */
  document.addEventListener('click', e => {
    const runBtn = e.target.closest('#tab-system .job-run-btn');
    if (runBtn) { e.preventDefault(); runJob(runBtn); return; }
    const ackBtn = e.target.closest('#tab-system .job-ack-btn');
    if (ackBtn) { e.preventDefault(); ackJob(ackBtn); return; }
    const sevBtn = e.target.closest('#tab-system .sev-chip-btn');
    if (sevBtn) { e.preventDefault(); openHarnessFindings(); return; }
    const mapNode = e.target.closest('#tab-system .map-node');
    if (mapNode) { e.preventDefault(); onMapNodeClick(mapNode); return; }
    const tuBtn = e.target.closest('#tab-system .tu-range-btn');
    if (tuBtn) {
      e.preventDefault();
      tuRange = { days: Number(tuBtn.dataset.tuDays), start: null, end: null };
      refreshTokenCard();
      return;
    }
    const tuApply = e.target.closest('#tab-system .tu-apply');
    if (tuApply) {
      e.preventDefault();
      const box = tuApply.closest('.tu-controls');
      const s = box?.querySelector('.tu-start')?.value;
      const en = box?.querySelector('.tu-end')?.value;
      if (s && en) { tuRange = { days: null, start: s, end: en }; refreshTokenCard(); }
      else Comp.toast('Pick both start and end dates', false);
    }
  });

  /* An AI-solve run (kind:'fix-job') finished — re-render so its row picks up
     whatever changed (e.g. a run acked/fixed the job on the next heartbeat
     tick) instead of sitting on stale rank/badges until the 60s refresh. */
  window.addEventListener('psb:ai-done', e => {
    if (e.detail && e.detail.kind === 'fix-job') render();
  });

  Tabs.system = {
    load() { render(); },
  };
})();
