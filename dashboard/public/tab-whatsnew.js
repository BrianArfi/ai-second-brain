/* ═══════════════════════════════════════════════════════════════════
   tab-whatsnew.js: ✨ What's new tab.
   Renders CHANGELOG.md from /api/changelog: the Unreleased section and the
   newest release open, every older section collapsed. Section bodies go
   through U.mdToHtml, which HTML-escapes the text before formatting it, so
   nothing in the changelog can inject markup. The server already dropped
   HTML comments and flattened links to repository files
   (dashboard/changelog.py).
   ═══════════════════════════════════════════════════════════════════ */
'use strict';
window.Tabs = window.Tabs || {};

(function () {
  const state = { data: null, err: null };

  function sectionCard(s, open) {
    const label = s.unreleased ? 'Unreleased'
      : s.version ? `v${s.version}` : s.title;
    const count = s.unreleased ? 'not released yet' : (s.date || '');
    const body = s.body
      ? `<div class="md">${U.mdToHtml(s.body)}</div>`
      : Comp.emptyState({ icon: '📝', title: 'Nothing here yet', hint: '' });
    return Comp.card({
      key: `whatsnew:${s.title}`,
      icon: s.unreleased ? '🚧' : s.version ? '🏷' : '🗓',
      title: label,
      count,
      body,
      open,
    });
  }

  function render() {
    const panel = document.getElementById('tab-whatsnew');
    if (!panel) return;
    if (state.err) {
      panel.innerHTML = `<div class="load-error">Changelog unavailable: ${U.esc(state.err)}</div>`;
      return;
    }
    const d = state.data;
    if (!d) {
      panel.innerHTML = '<div class="skeleton"><div class="skeleton-line"></div><div class="skeleton-line w-80"></div></div>';
      return;
    }
    if (d.missing || !(d.sections || []).length) {
      panel.innerHTML = Comp.emptyState({
        icon: '📄', title: 'No changelog found',
        hint: 'Add CHANGELOG.md at the repository root and it shows here.',
      });
      return;
    }
    const parts = [];
    parts.push(`<div class="whatsnew-head"><h2>What's new</h2>` +
      (d.latest ? `<p>Current release: <strong>v${U.esc(d.latest)}</strong></p>` : '') +
      `</div>`);
    if (d.intro) parts.push(`<div class="md whatsnew-intro">${U.mdToHtml(d.intro)}</div>`);
    let openedRelease = false;
    for (const s of d.sections) {
      let open = false;
      if (s.unreleased && s.body) open = true;
      else if (s.version && !openedRelease) { open = true; openedRelease = true; }
      parts.push(sectionCard(s, open));
    }
    panel.innerHTML = parts.join('\n');
  }

  window.Tabs.whatsnew = {
    load() {
      render();
      U.fetchJSON('/api/changelog')
        .then(d => { state.data = d; state.err = null; render(); })
        .catch(err => { state.err = err.message; render(); });
    },
  };
})();
