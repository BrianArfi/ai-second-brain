/* ═══════════════════════════════════════════════════════════════════
   tab-hours.js — ⏱ Hours tab. Work-hours tracker: when the owner started,
   what ran in parallel (meetings + AI workstreams), actual vs effective
   hours and the leverage multiplier. Data: /api/work-hours (written by
   .agent/skills/work-hours). Only Comp, U + documented CSS classes.
   Route: #hours or #hours/YYYY-MM-DD (pins a day across refreshes).
   ═══════════════════════════════════════════════════════════════════ */
'use strict';
window.Tabs = window.Tabs || {};

(function () {
  const state = { data: null, err: null, loaded: false };

  const LANES = [
    { key: 'meetings', name: 'Meetings', cat: 'cat-1' },
    { key: 'work', name: 'Work PM', cat: 'cat-2' },
    { key: 'you', name: 'You', cat: 'cat-3' },
    { key: 'other', name: 'Other AI', cat: 'cat-4' },
  ];
  const LANE_BY_KEY = Object.fromEntries(LANES.map(l => [l.key, l]));

  /* ── tiny formatters ─────────────────────────────────────────── */
  const pad = n => String(n).padStart(2, '0');
  const mClock = m => `${pad(Math.floor(m / 60) % 24)}:${pad(m % 60)}`;
  const mDur = m => m >= 60 ? `${Math.floor(m / 60)}h ${pad(m % 60)}m` : `${m}m`;
  const hFmt = h => `${(Math.round(h * 10) / 10)}h`;

  function wibNow() {
    return new Date(Date.now() + 7 * 3600000);
  }
  function wibNowMin() {  // minutes since 00:00 WIB today
    const d = wibNow();
    return d.getUTCHours() * 60 + d.getUTCMinutes();
  }
  function todayWorkday() {  // 04:00-boundary workday date string, in WIB
    const d = new Date(Date.now() + 7 * 3600000 - 4 * 3600000);
    return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
  }
  function niceDate(dstr, weekday) {
    const [, m, d] = dstr.split('-').map(Number);
    const MON = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    return `${weekday || ''} ${d} ${MON[m]}`.trim();
  }

  /* ── selection ───────────────────────────────────────────────── */
  function sortedDays() {
    return Object.keys((state.data || {}).days || {}).sort();
  }
  function currentDate(filter) {
    const days = sortedDays();
    if (!days.length) return null;
    if (filter && /^\d{4}-\d{2}-\d{2}$/.test(filter) && days.includes(filter)) return filter;
    return days[days.length - 1];
  }

  /* ── week helpers (ISO weeks, Mon–Sun) ───────────────────────── */
  function addDays(dstr, n) {
    const d = new Date(`${dstr}T00:00:00Z`);
    d.setUTCDate(d.getUTCDate() + n);
    return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
  }
  function mondayOf(dstr) {
    const d = new Date(`${dstr}T00:00:00Z`);
    return addDays(dstr, -((d.getUTCDay() + 6) % 7));
  }
  function weekLabel(monday) {
    const end = addDays(monday, 6);
    const MON = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const [, m0, d0] = monday.split('-').map(Number);
    const [, m1, d1] = end.split('-').map(Number);
    return m0 === m1 ? `${d0}–${d1} ${MON[m1]}` : `${d0} ${MON[m0]} – ${d1} ${MON[m1]}`;
  }
  function weeksMap() {  // monday -> sorted [dates with data]
    const map = {};
    for (const d of sortedDays()) (map[mondayOf(d)] = map[mondayOf(d)] || []).push(d);
    return map;
  }
  function weekAgg(dates) {
    const days = dates.map(k => state.data.days[k]);
    const sum = f => days.reduce((a, d) => a + (f(d) || 0), 0);
    const actual = sum(d => d.actual_h), effective = sum(d => d.effective_h);
    const equiv = sum(d => d.human_equiv_h != null ? d.human_equiv_h : d.effective_h);
    const lanes = {};
    for (const d of days) for (const [k, v] of Object.entries(d.lane_hours || {})) lanes[k] = (lanes[k] || 0) + v;
    return {
      days, dates, actual_h: actual, effective_h: effective, human_equiv_h: equiv,
      attention_h: sum(d => d.attention_h), meeting_h: sum(d => d.meeting_h), ai_h: sum(d => d.ai_h),
      sessions: sum(d => d.sessions), meetings_count: sum(d => d.meetings_count),
      lane_hours: lanes, ai_speed: (days[days.length - 1] || {}).ai_speed || 1,
      leverage: actual ? Math.round(effective / actual * 100) / 100 : 0,
      output_x: actual ? Math.round(equiv / actual * 100) / 100 : 0,
    };
  }

  /* ── KPI tiles ───────────────────────────────────────────────── */
  function kpiRow(day, days) {
    const isToday = day.date === todayWorkday();
    const prev7 = days.filter(d => d.date < day.date).slice(-7);
    const outX = d => d.output_x || d.leverage || 0;
    const avgOut = prev7.length
      ? prev7.reduce((a, d) => a + outX(d), 0) / prev7.length : null;
    const tiles = [
      Comp.statTile({
        key: 'wh-start', icon: '🌅', label: 'Started', value: day.start,
        sub: 'first activity',
      }),
      Comp.statTile({
        key: 'wh-end', icon: isToday ? '🟢' : '🌙', label: isToday ? 'Running' : 'Ended',
        value: day.end, sub: isToday ? 'still on the clock' : 'last activity',
      }),
      Comp.statTile({
        key: 'wh-actual', icon: '⏱', label: 'Actual hours', value: hFmt(day.actual_h),
        sub: `hands-on ≥ ${hFmt(day.attention_h)} · wall ${hFmt(day.wall_h)}`,
      }),
      Comp.statTile({
        key: 'wh-effective', icon: '🔀', label: 'Parallel output', value: hFmt(day.effective_h),
        sub: `${day.sessions} AI streams + ${day.meetings_count} meetings`,
      }),
      Comp.statTile({
        key: 'wh-leverage', icon: '🚀', label: 'Productivity', value: `${outX(day)}×`,
        sub: `≈ ${hFmt(day.human_equiv_h != null ? day.human_equiv_h : day.effective_h)} manual solo · parallel ${day.leverage}× · AI ×${day.ai_speed || 1} assumed${avgOut ? ` · 7d avg ${avgOut.toFixed(1)}×` : ''}`,
        status: avgOut && outX(day) >= avgOut ? 'good' : null,
      }),
    ];
    return `<div class="hero-row">${tiles.join('')}</div>`;
  }

  /* ── freshness label ─────────────────────────────────────────────
     The sweep froze on 9 Aug 2026 and the tab kept rendering the frozen file
     in muted grey, so nothing on screen said the numbers were three weeks old.
     Past STALE_H the label turns into a warning that names the fix. */
  const STALE_H = 6;

  function sweepAgeH() {
    if (!state.data || !state.data.last_sweep_wib) return null;
    const t = new Date(state.data.last_sweep_wib).getTime();
    if (!isFinite(t)) return null;
    return Math.max(0, (Date.now() - t) / 3600000);
  }

  function updatedLabel() {
    const err = state.data && state.data.refresh_error;
    const age = sweepAgeH();
    if (age === null) return '<span class="hours-updated is-stale">never swept</span>';
    const txt = `updated ${U.fmtAge(age)} ago`;
    if (err) {
      return `<span class="hours-updated is-stale" title="${U.esc(String(err))}">${U.esc(txt)} · auto-refresh failing</span>`;
    }
    if (age > STALE_H) {
      return `<span class="hours-updated is-stale" title="python3 .agent/skills/work-hours/scripts/work_hours.py sweep --backfill 14">${U.esc(txt)} · stale, run a sweep</span>`;
    }
    return `<span class="hours-updated">${U.esc(txt)}</span>`;
  }

  /* ── day selector chips ──────────────────────────────────────── */
  function dayChips(sel) {
    const days = sortedDays();
    const i = days.indexOf(sel);
    const d = state.data.days[sel];
    const prev = i > 0 ? days[i - 1] : null;
    const next = i < days.length - 1 ? days[i + 1] : null;
    const latest = days[days.length - 1];
    const upd = updatedLabel();
    return `<div class="chips hours-daynav">
      <button class="chip hours-nav-btn" data-goto="${prev ? U.esc(prev) : ''}" ${prev ? '' : 'disabled'}>‹</button>
      <span class="chip is-active">${U.esc(niceDate(sel, d && d.weekday))}</span>
      <button class="chip hours-nav-btn" data-goto="${next ? U.esc(next) : ''}" ${next ? '' : 'disabled'}>›</button>
      ${sel !== latest ? `<button class="chip hours-nav-btn" data-goto="${U.esc(latest)}">today</button>` : ''}
      <button class="chip hours-nav-btn" data-nav="hours/week/${U.esc(mondayOf(sel))}">📅 week view</button>
      ${upd}
    </div>`;
  }

  /* ── week selector chips ─────────────────────────────────────── */
  function weekChips(mondays, sel) {
    const i = mondays.indexOf(sel);
    const prev = i > 0 ? mondays[i - 1] : null;
    const next = i < mondays.length - 1 ? mondays[i + 1] : null;
    const latest = mondays[mondays.length - 1];
    const upd = updatedLabel();
    return `<div class="chips hours-daynav">
      <button class="chip hours-nav-btn" data-nav="${prev ? `hours/week/${U.esc(prev)}` : ''}" ${prev ? '' : 'disabled'}>‹</button>
      <span class="chip is-active">Week of ${U.esc(weekLabel(sel))}</span>
      <button class="chip hours-nav-btn" data-nav="${next ? `hours/week/${U.esc(next)}` : ''}" ${next ? '' : 'disabled'}>›</button>
      ${sel !== latest ? `<button class="chip hours-nav-btn" data-nav="hours/week/${U.esc(latest)}">this week</button>` : ''}
      <button class="chip hours-nav-btn" data-nav="hours">📆 day view</button>
      ${upd}
    </div>`;
  }

  /* ── week KPI tiles ──────────────────────────────────────────── */
  function weekKpiRow(agg, sel, mondays) {
    const isCurrent = mondayOf(todayWorkday()) === sel;
    const i = mondays.indexOf(sel);
    const prevAgg = i > 0 ? weekAgg(weeksMap()[mondays[i - 1]]) : null;
    const delta = (cur, prv) => (prv && prv > 0)
      ? ` · prev wk ${hFmt(prv)}` : '';
    const tiles = [
      Comp.statTile({
        key: 'wkh-days', icon: '📅', label: 'Days worked',
        value: String(agg.dates.length), sub: `${weekLabel(sel)}${isCurrent ? ' · so far' : ''}`,
      }),
      Comp.statTile({
        key: 'wkh-actual', icon: '⏱', label: 'Actual hours', value: hFmt(agg.actual_h),
        sub: `avg ${hFmt(agg.actual_h / Math.max(agg.dates.length, 1))}/day · hands-on ≥ ${hFmt(agg.attention_h)}${delta(agg.actual_h, prevAgg && prevAgg.actual_h)}`,
      }),
      Comp.statTile({
        key: 'wkh-eff', icon: '🔀', label: 'Parallel output', value: hFmt(agg.effective_h),
        sub: `${agg.sessions} AI streams + ${agg.meetings_count} meetings · leverage ${agg.leverage}×`,
      }),
      Comp.statTile({
        key: 'wkh-prod', icon: '🚀', label: 'Productivity', value: `${agg.output_x}×`,
        sub: `≈ ${hFmt(agg.human_equiv_h)} manual solo · AI ×${agg.ai_speed} assumed${prevAgg ? ` · prev wk ${prevAgg.output_x}×` : ''}`,
        status: prevAgg && agg.output_x >= prevAgg.output_x ? 'good' : null,
      }),
      Comp.statTile({
        key: 'wkh-meet', icon: '🎥', label: 'Meetings', value: hFmt(agg.meeting_h),
        sub: `${agg.meetings_count} meetings · ${agg.actual_h ? Math.round(agg.meeting_h / agg.actual_h * 100) : 0}% of actual`,
      }),
    ];
    return `<div class="hero-row">${tiles.join('')}</div>`;
  }

  /* ── timeline (gantt) ────────────────────────────────────────── */
  function stackLane(streams) {
    /* greedy stacking: overlapping streams get their own sub-row */
    const rows = [];
    const sorted = [...streams].sort((a, b) => a.blocks[0][0] - b.blocks[0][0]);
    for (const s of sorted) {
      const start = s.blocks[0][0];
      const end = s.blocks[s.blocks.length - 1][1];
      let row = rows.find(r => r.end <= start);
      if (!row) { row = { end: 0, items: [] }; rows.push(row); }
      row.items.push(s);
      row.end = Math.max(row.end, end);
    }
    return rows.map(r => r.items);
  }

  function timeline(day) {
    const streams = day.streams || [];
    if (!streams.length) return Comp.emptyState({ icon: '⏱', title: 'No activity recorded' });
    const isToday = day.date === todayWorkday();
    const nowMin = wibNowMin() < 4 * 60 ? wibNowMin() + 1440 : wibNowMin();  // past-midnight -> same workday axis

    let lo = Math.min(...streams.map(s => s.blocks[0][0]));
    let hi = Math.max(...streams.map(s => s.blocks[s.blocks.length - 1][1]));
    if (isToday) hi = Math.max(hi, Math.min(nowMin + 20, 28 * 60));
    lo = Math.floor(lo / 60) * 60;
    hi = Math.ceil(hi / 60) * 60;
    if (hi - lo < 6 * 60) hi = lo + 6 * 60;
    const span = hi - lo;
    const pct = m => ((m - lo) / span * 100).toFixed(2);
    const hourStep = span > 13 * 60 ? 120 : 60;

    /* hour ruler + vertical hairlines */
    let ticks = '';
    for (let m = lo; m <= hi; m += hourStep) {
      const last = m === hi;
      ticks += `<span class="hours-tick${last ? ' is-last' : ''}" style="--l:${pct(m)}%">${pad(Math.floor(m / 60) % 24)}</span>`;
    }
    let grid = '';
    for (let m = lo; m <= hi; m += 60) {
      grid += `<span class="hours-vline" style="--l:${pct(m)}%"></span>`;
    }
    const showNow = isToday && nowMin >= lo && nowMin <= hi;
    const nowLine = showNow ? `<span class="hours-nowline" style="--l:${pct(nowMin)}%"></span>` : '';
    const nowLineLabeled = showNow
      ? `<span class="hours-nowline" style="--l:${pct(nowMin)}%"><i>now</i></span>` : '';

    /* lane rows */
    let rows = '';
    for (const lane of LANES) {
      const laneStreams = streams.filter(s => s.lane === lane.key);
      if (!laneStreams.length) continue;
      const subs = stackLane(laneStreams);
      const laneCommits = (day.commits || []).filter(c =>
        (lane.key === 'work' && c.repo === 'product-second-brain') ||
        (lane.key === 'you' && c.repo !== 'product-second-brain'));
      subs.forEach((items, si) => {
        let blocks = '';
        for (const s of items) {
          for (const [b0, b1] of s.blocks) {
            const w = Math.max((b1 - b0) / span * 100, 0.35);
            blocks += `<button class="hours-block${s.source === 'scheduled' ? ' is-scheduled' : ''}"
              data-lane="${lane.key}" style="--l:${pct(b0)}%;--w:${w.toFixed(2)}%"
              data-tip-title="${U.esc(s.label)}"
              data-tip-meta="${U.esc(`${lane.name} · ${mClock(b0)}–${mClock(b1)} · ${mDur(b1 - b0)}`)}"
              data-tip-sub="${U.esc(tipSub(s))}"></button>`;
          }
        }
        let marks = '';
        if (si === 0) {
          for (const c of laneCommits) {
            if (c.min < lo || c.min > hi) continue;
            marks += `<button class="hours-commit" style="--l:${pct(c.min)}%"
              data-tip-title="${U.esc(c.msg)}"
              data-tip-meta="${U.esc(`git commit · ${c.repo} · ${mClock(c.min)}`)}"
              data-tip-sub=""></button>`;
          }
        }
        rows += `<div class="hours-row">
          <div class="hours-lane-label">${si === 0
            ? `<span class="hours-key" data-lane="${lane.key}"></span>${U.esc(lane.name)}`
            : ''}</div>
          <div class="hours-track">${grid}${blocks}${marks}${nowLine}</div>
        </div>`;
      });
    }

    const legend = LANES.map(l =>
      `<span class="hours-legend-item"><span class="hours-key" data-lane="${l.key}"></span>${U.esc(l.name)} <em>${hFmt((day.lane_hours || {})[l.key] || 0)}</em></span>`
    ).join('');

    return `<div class="hours-legend">${legend}
        <span class="hours-legend-note">◆ git commit · faded block = scheduled, attendance not verified</span></div>
      <div class="hours-gantt">
        <div class="hours-row hours-ruler"><div class="hours-lane-label"></div>
          <div class="hours-track">${ticks}${nowLineLabeled}</div></div>
        ${rows}
      </div>`;
  }

  function tipSub(s) {
    if (s.kind === 'meeting') {
      return s.source === 'recorded' ? 'recorded — attendance verified'
        : 'from calendar — attendance not verified';
    }
    const msgs = s.human_msgs ? `${s.human_msgs} prompts from the owner · ` : '';
    return `${msgs}session total ${mDur(s.minutes)}`;
  }

  /* ── grouped-bar trend (effective = accent, actual = context).
     Renders days by default; opts.weekBars renders week aggregates whose
     hit-targets navigate to week view instead of day view. ── */
  function trendCard(days, sel, opts) {
    opts = opts || {};
    const ds = days.slice(-(opts.max || 14));
    if (ds.length < (opts.minBars != null ? opts.minBars : 2)) return '';
    const W = 720, H = 170, padL = 34, padR = 10, padT = 26, padB = 22;
    const plotW = W - padL - padR, plotH = H - padT - padB;
    const maxV = Math.max(...ds.map(d => d.effective_h), 4);
    const yMax = Math.ceil(maxV / 4) * 4;
    const y = v => padT + plotH - (v / yMax) * plotH;
    const slot = plotW / ds.length;
    const barW = Math.min(14, (slot - 8) / 2);

    let gridLines = '', yLabels = '';
    for (let v = 0; v <= yMax; v += yMax / 4) {
      gridLines += `<line class="hours-grid" x1="${padL}" y1="${y(v).toFixed(1)}" x2="${W - padR}" y2="${y(v).toFixed(1)}"/>`;
      yLabels += `<text class="hours-axis" x="${padL - 6}" y="${(y(v) + 3.5).toFixed(1)}" text-anchor="end">${v}</text>`;
    }

    const topRect = (x, yv, w, h, r) => {
      if (h <= 0.5) return '';
      r = Math.min(r, w / 2, h);
      return `M${x},${yv + h} L${x},${yv + r} Q${x},${yv} ${x + r},${yv} L${x + w - r},${yv} Q${x + w},${yv} ${x + w},${yv + r} L${x + w},${yv + h} Z`;
    };

    let bars = '', hits = '', xLabels = '', levLabel = '';
    ds.forEach((d, i) => {
      const cx = padL + slot * i + slot / 2;
      const xA = cx - barW - 1, xE = cx + 1;
      /* clamp tiny nonzero values to a visible sliver (never vanish) */
      const hA = d.actual_h > 0 ? Math.max(plotH * d.actual_h / yMax, 2) : 0;
      const hE = d.effective_h > 0 ? Math.max(plotH * d.effective_h / yMax, 2) : 0;
      const selCls = d.date === sel ? ' is-selected' : '';
      bars += `<g class="hours-bargroup${selCls}" data-date="${U.esc(d.date)}">
        <path class="hours-bar-actual" d="${topRect(xA, padT + plotH - hA, barW, hA, 3)}"/>
        <path class="hours-bar-eff" d="${topRect(xE, padT + plotH - hE, barW, hE, 3)}"/>
      </g>`;
      const equivNote = d.human_equiv_h != null ? ` · ≈ ${hFmt(d.human_equiv_h)} manual (AI ×${d.ai_speed || 1})` : '';
      const navAttr = opts.weekBars ? `data-week="${U.esc(d.date)}"` : `data-date="${U.esc(d.date)}"`;
      const tipTitle = opts.weekBars
        ? `Week of ${weekLabel(d.date)} — ${d.output_x || d.leverage}× productivity`
        : `${niceDate(d.date, d.weekday)} — ${d.output_x || d.leverage}× productivity`;
      const tipSubTxt = opts.weekBars
        ? `meetings ${hFmt(d.meeting_h)} · AI ${hFmt(d.ai_h)} · ${d.daysWorked} days worked`
        : `meetings ${hFmt(d.meeting_h)} · AI ${hFmt(d.ai_h)} · ${d.start}–${d.end}`;
      hits += `<rect class="hours-hit" tabindex="0" role="button" ${navAttr} x="${(padL + slot * i).toFixed(1)}" y="0" width="${slot.toFixed(1)}" height="${H - padB + 4}"
        aria-label="${U.esc(`Inspect ${d.date}`)}"
        data-tip-title="${U.esc(tipTitle)}"
        data-tip-meta="${U.esc(`actual ${hFmt(d.actual_h)} → parallel ${hFmt(d.effective_h)}${equivNote}`)}"
        data-tip-sub="${U.esc(tipSubTxt)}"/>`;
      const dayNum = Number(d.date.slice(8, 10));
      xLabels += `<text class="hours-axis${d.date === sel ? ' is-selected' : ''}" x="${cx.toFixed(1)}" y="${H - 7}" text-anchor="middle">${dayNum}</text>`;
      if (i === ds.length - 1) {
        levLabel = `<text class="hours-levlabel" x="${(xE + barW / 2).toFixed(1)}" y="${(padT + plotH - hE - 7).toFixed(1)}" text-anchor="middle">${d.leverage}×</text>`;
      }
    });

    const avg = ds.reduce((a, d) => a + d.leverage, 0) / ds.length;
    const unit = opts.weekBars ? 'week' : 'day';
    const body = `
      <div class="hours-legend">
        <span class="hours-legend-item"><span class="hours-key" data-series="eff"></span>Parallel output</span>
        <span class="hours-legend-item"><span class="hours-key" data-series="act"></span>Actual hours</span>
        <span class="hours-legend-note">click a ${unit} to inspect it · avg leverage ${avg.toFixed(1)}×</span>
      </div>
      <div class="hours-trend-wrap"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Actual vs parallel hours, last ${ds.length} ${unit}s">
        ${gridLines}${yLabels}${bars}${levLabel}${xLabels}${hits}
      </svg></div>`;
    return Comp.card({
      key: opts.key || 'hours-trend', icon: '📈', title: opts.title || 'Leverage trend',
      count: `${ds.length}${opts.weekBars ? 'w' : 'd'}`, body, open: true,
    });
  }

  /* ── streams table (the chart's accessible twin) ─────────────── */
  function tableCard(day) {
    const rows = (day.streams || []).map(s => {
      const lane = LANE_BY_KEY[s.lane] || LANE_BY_KEY.other;
      const b0 = s.blocks[0][0], b1 = s.blocks[s.blocks.length - 1][1];
      return `<tr>
        <td class="num">${mClock(b0)}–${mClock(b1)}</td>
        <td>${Comp.badge(lane.cat, lane.name)}</td>
        <td class="hours-td-label" title="${U.esc(s.label)}">${s.kind === 'meeting' ? '🎥 ' : '🤖 '}${U.esc(s.label)}</td>
        <td class="num">${mDur(s.minutes)}</td>
      </tr>`;
    }).join('');
    const body = `<div class="hours-table-wrap"><table class="hours-table">
      <thead><tr><th>Time</th><th>Lane</th><th>Stream</th><th>Active</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
      <div class="hours-footnote">Workday boundary 04:00 WIB · AI streams from Claude Code transcripts
      (gaps > 15 min split a block) · ${day.automated_runs || 0} automated cron runs excluded ·
      actual = union of all streams, parallel output = sum of streams ·
      productivity = (meetings + AI hours × ${day.ai_speed || 1}) ÷ actual — the ×${day.ai_speed || 1}
      AI-speed factor is an assumption, tune it via sweep --ai-speed.</div>`;
    return Comp.card({
      key: 'hours-streams', icon: '🧵', title: 'Streams detail',
      count: String((day.streams || []).length), body, open: false,
    });
  }

  /* ── week: per-day table + lane split ────────────────────────── */
  function weekTableCard(agg) {
    const rows = agg.days.map(d => `<tr class="hours-day-link" data-date="${U.esc(d.date)}" tabindex="0" role="button" aria-label="${U.esc(`Open ${d.date}`)}">
        <td>${U.esc(niceDate(d.date, d.weekday))}</td>
        <td class="num">${U.esc(d.start)}–${U.esc(d.end)}</td>
        <td class="num">${hFmt(d.actual_h)}</td>
        <td class="num">${hFmt(d.effective_h)}</td>
        <td class="num">${d.leverage}×</td>
        <td class="num">${d.output_x || d.leverage}×</td>
        <td class="num">${hFmt(d.meeting_h)}</td>
      </tr>`).join('');
    const laneSplit = LANES
      .filter(l => (agg.lane_hours[l.key] || 0) > 0.05)
      .map(l => `<span class="hours-legend-item"><span class="hours-key" data-lane="${l.key}"></span>${U.esc(l.name)} <em>${hFmt(agg.lane_hours[l.key])}</em></span>`)
      .join('');
    const body = `<div class="hours-table-wrap"><table class="hours-table">
      <thead><tr><th>Day</th><th>Span</th><th>Actual</th><th>Parallel</th><th>Leverage</th><th>Productivity</th><th>Meetings</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
      <div class="hours-legend" style="margin-top:8px">${laneSplit}</div>
      <div class="hours-footnote">Click a day to open its timeline · totals are sums over the week's workdays ·
      weekly leverage and productivity are recomputed from the sums, not averaged per day.</div>`;
    return Comp.card({
      key: 'hours-week-days', icon: '🗓', title: 'Days in this week',
      count: String(agg.dates.length), body, open: true,
    });
  }

  /* ── methodology & sources (the "why should I trust these numbers" card) ── */
  function methodologyCard(day) {
    const f = day.ai_speed || 1;
    const body = `
      <div class="hours-method">
        <h4>How the numbers are computed</h4>
        <ul>
          <li><strong>Actual hours</strong> — union of every active minute across all streams (overlaps counted once). Signals: Claude Code transcript timestamps (interactive sessions only, cron/automation excluded), attended meetings, git commits. Gaps &gt; 15 min split a block; workday boundary is 04:00 WIB.</li>
          <li><strong>Parallel output</strong> — sum of per-stream hours, so 3 streams running one hour = 3h. <strong>Leverage</strong> = parallel ÷ actual. Both are <em>measured</em>, no assumptions.</li>
          <li><strong>Productivity</strong> = (meetings ×1 + AI hours ×${U.esc(String(f))}) ÷ actual. The ×${U.esc(String(f))} AI-speed factor converts AI-stream hours into estimated manual-solo hours. It is an <em>estimate calibrated from research</em>, not a measurement.</li>
          <li><strong>Hands-on</strong> — minutes around the owner's own prompts + meetings, bounded by actual. The honest ladder: hands-on ≤ actual ≤ parallel.</li>
        </ul>
        <h4>Where the ×${U.esc(String(f))} factor comes from (deep-research run, 16 Jul 2026)</h4>
        <p>No study directly measures "manual hours substituted per autonomous agent-hour" yet; the evidence brackets it between a peer-reviewed net floor (~1.1× per task after review/rework) and a vendor-reported gross ceiling (~8–9×). Triangulated per category: drafting/comms/synthesis ×2.5–3 · research ×2–3 · coding ×1–2. Blended for this PM mix: <strong>×2–2.5 conservative, ×3 optimistic edge</strong> — default set to ×2.5. Key verified sources:</p>
        <ul class="hours-method-sources">
          <li><a href="https://www.science.org/doi/10.1126/science.adh2586" target="_blank" rel="noopener">Noy &amp; Zhang 2023 (Science, RCT n=453)</a> — pro writing 40% faster, +18% quality with AI.
            <span class="hours-method-url">science.org/doi/10.1126/science.adh2586</span></li>
          <li><a href="https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4573321" target="_blank" rel="noopener">Dell'Acqua et al. 2023 (BCG, RCT n=758)</a> — consultants +25% speed in-frontier; net-negative outside it.
            <span class="hours-method-url">papers.ssrn.com/sol3/papers.cfm?abstract_id=4573321</span></li>
          <li><a href="https://arxiv.org/pdf/2304.11771" target="_blank" rel="noopener">Brynjolfsson et al. 2023 (QJE, n=5,172)</a> — support agents +15% resolutions/hour.
            <span class="hours-method-url">arxiv.org/pdf/2304.11771</span></li>
          <li><a href="https://arxiv.org/pdf/2503.18238" target="_blank" rel="noopener">Ju &amp; Aral 2026 (MIT, preregistered RCT n=2,234)</a> — AI teammate: ~1.5× output per human, real-world quality ~constant for text.
            <span class="hours-method-url">arxiv.org/pdf/2503.18238</span></li>
          <li><a href="https://metr.org/blog/2026-02-24-uplift-update/" target="_blank" rel="noopener">METR 2025–26 RCT</a> — counter-evidence: experienced devs 19% <em>slower</em> with AI on familiar repos; why coding gets ×1–2.
            <span class="hours-method-url">metr.org/blog/2026-02-24-uplift-update</span></li>
          <li><a href="https://arxiv.org/html/2510.04374v1" target="_blank" rel="noopener">OpenAI GDPval 2025</a> — with review/rework counted, net per-task gain shrinks to ~1.1×.
            <span class="hours-method-url">arxiv.org/html/2510.04374v1</span></li>
          <li><a href="https://arxiv.org/abs/2606.07489" target="_blank" rel="noopener">Perplexity telemetry 2026 (n=10k pairs)</a> — gross ceiling 8–9×; vendor-affiliated, estimated baselines, treated as ceiling only.
            <span class="hours-method-url">arxiv.org/abs/2606.07489</span></li>
        </ul>
        <p class="hours-method-note">Every claim above survived 3-voter adversarial verification against its primary source; two popular numbers (Perplexity "87% = 7.5×", GDPval win-rate rework ceiling) were refuted and excluded.
        Full reasoning chain: <a class="doc-link" data-drawer-path=".agent/skills/work-hours/research_ai_speed_factor.md" data-drawer-title="AI-speed factor — research report">open the full research report</a> (reads in a side panel).
        Factor is model-era specific — revisit quarterly. Tune per sweep: <code>--ai-speed N</code>.</p>
      </div>`;
    return Comp.card({
      key: 'hours-method', icon: '📚', title: 'Methodology & sources',
      count: `AI ×${f}`, body, open: false,
    });
  }

  /* ── tooltip (shared, textContent only — labels are untrusted) ── */
  function ensureTip(panel) {
    let tip = panel.querySelector('.hours-tip');
    if (!tip) {
      tip = document.createElement('div');
      tip.className = 'hours-tip';
      tip.setAttribute('hidden', '');
      tip.innerHTML = '<div class="hours-tip-title"></div><div class="hours-tip-meta"></div><div class="hours-tip-sub"></div>';
      panel.appendChild(tip);
    }
    return tip;
  }

  function wireEvents(panel) {
    const tip = ensureTip(panel);
    const show = (el, x, y) => {
      tip.querySelector('.hours-tip-title').textContent = el.dataset.tipTitle || '';
      tip.querySelector('.hours-tip-meta').textContent = el.dataset.tipMeta || '';
      const sub = el.dataset.tipSub || '';
      const subEl = tip.querySelector('.hours-tip-sub');
      subEl.textContent = sub;
      subEl.toggleAttribute('hidden', !sub);
      tip.removeAttribute('hidden');
      const pr = panel.getBoundingClientRect();
      const tw = tip.offsetWidth, th = tip.offsetHeight;
      let left = x - pr.left + 14, top = y - pr.top - th - 10;
      left = Math.max(4, Math.min(left, pr.width - tw - 8));
      if (top < 4) top = y - pr.top + 18;
      tip.style.setProperty('--tx', `${Math.round(left)}px`);
      tip.style.setProperty('--ty', `${Math.round(top)}px`);
    };
    const hide = () => tip.setAttribute('hidden', '');

    panel.addEventListener('pointerover', e => {
      const el = e.target.closest('[data-tip-title]');
      if (el) show(el, e.clientX, e.clientY);
    });
    panel.addEventListener('pointermove', e => {
      const el = e.target.closest('[data-tip-title]');
      if (el) show(el, e.clientX, e.clientY); else hide();
    });
    panel.addEventListener('pointerleave', hide);
    panel.addEventListener('focusin', e => {
      const el = e.target.closest('[data-tip-title]');
      if (el) {
        const r = el.getBoundingClientRect();
        show(el, r.left + r.width / 2, r.top);
      }
    });
    panel.addEventListener('focusout', hide);
    const navigate = el => {
      if (!el) return false;
      if (el.dataset.nav) { location.hash = el.dataset.nav; return true; }
      if (el.dataset.week) { location.hash = `hours/week/${el.dataset.week}`; return true; }
      if (el.dataset.date && !el.classList.contains('is-active')) { location.hash = `hours/${el.dataset.date}`; return true; }
      if (el.dataset.goto) { location.hash = `hours/${el.dataset.goto}`; return true; }
      return false;
    };
    panel.addEventListener('click', e => {
      navigate(e.target.closest('.hours-nav-btn, .hours-hit, .hours-day-link'));
    });
    panel.addEventListener('keydown', e => {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      const el = e.target.closest && e.target.closest('.hours-hit, .hours-day-link');
      if (el && navigate(el)) e.preventDefault();
    });
  }

  /* ── render: week view ───────────────────────────────────────── */
  function renderWeek(panel, mondayFilter) {
    const wm = weeksMap();
    const mondays = Object.keys(wm).sort();
    if (!mondays.length) return false;
    const sel = (mondayFilter && wm[mondayFilter]) ? mondayFilter : mondays[mondays.length - 1];
    const agg = weekAgg(wm[sel]);
    const weekSeries = mondays.map(m => {
      const a = weekAgg(wm[m]);
      return {
        date: m, actual_h: a.actual_h, effective_h: a.effective_h, leverage: a.leverage,
        output_x: a.output_x, human_equiv_h: a.human_equiv_h, ai_speed: a.ai_speed,
        meeting_h: a.meeting_h, ai_h: a.ai_h, daysWorked: a.dates.length,
      };
    });
    panel.innerHTML = `
      ${weekChips(mondays, sel)}
      ${weekKpiRow(agg, sel, mondays)}
      ${trendCard(agg.days, null, { key: 'hours-week-daily', title: `Daily breakdown — week of ${weekLabel(sel)}`, minBars: 1 })}
      ${trendCard(weekSeries, sel, { key: 'hours-week-trend', title: 'Weekly trend', weekBars: true, max: 12, minBars: 2 })}
      ${weekTableCard(agg)}
      ${methodologyCard(agg)}`;
    wireEvents(panel);
    return true;
  }

  /* ── render ──────────────────────────────────────────────────── */
  function render(filter) {
    const panel = document.getElementById('tab-hours');
    if (!panel) return;
    if (state.err && !state.data) {
      panel.innerHTML = `<div class="load-error">Work hours unavailable: ${U.esc(state.err)}</div>`;
      return;
    }
    if (!state.data) return;
    const wk = /^week(?:\/(\d{4}-\d{2}-\d{2}))?$/.exec(filter || '');
    if (wk && renderWeek(panel, wk[1])) return;
    const sel = currentDate(filter);
    if (!sel) {
      panel.innerHTML = Comp.emptyState({
        icon: '⏱', title: 'No work-hours data yet',
        hint: 'python3 .agent/skills/work-hours/scripts/work_hours.py sweep --backfill 14',
      });
      return;
    }
    const daysArr = sortedDays().map(k => state.data.days[k]);
    const day = state.data.days[sel];
    panel.innerHTML = `
      ${dayChips(sel)}
      ${kpiRow(day, daysArr)}
      ${Comp.card({
        key: 'hours-timeline', icon: '🗓', title: `Timeline — ${niceDate(sel, day.weekday)}`,
        count: `${(day.streams || []).length} streams`, body: timeline(day), open: true,
      })}
      ${trendCard(daysArr, sel)}
      ${tableCard(day)}
      ${methodologyCard(day)}`;
    wireEvents(panel);
  }

  window.Tabs.hours = {
    load(filter) {
      const panel = document.getElementById('tab-hours');
      if (panel && !state.loaded) {
        panel.innerHTML = '<div class="skeleton"><div class="skeleton-line"></div><div class="skeleton-line w-80"></div><div class="skeleton-line w-60"></div></div>';
      }
      U.fetchJSON('/api/work-hours')
        .then(d => { state.data = d; state.err = null; state.loaded = true; render(filter); })
        .catch(err => {
          state.err = err.message;
          state.loaded = true;
          if (!state.data) render(filter);  // refetch keeps the previous frame
        });
    },
  };
})();
