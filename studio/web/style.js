import { html, useState, useEffect, useRef } from './vendor/preact-htm.js';
import { api, toast, go } from './common.js';

const SASH_LABELS = {
  watchlist: 'On your watchlist', wins: 'Oscar / Emmy winner', gg_wins: 'Golden Globe winner', festival: 'Festival prize',
  pic_noms: 'Oscar / Emmy nominee', metacritic: 'Metacritic must-see', gg_noms: 'Golden Globe nominee',
  studio: 'Notable studio', director: 'Notable director', cast: 'Notable cast', trending: 'Trending',
  new_season: 'New season', returning: 'Returning', premiere: 'Premiere', just_added: 'Just added',
  season_finale: 'Season finale', cult: 'Cult classic', foreign: 'Foreign language', newly_streaming: 'Newly streaming',
  true_story: 'True story', short_film: 'Short film', mini_series: 'Mini series', binge_ready: 'Binge ready',
  trending_broad: 'Trending (lower ranks)', cinema: 'In cinemas', streaming: 'Streaming', physical: 'Disc release',
  production: 'In production', ended: 'Ended', cancelled: 'Cancelled', airing: 'Airing', renewed: 'Renewed',
};
const sashLabel = s => SASH_LABELS[s] || s.replace(/_/g, ' ').replace(/^./, c => c.toUpperCase());

const LOGO_PRIORITY = [
  ['native,english,original,neutral,text', 'The title’s own language, then English'],
  ['english,native,original,neutral,text', 'English first'],
  ['native_original', 'PostersPlus default'],
];
const SECTIONS = [
  ['Logo', [
    { k: 'logo_max_w_ratio', type: 'range', label: 'Logo width', min: 0.4, max: 0.95, step: 0.01, pct: true },
    { k: 'logo_max_h_ratio', type: 'range', label: 'Logo height limit', min: 0.1, max: 0.45, step: 0.01, pct: true },
    { k: 'logo_bottom_ratio', type: 'range', label: 'Distance from the bottom', min: 0, max: 0.3, step: 0.005, pct: true },
    { k: 'logo_bottom_anchor', type: 'bool', label: 'Keep every logo on the same baseline', help: 'Short and tall logos sit at the same height from the bottom.' },
    { k: 'logo_priority', type: 'select', label: 'Logo language', options: LOGO_PRIORITY },
  ]],
  ['Fades', [
    { k: 'top_gradient', type: 'select', label: 'Top fade', options: [['off', 'Off'], ['low', 'Light'], ['medium', 'Medium'], ['high', 'Strong']] },
    { k: 'bottom_gradient', type: 'select', label: 'Bottom fade', options: [['off', 'Off'], ['low', 'Light'], ['medium', 'Medium'], ['high', 'Strong']] },
    { k: 'vignette_poster_color_bottom', type: 'bool', label: 'Tint the bottom fade with the poster’s colour', help: 'Instead of plain black.' },
  ]],
  ['Notch', [
    { k: 'show_award_sash', type: 'bool', label: 'Show the notch', help: 'The label at the top: awards, new season, trending…' },
    { k: 'sash_mode', type: 'select', label: 'Shape', options: [['notch', 'Notch at the top'], ['sash', 'Diagonal sash in the corner']] },
    { k: 'sash_badge_style', type: 'select', label: 'Look', options: [['frosted', 'Frosted (poster colour)'], ['black', 'Black'], ['silver', 'Silver'], ['gold', 'Gold']] },
    { k: 'sash_badge_pos', type: 'select', label: 'Position', options: [['center', 'Centre'], ['left', 'Left corner'], ['right', 'Right corner']] },
    { k: 'sash_badge_size_w', type: 'range', label: 'Width', min: 0.6, max: 2, step: 0.05, x: true },
    { k: 'sash_badge_size_h', type: 'range', label: 'Height', min: 0.6, max: 2, step: 0.05, x: true },
  ]],
  ['Rating and badges', [
    { k: 'rating_display_mode', type: 'select', label: 'Rating', options: [['0', 'Hidden'], ['2', 'Clean (genre · score)'], ['3', 'Minimalist'], ['1', 'Rating bar'], ['4', 'Frosted bar']] },
    { k: 'badge_display_mode', type: 'select', label: 'Quality badges (4K, HDR…)', options: [['0', 'Hidden'], ['7', 'Graphic badges'], ['4', 'Classic badges']] },
  ]],
  ['When there’s no artwork', [
    { k: 'fallback_bg_style', type: 'select', label: 'Background', options: [['photoreal', 'Photographic'], ['minimal', 'Minimal texture']] },
  ]],
];

function fmt(ctl, v) {
  const n = +v;
  if (ctl.pct) return `${Math.round(n * 100)}%`;
  if (ctl.x) return `${n.toFixed(2)}×`;
  return v;
}

function SashList({ sash, onChange }) {
  const active = sash.active;
  const inactive = sash.all.filter(s => !active.includes(s));
  const move = (i, d) => { const a = [...active]; const j = i + d; if (j < 0 || j >= a.length) return; [a[i], a[j]] = [a[j], a[i]]; onChange(a); };
  return html`<div>
    <p class="hint-sm">The first label that applies to a title is the one shown. Switch labels off or move them up.</p>
    <div class="list sash-list">${active.map((s, i) => html`<div class="list-row">
      <label class="switch" style="padding:0;flex:1"><input type="checkbox" checked onChange=${() => onChange(active.filter(x => x !== s))} /><span class="t">${sashLabel(s)}</span></label>
      <button class="icon" onClick=${() => move(i, -1)} disabled=${i === 0} aria-label="Move up">↑</button>
      <button class="icon" onClick=${() => move(i, 1)} disabled=${i === active.length - 1} aria-label="Move down">↓</button>
    </div>`)}
    ${inactive.map(s => html`<div class="list-row off">
      <label class="switch" style="padding:0;flex:1"><input type="checkbox" onChange=${() => onChange([...active, s])} /><span class="t">${sashLabel(s)}</span></label>
    </div>`)}</div>
  </div>`;
}

export function Style({ refreshStatus }) {
  const [s, setS] = useState(null);
  const [params, setParams] = useState(null);
  const [before, setBefore] = useState(false);
  const [ts, setTs] = useState(Date.now());
  const [raw, setRaw] = useState(null);
  const [url, setUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const timer = useRef(null);

  const take = d => { setS(d); setParams(d.params); setRaw(null); setTs(Date.now()); };
  useEffect(() => { api('/style').then(take).catch(ex => toast(ex.message, true)); }, []);
  if (!s || !params) return html`<div class="empty">Loading…</div>`;

  function save(next) {
    setParams(next);
    clearTimeout(timer.current);
    timer.current = setTimeout(async () => {
      try { const d = await api('/style', { method: 'PUT', body: { params: next } }); setS(d); setTs(Date.now()); refreshStatus(); }
      catch (ex) { toast(ex.message, true); }
    }, 600);
  }
  const value = k => (params[k] !== undefined ? params[k] : s.defaults[k]);
  const set = (k, v) => { const next = { ...params }; if (v === null || v === undefined || v === s.defaults[k]) delete next[k]; else next[k] = String(v); save(next); };
  function setSash(active) {
    const next = { ...params };
    next.sash_priority = active.length ? active.join(',') : s.sash.all.map(x => '-' + x).join(',');
    setS({ ...s, sash: { ...s.sash, active } });
    save(next);
  }
  async function act(path, body, msg) {
    setBusy(true);
    try { const d = await api(path, { method: 'POST', body: body || {} }); take(d); if (msg) toast(msg); refreshStatus(); return d; }
    catch (ex) { toast(ex.message, true); return null; }
    finally { setBusy(false); }
  }
  async function apply() {
    const d = await act('/style/apply', { run: true });
    if (d) toast(d.uploads_enabled ? 'Applied. Studio is updating your library now (see Activity).' : 'Applied. Uploads are off, so the run is a preview.');
  }
  function saveRaw() {
    const next = {};
    for (const line of raw.split('\n')) {
      const i = line.indexOf('='); if (i < 1) continue;
      next[line.slice(0, i).trim()] = line.slice(i + 1).trim();
    }
    save(next); setRaw(null);
  }

  const rawText = raw !== null ? raw : Object.entries(params).map(([k, v]) => `${k}=${v}`).join('\n');
  return html`
    <div class="page-head">
      <div><h1>Poster style</h1><p>The look every poster gets. Titles can still override it in their own editor.</p></div>
      <div class="row">
        ${s.has_draft ? html`<button onClick=${() => act('/style/discard', {}, 'Changes discarded')} disabled=${busy}>Discard changes</button>
          <button class="primary" onClick=${apply} disabled=${busy}>Apply to library</button>`
          : html`<button onClick=${() => act('/style/undo', {}, 'The previous style is loaded as a draft: apply it to go back')} disabled=${busy}>Undo last apply</button>`}
      </div>
    </div>
    ${s.has_draft
      ? html`<div class="notice warn"><p><strong>Not live yet.</strong> These changes only show in the previews below. Jellyfin keeps the current style until you press <strong>Apply to library</strong>.</p></div>`
      : html`<div class="notice ok"><p>This is the style Jellyfin has. Change anything below to start a draft.</p></div>`}
    <div class="style-layout">
      <section class="style-previews">
        <div class="row" style="justify-content:space-between;margin-bottom:10px">
          <div class="seg small">
            <button class=${!before ? 'on' : ''} onClick=${() => setBefore(false)}>${s.has_draft ? 'With changes' : 'Current'}</button>
            <button class=${before ? 'on' : ''} onClick=${() => setBefore(true)} disabled=${!s.has_draft}>Before</button>
          </div>
          <button class="link" onClick=${async () => { const d = await api('/style/samples', { method: 'POST', body: {} }); setS({ ...s, samples: d.samples }); }}>Other titles</button>
        </div>
        <div class="sample-grid">${s.samples.map(x => html`<figure key=${x.jf_id}>
          <img loading="lazy" alt="" src=${`/studio/api/preview/${x.jf_id}?w=500${!before && s.has_draft ? '&style=draft' : ''}&_=${ts}`} onClick=${() => go(`title/${x.jf_id}`)} />
          <figcaption>${x.name}</figcaption></figure>`)}</div>
      </section>
      <section class="style-controls">
        ${SECTIONS.map(([title, ctls]) => html`<div class="card">
          <h3 style="margin-bottom:6px">${title}</h3>
          ${ctls.map(ctl => {
            const v = value(ctl.k);
            const custom = params[ctl.k] !== undefined;
            if (ctl.type === 'range') return html`<div class="slider-row">
              <label for=${ctl.k}>${ctl.label}</label>
              <input id=${ctl.k} type="range" min=${ctl.min} max=${ctl.max} step=${ctl.step} value=${v} onInput=${e => set(ctl.k, e.target.value)} />
              <span class="val">${fmt(ctl, v)}</span></div>`;
            if (ctl.type === 'bool') return html`<label class="switch">
              <input type="checkbox" checked=${v === 'true'} onChange=${e => set(ctl.k, e.target.checked ? 'true' : 'false')} />
              <span><span class="t">${ctl.label}</span>${ctl.help && html`<span class="d" style="display:block">${ctl.help}</span>`}</span></label>`;
            const opts = ctl.options.some(([o]) => o === v) ? ctl.options : [...ctl.options, [v, `${v} (custom)`]];
            return html`<div class="slider-row"><label for=${ctl.k}>${ctl.label}</label>
              <select id=${ctl.k} value=${v} onChange=${e => set(ctl.k, e.target.value)}>${opts.map(([o, l]) => html`<option value=${o}>${l}</option>`)}</select>
              ${custom ? '' : html`<span class="val">default</span>`}</div>`;
          })}
          ${title === 'Notch' && html`<${SashList} sash=${s.sash} onChange=${setSash} />`}
        </div>`)}
        <div class="card">
          <h3>Advanced</h3>
          <p class="hint-sm">Every setting as <code>name=value</code>, one per line (the configurator's poster URL parameters). Or paste a URL from the configurator's Copy config to take its look.</p>
          <textarea rows="8" value=${rawText} onInput=${e => setRaw(e.target.value)}></textarea>
          <div class="row" style="margin-top:10px">
            <button onClick=${saveRaw} disabled=${raw === null}>Use these settings</button>
            <a href="/" target="_blank"><button>Open the configurator</button></a>
          </div>
          <div class="row" style="margin-top:10px">
            <input type="url" placeholder="Paste a configurator poster URL" value=${url} onInput=${e => setUrl(e.target.value)} style="flex:1" />
            <button onClick=${() => { act('/style/import', { url }, 'Imported as a draft'); setUrl(''); }} disabled=${!url || busy}>Import</button>
          </div>
        </div>
      </section>
    </div>`;
}
