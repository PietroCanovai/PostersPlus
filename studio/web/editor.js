import { html, useState, useEffect, useRef, useMemo, useCallback } from './vendor/preact-htm.js';
import { api, toast, go, nav, thumbUrl, fullImageUrl, ago } from './common.js';
import { chipsFor } from './library.js';

const PROVIDERS = { tmdb: 'TMDB', fanart: 'Fanart', tvdb: 'TVDB', custom: 'Yours' };
const MODES = [
  ['auto', 'Automatic', 'PostersPlus picks the best poster, skipping anything you marked Never.'],
  ['pinned', 'Pinned', 'Always the same look: the poster, logo and colours you choose.'],
  ['rotation', 'Daily rotation', 'A different look each night, shuffled, no repeats until all have had their day.'],
];
// Look colours: [key in look.colors, parameter in a title style, label, help]
const COLORS = [
  ['tint', 'tint_color', 'Notch colour', 'The frosted notch at the top (normally sampled from the poster).'],
  ['fade', 'fade_color', 'Fade colour', 'The dark fade behind the logo.'],
  ['sash_text', 'notch_text_color', 'Notch text', 'The label inside the notch.'],
  ['logo', 'logo_color', 'Logo colour', 'Recolours the logo (solid or tinted).'],
];
const STYLE_CONTROLS = [
  { key: 'logo_max_w_ratio', label: 'Logo width', min: 0.4, max: 0.95, step: 0.01, def: 0.74, fmt: v => `${Math.round(v * 100)}%` },
  { key: 'logo_max_h_ratio', label: 'Logo height limit', min: 0.1, max: 0.45, step: 0.01, def: 0.24, fmt: v => `${Math.round(v * 100)}%` },
  { key: 'logo_bottom_ratio', label: 'Logo distance from bottom', min: 0, max: 0.3, step: 0.005, def: 0.05, fmt: v => `${Math.round(v * 100)}%` },
];

function posterKind(c) { return c.kind || 'posters'; }

// ── Crop dialog: a 2:3 window on a wide image (same maths as the Artwork tab) ──
function CropDialog({ src, initial, onSave, onClose }) {
  const [crop, setCrop] = useState({ x: 0.5, y: 0.5, zoom: 1, ...(initial || {}) });
  const img = useRef(null);
  const [geo, setGeo] = useState(null);
  const measure = useCallback(() => {
    const el = img.current; if (!el || !el.naturalWidth) return;
    const dw = el.clientWidth, dh = el.clientHeight;
    const cw = Math.min(dw, dh * 2 / 3) / crop.zoom, ch = cw * 1.5;
    setGeo({ dw, dh, cw, ch, left: (dw - cw) * crop.x, top: (dh - ch) * crop.y });
  }, [crop]);
  useEffect(() => { measure(); }, [crop]);
  useEffect(() => {
    const onKey = e => {
      if (e.key === 'Escape') onClose();
      const step = e.shiftKey ? 0.1 : 0.02;
      const d = { ArrowLeft: ['x', -step], ArrowRight: ['x', step], ArrowUp: ['y', -step], ArrowDown: ['y', step] }[e.key];
      if (d) { e.preventDefault(); e.stopPropagation(); setCrop(c => ({ ...c, [d[0]]: Math.min(1, Math.max(0, c[d[0]] + d[1])) })); }
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, []);
  function drag(e) {
    if (!geo) return;
    e.preventDefault();
    const stage = e.currentTarget, rect = stage.getBoundingClientRect();
    const inside = e.target.classList.contains('crop-win');
    const startLeft = inside ? geo.left : e.clientX - rect.left - geo.cw / 2;
    const startTop = inside ? geo.top : e.clientY - rect.top - geo.ch / 2;
    const x0 = e.clientX, y0 = e.clientY;
    const move = ev => {
      const left = Math.min(Math.max(startLeft + ev.clientX - x0, 0), geo.dw - geo.cw);
      const top = Math.min(Math.max(startTop + ev.clientY - y0, 0), geo.dh - geo.ch);
      setCrop(c => ({ ...c, x: geo.dw > geo.cw ? left / (geo.dw - geo.cw) : 0.5, y: geo.dh > geo.ch ? top / (geo.dh - geo.ch) : 0.5 }));
    };
    move(e);
    stage.setPointerCapture(e.pointerId);
    stage.onpointermove = move;
    stage.onpointerup = stage.onpointercancel = () => { stage.onpointermove = null; };
  }
  return html`<div class="veil" onClick=${e => { if (e.target === e.currentTarget) onClose(); }}>
    <div class="dialog wide" role="dialog" aria-label="Frame the poster">
      <h2>Frame the poster</h2>
      <p class="sub">Drag the frame (or use the arrow keys). The logo goes near the bottom.</p>
      <div class="crop-stage" onPointerDown=${drag}>
        <img ref=${img} src=${src} alt="" referrerpolicy="no-referrer" draggable="false" onLoad=${measure} />
        ${geo && html`<div class="crop-win" style=${`left:${geo.left}px;top:${geo.top}px;width:${geo.cw}px;height:${geo.ch}px`}></div>`}
      </div>
      <label class="field" style="margin:14px 0 0;display:block"><span style="font-weight:580">Zoom ${crop.zoom.toFixed(2)}×</span>
        <input type="range" min="1" max="4" step="0.05" value=${crop.zoom} onInput=${e => setCrop(c => ({ ...c, zoom: +e.target.value }))} style="width:100%" /></label>
      <div class="row" style="justify-content:flex-end;margin-top:12px">
        <button onClick=${onClose}>Cancel</button>
        <button class="primary" onClick=${() => onSave({ x: +crop.x.toFixed(4), y: +crop.y.toFixed(4), zoom: +crop.zoom.toFixed(3) })}>Use this frame</button>
      </div>
    </div></div>`;
}

// ── Matching a title Jellyfin couldn't ──────────────────────────────────────
function MatchPanel({ t, onDone }) {
  const [q, setQ] = useState(t.item.name.replace(/\s*\(\d{4}\)\s*$/, ''));
  const [res, setRes] = useState(null);
  const type = t.item.jf_type === 'Series' ? 'tv' : 'movie';
  async function search(e) {
    if (e) e.preventDefault();
    try { setRes((await api(`/tmdb/search?type=${type}&q=${encodeURIComponent(q)}`)).results); }
    catch (ex) { toast(ex.message, true); }
  }
  useEffect(() => { search(); }, []);
  async function link(id) {
    try { await api(`/items/${t.item.jf_id}/match`, { method: 'PUT', body: { tmdb_id: id } }); toast('Linked. Studio will make its poster.'); onDone(); }
    catch (ex) { toast(ex.message, true); }
  }
  return html`<div class="card">
    <h2>Find this title on TMDB</h2>
    <p class="sub">Jellyfin has no TMDB id for it, so Studio can't find artwork. Pick the right one; this only changes Studio, not Jellyfin's metadata.</p>
    <form class="row" onSubmit=${search} style="margin-bottom:14px">
      <input type="search" value=${q} onInput=${e => setQ(e.target.value)} style="flex:1" />
      <button class="primary">Search</button></form>
    ${res && (res.length ? html`<div class="list">${res.map(r => html`<div class="list-row">
        ${r.thumb ? html`<img src=${r.thumb} alt="" class="mini-poster" referrerpolicy="no-referrer" />` : html`<span class="mini-poster"></span>`}
        <div class="grow"><div class="name">${r.title}${r.year ? ` (${r.year})` : ''}</div><div class="meta">${r.overview}</div></div>
        <button onClick=${() => link(r.tmdb_id)}>This one</button></div>`)}</div>`
      : html`<div class="empty">Nothing found. Try another spelling.</div>`)}
  </div>`;
}

// ── Candidate cards ─────────────────────────────────────────────────────────
function Card({ c, kind, badges, actions, dim }) {
  const meta = [PROVIDERS[c.provider] || c.provider, c.language || (kind === 'logos' ? 'no language' : kind === 'posters' ? 'no text' : ''),
    c.width ? `${c.width}×${c.height}` : ''].filter(Boolean).join(' · ');
  return html`<div class="cand ${kind} ${dim ? 'dim' : ''}">
    <div class="cand-img">
      <img loading="lazy" referrerpolicy="no-referrer" alt="" src=${c.thumb || thumbUrl(c.path, kind)}
        onError=${e => { if (e.target.src !== fullImageUrl(c.path)) e.target.src = fullImageUrl(c.path); }} />
      ${badges.length > 0 && html`<span class="cand-badges">${badges.map(([l, cl]) => html`<span class="chip ${cl}">${l}</span>`)}</span>`}
    </div>
    <div class="cand-meta">${meta}</div>
    <div class="cand-actions">${actions}</div>
  </div>`;
}

// ── The editor ──────────────────────────────────────────────────────────────
export function Editor({ id, review }) {
  const [t, setT] = useState(null);
  const [cands, setCands] = useState(null);
  const [tab, setTab] = useState('posters');
  const [pkind, setPkind] = useState('textless');
  const [source, setSource] = useState('all');
  const [sel, setSel] = useState(null);           // look being edited (rotation)
  const [ts, setTs] = useState(Date.now());       // preview cache-buster
  const [busy, setBusy] = useState(false);
  const [crop, setCrop] = useState(null);         // {path, initial, then}
  const [pick, setPick] = useState(null);         // colour being eyedropped
  const [link, setLink] = useState('');
  const [localStyle, setLocalStyle] = useState(null);
  const saveTimer = useRef(null);
  const previewImg = useRef(null);

  const load = useCallback(async () => {
    try { setT(await api(`/title/${id}`)); } catch (ex) { toast(ex.message, true); }
  }, [id]);
  useEffect(() => {
    setT(null); setCands(null); setSel(null); setLocalStyle(null); setTab('posters');
    load();
    api(`/title/${id}/candidates`).then(setCands).catch(ex => setCands({ error: ex.message, candidates: { posters: [], backdrops: [], logos: [] } }));
  }, [id]);

  const idx = nav.ids.indexOf(id);
  const prevId = idx > 0 ? nav.ids[idx - 1] : null;
  const nextId = idx >= 0 && idx < nav.ids.length - 1 ? nav.ids[idx + 1] : null;
  const goTo = other => go(`title/${other}${review ? '?review' : ''}`);

  useEffect(() => {
    const onKey = e => {
      if (crop || /INPUT|TEXTAREA|SELECT/.test(document.activeElement && document.activeElement.tagName)) return;
      if (e.key === 'ArrowLeft' && prevId) goTo(prevId);
      if (e.key === 'ArrowRight' && nextId) goTo(nextId);
      if (e.key === 'Enter' && review) markReviewed();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  const refresh = async () => { await load(); setTs(Date.now()); };
  async function call(path, opts, okMsg) {
    setBusy(true);
    try { const r = await api(path, opts); if (okMsg) toast(okMsg); await refresh(); return r; }
    catch (ex) { toast(ex.message, true); return null; }
    finally { setBusy(false); }
  }

  if (!t) return html`<div class="empty">Loading…</div>`;
  const matched = !!t.tmdb_id || t.stage;
  const mode = t.title.mode;
  const looks = t.looks;
  const pinned = looks.find(l => l.look_id === t.title.pinned_look_id);
  const rotation = looks.filter(l => l.in_rotation);
  const never = { poster: new Set(t.never.poster || []), logo: new Set(t.never.logo || []) };
  const editLook = mode === 'pinned' ? pinned
    : mode === 'rotation' ? (rotation.find(l => l.look_id === sel) || rotation.find(l => l.look_id === t.today_look_id) || rotation[0])
    : null;
  const autoPoster = cands && cands.auto && cands.auto.poster;
  const autoLogo = cands && cands.auto && cands.auto.logo;

  // ── Actions ──
  async function setMode(m) {
    if (m === 'pinned') {
      if (pinned) return call(`/title/${id}`, { method: 'PUT', body: { mode: 'pinned', pinned_look_id: pinned.look_id } });
      const base = editLook || rotation[0];
      const fields = base ? { poster: base.poster, crop: base.crop, own_title: base.own_title, logo: base.logo, colors: base.colors, style: base.style }
        : { poster: '', logo: '' };
      return call(`/title/${id}/looks`, { method: 'POST', body: { ...fields, pin: true } }, 'Pinned. Pick the poster below.');
    }
    await call(`/title/${id}`, { method: 'PUT', body: { mode: m } },
      m === 'rotation' && !rotation.length ? 'Now add posters to the rotation with ↻ Rotate below.' : null);
  }
  function withCrop(path, kind, then) {
    if (kind === 'backdrops') setCrop({ path, initial: null, then });
    else then('');
  }
  function usePoster(c, kind) {
    withCrop(c.path, kind, async cropVal => {
      const fields = { poster: c.path, crop: cropVal || '', own_title: kind === 'posters' && !!c.language };
      if (pinned) {
        await call(`/looks/${pinned.look_id}`, { method: 'PUT', body: fields });
        if (mode !== 'pinned') await call(`/title/${id}`, { method: 'PUT', body: { mode: 'pinned', pinned_look_id: pinned.look_id } });
        toast('Pinned');
      } else {
        await call(`/title/${id}/looks`, { method: 'POST', body: { ...fields, pin: true } }, 'Pinned');
      }
    });
  }
  function toggleRotation(c, kind) {
    const existing = rotation.find(l => l.poster === c.path);
    if (existing) return call(`/looks/${existing.look_id}`, { method: 'DELETE' }, 'Removed from the rotation');
    withCrop(c.path, kind, cropVal => call(`/title/${id}/looks`, {
      method: 'POST', body: { poster: c.path, crop: cropVal || '', own_title: kind === 'posters' && !!c.language, in_rotation: true },
    }, mode === 'rotation' ? 'Added to the rotation' : 'Added to the rotation (switched to Daily rotation)'));
  }
  function toggleNever(kindName, path) {
    const on = !never[kindName].has(path);
    return call(`/title/${id}/never`, { method: 'PUT', body: { kind: kindName, ref: path, on } },
      on ? 'Never used automatically again' : 'Allowed again');
  }
  async function useLogo(path) {
    if (editLook) return call(`/looks/${editLook.look_id}`, { method: 'PUT', body: { logo: path } }, 'Logo set');
    return call(`/title/${id}/looks`, { method: 'POST', body: { poster: '', logo: path, pin: true } }, 'Logo set (this title is now pinned, poster still automatic)');
  }
  async function upload(file, kindName) {
    if (!file) return;
    setBusy(true);
    try {
      const r = await api(`/title/${id}/image?kind=${kindName}`, { method: 'POST', raw: file });
      setBusy(false);
      if (kindName === 'logo') await useLogo(r.path); else usePoster({ path: r.path, provider: 'custom' }, 'posters');
    } catch (ex) { setBusy(false); toast(ex.message, true); }
  }
  async function useLink(kindName) {
    if (!link.trim()) return;
    setBusy(true);
    try {
      const r = await api(`/title/${id}/image-link`, { method: 'POST', body: { url: link.trim(), kind: kindName } });
      setBusy(false); setLink('');
      if (kindName === 'logo') await useLogo(r.path); else usePoster({ path: r.path, provider: 'custom' }, 'posters');
    } catch (ex) { setBusy(false); toast(ex.message, true); }
  }
  async function push() {
    setBusy(true);
    try {
      const r = await api(`/title/${id}/push`, { method: 'POST', body: {} });
      const c = r.counts || {};
      toast(r.status !== 'done' ? (r.message || 'Push failed')
        : c.error ? `Failed: ${(r.items.find(i => i.action === 'error') || {}).detail || 'error'}`
        : c.uploaded || c.reverted ? 'Sent to Jellyfin' : c.skipped ? 'Skipped (hands off or no match)' : 'Jellyfin already had this poster', !!(c.error || r.status !== 'done'));
      await refresh();
    } catch (ex) { toast(ex.message, true); }
    setBusy(false);
  }
  async function markReviewed() {
    await api(`/title/${id}`, { method: 'PUT', body: { reviewed: true } }).catch(() => {});
    if (nextId) goTo(nextId); else { toast('That was the last one'); await refresh(); }
  }

  // Colours and style: saved (debounced) on the look being edited, or on the title.
  const styleOf = () => (editLook ? { colors: { ...editLook.colors }, style: { ...editLook.style } } : { title: { ...t.title.style } });
  const current = localStyle || styleOf();
  function colorValue(key, param) { return current.title ? current.title[param] : current.colors[key]; }
  function styleValue(key) { return current.title ? current.title[key] : current.style[key]; }
  function change(fn) {
    const next = JSON.parse(JSON.stringify(current));
    fn(next);
    setLocalStyle(next);
    clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(async () => {
      try {
        if (next.title) await api(`/title/${id}`, { method: 'PUT', body: { style: next.title } });
        else await api(`/looks/${editLook.look_id}`, { method: 'PUT', body: { colors: next.colors, style: next.style } });
        await load(); setLocalStyle(null); setTs(Date.now());
      } catch (ex) { toast(ex.message, true); }
    }, 700);
  }
  function setColor(key, param, hex) {
    change(s => {
      if (s.title) { if (hex) s.title[param] = hex.replace('#', ''); else delete s.title[param]; }
      else { if (hex) s.colors[key] = hex.replace('#', ''); else delete s.colors[key]; }
    });
  }
  function setStyle(key, value) {
    change(s => { const tgt = s.title || s.style; if (value === null || value === undefined) delete tgt[key]; else tgt[key] = String(value); });
  }
  function eyedrop(e) {
    if (!pick) return;
    const img = previewImg.current;
    const canvas = document.createElement('canvas');
    canvas.width = img.naturalWidth; canvas.height = img.naturalHeight;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(img, 0, 0);
    const r = img.getBoundingClientRect();
    const x = Math.floor((e.clientX - r.left) / r.width * img.naturalWidth), y = Math.floor((e.clientY - r.top) / r.height * img.naturalHeight);
    const [cr, cg, cb] = ctx.getImageData(x, y, 1, 1).data;
    const hex = [cr, cg, cb].map(v => v.toString(16).padStart(2, '0')).join('');
    setColor(pick[0], pick[1], hex);
    setPick(null);
  }

  // ── Preview ──
  const previewSrc = `/studio/api/preview/${id}?w=500${editLook ? `&look_id=${editLook.look_id}` : ''}&_=${ts}`;
  const chips = chipsFor({ ...t.item, mode, hands_off: !!t.title.hands_off, rotation: rotation.length,
    never: never.poster.size + never.logo.size, styled: false });

  // ── Posters tab ──
  const all = (cands && cands.candidates) || { posters: [], backdrops: [], logos: [] };
  const customPosters = t.uploads.filter(p => !p.endsWith('.png')).map(p => ({ path: p, provider: 'custom', language: null }));
  const customLogos = t.uploads.filter(p => p.endsWith('.png')).map(p => ({ path: p, provider: 'custom', language: null }));
  const posterList = pkind === 'backdrops' ? all.backdrops
    : pkind === 'yours' ? customPosters
    : all.posters.filter(c => (pkind === 'textless' ? !c.language : !!c.language));
  const shownPosters = posterList.filter(c => source === 'all' || c.provider === source);
  const kindOf = pkind === 'backdrops' ? 'backdrops' : 'posters';
  function posterBadges(c) {
    const b = [];
    if (pinned && pinned.poster === c.path && mode === 'pinned') b.push(['Pinned', 'info']);
    if (rotation.some(l => l.poster === c.path)) b.push(['In rotation', 'info']);
    if (!pinned && mode === 'auto' && autoPoster && autoPoster.path === c.path) b.push(['Automatic pick', 'ok']);
    if (never.poster.has(c.path)) b.push(['Never', 'bad']);
    return b;
  }

  const lookLabel = editLook
    ? (mode === 'pinned' ? 'the pinned look' : `rotation look ${rotation.indexOf(editLook) + 1} of ${rotation.length}`)
    : 'this title (automatic poster)';

  return html`
    ${review && html`<div class="notice info review-bar"><p><strong>Reviewing</strong> ${idx >= 0 ? `${idx + 1} of ${nav.ids.length} in ${nav.label}` : ''}. Fix what's wrong, then press <strong>Looks good</strong> (or Enter). ← → move without marking.</p>
      <div class="row"><button onClick=${() => go('library')}>Stop</button><button class="primary" onClick=${markReviewed}>Looks good ✓</button></div></div>`}
    <div class="page-head">
      <div>
        <a href="#library">← Library</a>
        <h1 style="margin-top:8px">${t.item.name}${t.item.year ? html` <span class="dim-text">(${t.item.year})</span>` : ''}</h1>
        <p>${t.item.library_name}${t.siblings.length > 1 ? ` · ${t.siblings.length} copies in Jellyfin share these rules` : ''}${t.item.pushed_at ? ` · sent to Jellyfin ${ago(t.item.pushed_at)}` : ''}
          ${chips.length ? html` ${chips.map(([l, c]) => html`<span class="chip ${c}" style="margin-left:4px">${l}</span>`)}` : ''}</p>
      </div>
      <div class="row">
        <button onClick=${() => prevId && goTo(prevId)} disabled=${!prevId} aria-label="Previous title">←</button>
        <button onClick=${() => nextId && goTo(nextId)} disabled=${!nextId} aria-label="Next title">→</button>
      </div>
    </div>

    ${!matched && html`<${MatchPanel} t=${t} onDone=${() => { refresh(); api(`/title/${id}/candidates`).then(setCands); }} />`}

    <div class="editor">
      <aside class="ed-side">
        <div class="preview ${pick ? 'picking' : ''}">
          <img ref=${previewImg} src=${previewSrc} alt="Poster preview" onClick=${eyedrop} key=${previewSrc} />
          ${pick && html`<div class="pick-hint">Click the poster to pick the ${pick[2].toLowerCase()}</div>`}
        </div>
        <div class="ed-caption">${mode === 'rotation' && editLook ? (editLook.look_id === t.today_look_id ? 'Today’s look' : 'Previewing a rotation look') : 'What Jellyfin gets'}${t.title.hands_off ? ' — but Hands off is on, so Studio won’t send it' : ''}</div>
        <div class="row" style="margin-top:12px">
          <button class="primary" onClick=${push} disabled=${busy || !matched || t.title.hands_off}>Push now</button>
          <button onClick=${() => call(`/title/${id}`, { method: 'PUT', body: { hands_off: !t.title.hands_off } }, t.title.hands_off ? 'Studio manages this title again' : 'Hands off: Studio will leave its poster alone')}>
            ${t.title.hands_off ? 'Manage again' : 'Hands off'}</button>
        </div>
        <p class="hint-sm">Changes save by themselves and reach Jellyfin tonight, or right away with Push now.</p>
        <div class="now-in-jf">
          <img src=${`/studio/api/thumb/${t.item.jf_id}?h=240&tag=${encodeURIComponent(t.item.jf_image_tag || '')}`} alt="" />
          <div><div style="font-weight:580">In Jellyfin now</div>
            <div class="hint-sm">${t.item.status === 'ok' ? 'Studio’s poster' : t.item.status === 'new' ? 'Not sent by Studio yet' : t.item.status}</div>
            ${looks.length || t.never.poster.length || t.never.logo.length || Object.keys(t.title.style).length
              ? html`<button class="link danger" style="margin-top:6px" onClick=${() => confirm('Forget every choice for this title (looks, Never lists, colours)?') && call(`/title/${id}/reset`, { method: 'POST', body: {} }, 'Back to automatic')}>Reset this title</button>` : ''}
          </div>
        </div>
      </aside>

      <section class="ed-main">
        <div class="mode-pick">${MODES.map(([m, label, help]) => html`<button class=${mode === m ? 'on' : ''} onClick=${() => mode !== m && setMode(m)} disabled=${busy || !matched}>
          <span class="t">${label}</span><span class="d">${help}</span></button>`)}</div>

        ${mode === 'rotation' && html`<div class="card rot">
          <h3>In the rotation (${rotation.length})</h3>
          ${rotation.length ? html`<div class="rot-strip">${rotation.map(l => html`<button class="rot-item ${editLook && editLook.look_id === l.look_id ? 'on' : ''}" onClick=${() => { setSel(l.look_id); setLocalStyle(null); }}>
              <img src=${l.poster ? thumbUrl(l.poster, l.crop ? 'backdrops' : 'posters') : ''} alt="" referrerpolicy="no-referrer" class=${l.crop ? 'cropped' : ''} />
              ${l.look_id === t.today_look_id ? html`<span class="chip ok">Today</span>` : t.upcoming[0] === l.look_id ? html`<span class="chip">Next</span>` : ''}
            </button>`)}</div>
            <p class="hint-sm">Click one to change its logo and colours. Remove it with ↻ Rotate on its card below.</p>`
          : html`<p class="hint-sm">Nothing yet. Press ↻ Rotate on any poster below to add it.</p>`}
        </div>`}

        <div class="seg" role="tablist">
          ${[['posters', 'Posters'], ['logos', 'Logos'], ['look', 'Colours & layout']].map(([k, l]) => html`<button class=${tab === k ? 'on' : ''} onClick=${() => setTab(k)}>${l}</button>`)}
        </div>

        ${cands && cands.error && html`<div class="notice warn"><p>${cands.error}</p></div>`}

        ${tab === 'posters' && html`
          <div class="toolbar">
            <div class="seg small">${[['textless', 'No text'], ['titled', 'With title'], ['backdrops', 'Backdrops'], ['yours', 'Yours']].map(([k, l]) => html`<button class=${pkind === k ? 'on' : ''} onClick=${() => setPkind(k)}>${l}</button>`)}</div>
            ${pkind !== 'yours' && html`<select value=${source} onChange=${e => setSource(e.target.value)} aria-label="Source">
              <option value="all">All sources</option><option value="tmdb">TMDB</option><option value="fanart">Fanart</option><option value="tvdb">TVDB</option></select>`}
          </div>
          <div class="own-row">
            <label class="btn">Upload a poster<input type="file" accept="image/png,image/jpeg,image/webp" hidden onChange=${e => upload(e.target.files[0], 'poster')} /></label>
            <input type="url" placeholder="…or paste an image link (ThePosterDB download links work)" value=${link} onInput=${e => setLink(e.target.value)} />
            <button onClick=${() => useLink('poster')} disabled=${!link || busy}>Use link</button>
            <a class="hint-sm" target="_blank" rel="noopener noreferrer" href=${`https://theposterdb.com/search?term=${encodeURIComponent(t.item.name)}`}>ThePosterDB ↗</a>
          </div>
          ${pkind === 'titled' && html`<p class="hint-sm">These carry their own title, so Studio uses them as they are, with no logo on top.</p>`}
          ${pkind === 'backdrops' && html`<p class="hint-sm">Wide images: Studio asks you to frame a poster-shaped part of it.</p>`}
          ${!cands ? html`<div class="empty">Loading artwork…</div>` : shownPosters.length ? html`<div class="cands">${shownPosters.map(c => {
            const isNever = never.poster.has(c.path);
            return html`<${Card} key=${c.path} c=${c} kind=${kindOf} badges=${posterBadges(c)} dim=${isNever}
              actions=${html`
                <button onClick=${() => usePoster(c, kindOf)} disabled=${busy || isNever} title="Always use this poster">Pin</button>
                <button onClick=${() => toggleRotation(c, kindOf)} disabled=${busy || isNever} class=${rotation.some(l => l.poster === c.path) ? 'on' : ''} title="Add to (or remove from) the daily rotation">↻<span class="lbl"> Rotate</span></button>
                <button onClick=${() => toggleNever('poster', c.path)} disabled=${busy} class=${isNever ? 'on-bad' : ''} title="Never use this one automatically">${isNever ? 'Allow' : 'Never'}</button>`} />`;
          })}</div>` : html`<div class="empty">None here.</div>`}`}

        ${tab === 'logos' && html`
          <p class="hint-sm">The logo for ${lookLabel}.</p>
          <div class="own-row">
            <button class=${editLook && !editLook.logo ? 'on' : ''} onClick=${() => useLogo('')} disabled=${busy}>Automatic logo</button>
            <button class=${editLook && editLook.logo === 'text' ? 'on' : ''} onClick=${() => useLogo('text')} disabled=${busy}>Title as text</button>
            <label class="btn">Upload a PNG<input type="file" accept="image/png,image/webp" hidden onChange=${e => upload(e.target.files[0], 'logo')} /></label>
            <input type="url" placeholder="…or paste a logo link" value=${link} onInput=${e => setLink(e.target.value)} />
            <button onClick=${() => useLink('logo')} disabled=${!link || busy}>Use link</button>
          </div>
          ${!cands ? html`<div class="empty">Loading logos…</div>` : html`<div class="cands logos">${[...customLogos, ...all.logos].filter(c => source === 'all' || c.provider === source || c.provider === 'custom').map(c => {
            const isNever = never.logo.has(c.path);
            const used = editLook && editLook.logo === c.path;
            const b = [];
            if (used) b.push(['Used', 'info']);
            if (!used && autoLogo === c.path && (!editLook || !editLook.logo)) b.push(['Automatic pick', 'ok']);
            if (isNever) b.push(['Never', 'bad']);
            return html`<${Card} key=${c.path} c=${c} kind="logos" badges=${b} dim=${isNever}
              actions=${html`<button onClick=${() => useLogo(c.path)} disabled=${busy || isNever}>Use</button>
                <button onClick=${() => toggleNever('logo', c.path)} disabled=${busy} class=${isNever ? 'on-bad' : ''}>${isNever ? 'Allow' : 'Never'}</button>`} />`;
          })}</div>`}`}

        ${tab === 'look' && html`
          <p class="hint-sm">Editing ${lookLabel}. Anything left on Automatic follows the global style.</p>
          <div class="card">
            <h3 style="margin-bottom:10px">Colours</h3>
            ${COLORS.map(([key, param, label, help]) => {
              const v = colorValue(key, param);
              return html`<div class="color-row">
                <div class="grow"><div style="font-weight:580">${label}</div><div class="hint-sm">${help}</div></div>
                <input type="color" value=${v ? '#' + v : '#888888'} onInput=${e => setColor(key, param, e.target.value)} aria-label=${label} class=${v ? '' : 'unset'} />
                <button onClick=${() => setPick([key, param, label])} title="Pick from the poster">Pick</button>
                <button onClick=${() => setColor(key, param, null)} disabled=${!v}>${v ? 'Automatic' : 'Automatic ✓'}</button>
              </div>`;
            })}
            ${colorValue('logo', 'logo_color') && html`<label class="switch" style="padding-bottom:0">
              <input type="checkbox" checked=${(current.title ? current.title.logo_color_mode : current.colors.logo_mode) === 'tint'}
                onChange=${e => change(s => { if (s.title) { if (e.target.checked) s.title.logo_color_mode = 'tint'; else delete s.title.logo_color_mode; } else { if (e.target.checked) s.colors.logo_mode = 'tint'; else delete s.colors.logo_mode; } })} />
              <span><span class="t">Keep the logo's shading</span><span class="d" style="display:block">Off: one solid colour. On: dark and light parts keep their contrast.</span></span></label>`}
          </div>
          <div class="card">
            <h3 style="margin-bottom:10px">Layout</h3>
            ${STYLE_CONTROLS.map(ctl => {
              const raw = styleValue(ctl.key), v = raw !== undefined ? +raw : ctl.def;
              return html`<div class="slider-row">
                <label for=${ctl.key}>${ctl.label}</label>
                <input id=${ctl.key} type="range" min=${ctl.min} max=${ctl.max} step=${ctl.step} value=${v} onInput=${e => setStyle(ctl.key, e.target.value)} />
                <span class="val">${ctl.fmt(v)}${raw === undefined ? ' (global)' : ''}</span>
                <button class="link" onClick=${() => setStyle(ctl.key, null)} disabled=${raw === undefined}>Reset</button>
              </div>`;
            })}
            <div class="slider-row">
              <label for="bg">Bottom fade</label>
              <select id="bg" value=${styleValue('bottom_gradient') || ''} onChange=${e => setStyle('bottom_gradient', e.target.value || null)}>
                <option value="">Global style</option><option value="off">Off</option><option value="low">Light</option><option value="medium">Medium</option><option value="high">Strong</option></select>
            </div>
            <label class="switch">
              <input type="checkbox" checked=${styleValue('show_award_sash') !== 'false'} onChange=${e => setStyle('show_award_sash', e.target.checked ? null : 'false')} />
              <span><span class="t">Show the notch</span><span class="d" style="display:block">The label at the top (awards, new season, …).</span></span></label>
            ${editLook && editLook.crop && html`<button onClick=${() => setCrop({ path: editLook.poster, initial: Object.fromEntries(['x', 'y', 'zoom'].map((k, i) => [k, +editLook.crop.split(',')[i]])),
              then: v => call(`/looks/${editLook.look_id}`, { method: 'PUT', body: { crop: v } }, 'Frame saved') })}>Adjust the frame</button>`}
          </div>`}
      </section>
    </div>
    ${crop && html`<${CropDialog} src=${fullImageUrl(crop.path)} initial=${crop.initial}
      onClose=${() => setCrop(null)} onSave=${v => { const then = crop.then; setCrop(null); then(v); }} />`}`;
}
