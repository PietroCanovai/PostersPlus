import { html, useState, useEffect, useMemo } from './vendor/preact-htm.js';
import { api, toast, n, go, nav } from './common.js';

const FILTERS = [
  ['all', 'Everything'],
  ['customised', 'Customised'],
  ['rotation', 'Rotating'],
  ['pinned', 'Pinned'],
  ['never', 'Has “Never” images'],
  ['hands_off', 'Hands off'],
  ['unreviewed', 'Not reviewed yet'],
  ['attention', 'Needs attention'],
  ['left_alone', 'Left alone'],
];

function matches(it, filter) {
  switch (filter) {
    case 'customised': return it.mode !== 'auto' || it.never || it.styled || it.hands_off;
    case 'rotation': return it.mode === 'rotation' && it.rotation > 0;
    case 'pinned': return it.mode === 'pinned';
    case 'never': return it.never > 0;
    case 'hands_off': return it.hands_off;
    case 'unreviewed': return !it.reviewed && it.status !== 'left_alone';
    case 'attention': return it.status === 'needs_match' || it.status === 'error';
    case 'left_alone': return it.status === 'left_alone';
    default: return true;
  }
}

export function chipsFor(it) {
  const c = [];
  if (it.status === 'needs_match') c.push(['Needs match', 'warn']);
  if (it.status === 'error') c.push(['Error', 'bad']);
  if (it.status === 'left_alone') c.push(['Left alone', '']);
  if (it.hands_off) c.push(['Hands off', '']);
  if (it.mode === 'pinned') c.push(['Pinned', 'info']);
  if (it.mode === 'rotation' && it.rotation) c.push([`Rotating ×${it.rotation}`, 'info']);
  if (it.never) c.push([`Never ×${it.never}`, 'bad']);
  if (it.styled) c.push(['Styled', 'ok']);
  return c;
}

function Tile({ it, onOpen }) {
  const chips = chipsFor(it);
  return html`<button type="button" class="tile" onClick=${onOpen} title=${it.name}>
    <span class="tile-img">
      <img loading="lazy" alt="" src=${`/studio/api/thumb/${it.jf_id}?h=360&tag=${encodeURIComponent(it.jf_image_tag || '')}`}
        onError=${e => { e.target.style.visibility = 'hidden'; }} />
      ${chips.length > 0 && html`<span class="tile-chips">${chips.slice(0, 3).map(([l, c]) => html`<span class="chip ${c}">${l}</span>`)}</span>`}
      ${it.reviewed && html`<span class="tile-check" title="Reviewed">✓</span>`}
    </span>
    <span class="tile-name">${it.name}</span>
    <span class="tile-meta">${it.year || ''}${it.year ? ' · ' : ''}${it.library_name}</span>
  </button>`;
}

export function Library() {
  const [data, setData] = useState(null);
  const [q, setQ] = useState(sessionStorage.getItem('lib.q') || '');
  const [lib, setLib] = useState(sessionStorage.getItem('lib.lib') || '');
  const [filter, setFilter] = useState(sessionStorage.getItem('lib.filter') || 'all');
  const [sort, setSort] = useState(sessionStorage.getItem('lib.sort') || 'name');

  useEffect(() => { api('/library').then(setData).catch(ex => toast(ex.message, true)); }, []);
  useEffect(() => {
    sessionStorage.setItem('lib.q', q); sessionStorage.setItem('lib.lib', lib);
    sessionStorage.setItem('lib.filter', filter); sessionStorage.setItem('lib.sort', sort);
  }, [q, lib, filter, sort]);

  const shown = useMemo(() => {
    if (!data) return [];
    const needle = q.trim().toLowerCase();
    let rows = data.items.filter(it => (!lib || it.library_name === lib) && matches(it, filter)
      && (!needle || it.name.toLowerCase().includes(needle)));
    if (sort === 'added') rows = [...rows].sort((a, b) => (b.added_at || 0) - (a.added_at || 0));
    if (sort === 'year') rows = [...rows].sort((a, b) => (b.year || 0) - (a.year || 0));
    return rows;
  }, [data, q, lib, filter, sort]);

  // Titles sharing one set of rules (a 4K and a 1080p copy) show once.
  const tiles = useMemo(() => {
    const seen = new Set();
    return shown.filter(it => (seen.has(it.title_key) ? false : (seen.add(it.title_key), true)));
  }, [shown]);

  function open(it, review = false) {
    nav.ids = tiles.map(t => t.jf_id);
    nav.label = filter === 'all' && !lib && !q ? 'your library' : 'this list';
    go(`title/${it.jf_id}${review ? '?review' : ''}`);
  }
  function startReview() {
    const first = tiles.find(t => !t.reviewed && t.status !== 'left_alone') || tiles[0];
    if (first) open(first, true);
  }

  if (!data) return html`<div class="empty">Loading your library…</div>`;
  if (!data.items.length) return html`<div class="notice info"><p>The library is empty. Connect Jellyfin in Settings, then run a preview from Activity so Studio can read it.</p></div>`;
  const reviewed = data.items.filter(i => i.reviewed).length;
  const reviewable = data.items.filter(i => i.status !== 'left_alone').length;
  return html`
    <div class="page-head">
      <div><h1>Library</h1><p>${n(tiles.length)} of ${n(new Set(data.items.map(i => i.title_key)).size)} titles · ${n(reviewed)} of ${n(reviewable)} reviewed</p></div>
      <button class="primary" onClick=${startReview} disabled=${!tiles.length}>Review one by one</button>
    </div>
    <div class="toolbar">
      <input type="search" placeholder="Search titles" value=${q} onInput=${e => setQ(e.target.value)} aria-label="Search titles" />
      <select value=${filter} onChange=${e => setFilter(e.target.value)} aria-label="Filter">
        ${FILTERS.map(([v, l]) => html`<option value=${v}>${l}</option>`)}</select>
      <select value=${sort} onChange=${e => setSort(e.target.value)} aria-label="Sort">
        <option value="name">A–Z</option><option value="added">Recently added</option><option value="year">Newest first</option></select>
    </div>
    <div class="seg" role="tablist">
      <button class=${!lib ? 'on' : ''} onClick=${() => setLib('')}>All</button>
      ${data.libraries.map(l => html`<button class=${lib === l ? 'on' : ''} onClick=${() => setLib(l)}>${l}</button>`)}
    </div>
    ${tiles.length
      ? html`<div class="tiles">${tiles.map(it => html`<${Tile} key=${it.jf_id} it=${it} onOpen=${() => open(it)} />`)}</div>`
      : html`<div class="empty">Nothing matches.</div>`}`;
}
