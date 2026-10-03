import { html, useState, useEffect, useMemo } from './vendor/preact-htm.js';
import { api, toast, n, go, nav, ago } from './common.js';

const KINDS = [['poster', 'Poster'], ['backdrop', 'Backdrop'], ['logo', 'Logo'], ['thumb', 'Thumb']];

// Every title Jellyfin has no poster, backdrop, logo or thumb for.
export function Missing() {
  const [data, setData] = useState(null);
  const [kinds, setKinds] = useState(new Set((sessionStorage.getItem('missing.kinds') || 'poster,backdrop,logo,thumb').split(',').filter(Boolean)));
  const [lib, setLib] = useState('');
  const [busy, setBusy] = useState(false);

  const reload = () => api('/missing').then(setData).catch(ex => toast(ex.message, true));
  useEffect(() => { reload(); }, []);
  useEffect(() => { sessionStorage.setItem('missing.kinds', [...kinds].join(',')); }, [kinds]);

  const shown = useMemo(() => (data ? data.items.filter(it => (!lib || it.library_name === lib) && it.missing.some(k => kinds.has(k))) : []), [data, kinds, lib]);

  async function rescan() {
    setBusy(true);
    try { await api('/scan', { method: 'POST', body: {} }); await reload(); toast('Read from Jellyfin again'); }
    catch (ex) { toast(ex.message, true); }
    setBusy(false);
  }
  function toggle(k) {
    const next = new Set(kinds);
    if (next.has(k)) next.delete(k); else next.add(k);
    setKinds(next);
  }
  function open(it) {
    nav.ids = shown.map(t => t.jf_id);
    nav.label = 'this list';
    go(`title/${it.jf_id}?slot=${it.missing.find(k => kinds.has(k)) || it.missing[0]}`);
  }

  if (!data) return html`<div class="empty">Loading…</div>`;
  const libs = [...new Set(data.items.map(i => i.library_name))].sort();
  return html`
    <div class="page-head">
      <div><h1>Missing</h1><p>${n(shown.length)} titles · read from Jellyfin ${ago(data.last_scan_at)}</p></div>
      <button onClick=${rescan} disabled=${busy}>${busy ? 'Reading…' : 'Read again'}</button>
    </div>
    <div class="seg" role="tablist">
      ${KINDS.map(([k, l]) => html`<button class=${kinds.has(k) ? 'on' : ''} aria-pressed=${kinds.has(k)} onClick=${() => toggle(k)}>${l} ${n(data.counts[k])}</button>`)}
    </div>
    ${libs.length > 1 && html`<div class="seg small" role="tablist">
      <button class=${!lib ? 'on' : ''} onClick=${() => setLib('')}>All</button>
      ${libs.map(l => html`<button class=${lib === l ? 'on' : ''} onClick=${() => setLib(l)}>${l}</button>`)}
    </div>`}
    ${shown.length
      ? html`<div class="tiles">${shown.map(it => html`<button type="button" class="tile" key=${it.jf_id} onClick=${() => open(it)} title=${it.name}>
          <span class="tile-img">
            ${it.jf_image_tag && html`<img loading="lazy" alt="" src=${`/studio/api/thumb/${it.jf_id}?h=360&tag=${encodeURIComponent(it.jf_image_tag)}`}
              onError=${e => { e.target.style.visibility = 'hidden'; }} />`}
            <span class="tile-chips all">${it.missing.filter(k => kinds.has(k)).map(k => html`<span class="chip warn">No ${k}</span>`)}</span>
          </span>
          <span class="tile-name">${it.name}</span>
          <span class="tile-meta">${it.year || ''}${it.year ? ' · ' : ''}${it.library_name}</span>
        </button>`)}</div>`
      : html`<div class="empty">${data.items.length ? 'Nothing missing here.' : 'Jellyfin has every image for every title.'}</div>`}`;
}
