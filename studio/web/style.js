import { html, useState, useEffect, useRef } from './vendor/preact-htm.js';
import { api, toast, go } from './common.js';
import { StyleControls, GROUPS } from './controls.js';

// The editor's shared controls, plus what only makes sense library-wide.
const EXTRA = {
  Logo: [
    { k: 'logo_bottom_anchor', t: 'bool', label: 'Same baseline' },
    { k: 'logo_priority', t: 'select', label: 'Language', options: [
      ['native,english,original,neutral,text', 'Own, then English'], ['english,native,original,neutral,text', 'English first'],
      ['native_original', 'PostersPlus default']] },
  ],
  Fades: [{ k: 'vignette_poster_color_bottom', t: 'bool', label: 'Poster tint' }],
  Notch: [
    { k: 'sash_mode', t: 'select', label: 'Shape', options: [['notch', 'Notch'], ['sash', 'Diagonal sash']] },
    { k: 'sash_badge_pos', t: 'select', label: 'Position', options: [['center', 'Centre'], ['left', 'Left'], ['right', 'Right']] },
    { k: 'sash_badge_size_w', t: 'range', label: 'Width', min: 0.6, max: 2, step: 0.05 },
    { k: 'sash_badge_size_h', t: 'range', label: 'Height', min: 0.6, max: 2, step: 0.05 },
  ],
};
const LIBRARY_GROUPS = [
  ...GROUPS.map(([name, ctls]) => [name, [...ctls.filter(c => c.k !== 'notch_label'), ...(EXTRA[name] || [])]]),
  ['Rating & badges', [
    { k: 'rating_display_mode', t: 'select', label: 'Rating', options: [['0', 'Hidden'], ['2', 'Clean'], ['3', 'Minimalist'], ['1', 'Bar'], ['4', 'Frosted bar']] },
    { k: 'badge_display_mode', t: 'select', label: 'Quality', options: [['0', 'Hidden'], ['7', 'Graphic'], ['4', 'Classic']] },
    { k: 'fallback_bg_style', t: 'select', label: 'No art', options: [['photoreal', 'Photographic'], ['minimal', 'Minimal']] },
  ]],
];

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
  const set = (k, v) => { const next = { ...params }; if (v === null || v === undefined) delete next[k]; else next[k] = String(v); save(next); };
  async function act(path, body, msg) {
    setBusy(true);
    try { const d = await api(path, { method: 'POST', body: body || {} }); take(d); if (msg) toast(msg); refreshStatus(); return d; }
    catch (ex) { toast(ex.message, true); return null; }
    finally { setBusy(false); }
  }
  const apply = async () => { const d = await act('/style/apply', { run: true }); if (d) toast(d.uploads_enabled ? 'Applied · updating the library' : 'Applied · uploads are off'); };
  function saveRaw() {
    const next = {};
    for (const line of raw.split('\n')) { const i = line.indexOf('='); if (i > 0) next[line.slice(0, i).trim()] = line.slice(i + 1).trim(); }
    save(next); setRaw(null);
  }
  const rawText = raw !== null ? raw : Object.entries(params).map(([k, v]) => `${k}=${v}`).join('\n');

  return html`
    <div class="page-head">
      <div><h1>Style</h1><p>${s.has_draft ? html`<span class="chip warn">Draft</span> Only the previews show it until you apply.` : 'What every poster gets. Titles and single posters can override it.'}</p></div>
      <div class="row">
        ${s.has_draft ? html`<button onClick=${() => act('/style/discard', {})} disabled=${busy}>Discard</button>
          <button class="primary" onClick=${apply} disabled=${busy}>Apply to library</button>`
          : s.can_undo ? html`<button onClick=${() => act('/style/undo', {}, 'Previous style loaded as a draft')} disabled=${busy}>Undo last apply</button>` : ''}
      </div>
    </div>
    <div class="style-layout">
      <section class="style-previews">
        <div class="row" style="justify-content:space-between">
          <div class="seg small">
            <button class=${!before ? 'on' : ''} onClick=${() => setBefore(false)}>${s.has_draft ? 'Draft' : 'Current'}</button>
            <button class=${before ? 'on' : ''} onClick=${() => setBefore(true)} disabled=${!s.has_draft}>Before</button>
          </div>
          <button class="link" onClick=${async () => { const d = await api('/style/samples', { method: 'POST', body: {} }); setS({ ...s, samples: d.samples }); }}>Other titles</button>
        </div>
        <div class="sample-grid">${s.samples.map(x => html`<figure key=${x.jf_id}>
          <img loading="lazy" alt="" src=${`/studio/api/preview/${x.jf_id}?w=500${!before && s.has_draft ? '&style=draft' : ''}&_=${ts}`} onClick=${() => go(`title/${x.jf_id}`)} />
          <figcaption>${x.name}</figcaption></figure>`)}</div>
      </section>
      <section>
        <${StyleControls} values=${params} inherited=${s.defaults} from="default" onSet=${set} groups=${LIBRARY_GROUPS} />
        <div class="row" style="margin-bottom:12px"><button onClick=${() => go('notch')}>Notch labels →</button></div>
        <details class="labels">
          <summary>Advanced</summary>
          <p class="hint-sm">Raw <code>name=value</code> settings, or a configurator URL.</p>
          <textarea rows="7" value=${rawText} onInput=${e => setRaw(e.target.value)}></textarea>
          <div class="row" style="margin-top:8px">
            <button onClick=${saveRaw} disabled=${raw === null}>Use</button>
            <input type="url" placeholder="Configurator URL" value=${url} onInput=${e => setUrl(e.target.value)} style="flex:1" />
            <button onClick=${() => { act('/style/import', { url }, 'Imported as a draft'); setUrl(''); }} disabled=${!url || busy}>Import</button>
          </div>
        </details>
      </section>
    </div>`;
}
