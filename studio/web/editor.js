import { html, useState, useEffect, useRef, useCallback } from './vendor/preact-htm.js';
import { api, toast, go, nav, thumbUrl, fullImageUrl, ago } from './common.js';
import { chipsFor } from './library.js';
import { sashName } from './notch.js';
import { StyleControls, GROUPS, THUMB_GROUPS } from './controls.js';

const PROVIDERS = { tmdb: 'TMDB', fanart: 'Fanart', tvdb: 'TVDB', custom: 'Yours', stagemedia: 'StageMedia', frame: 'Frame' };
// Curated sites without an API: opened in a new tab, their images added by link.
const POSTER_SITES = [['ThePosterDB', 'https://theposterdb.com/search?term=']];
const FRAME_SITES = [['FilmGrab', 'https://film-grab.com/?s='], ['Screencaps', 'https://movie-screencaps.com/?s=']];
const SLOTS = [['poster', 'Poster'], ['backdrop', 'Backdrop'], ['logo', 'Logo'], ['thumb', 'Thumb']];
const JF_TYPE = { poster: 'Primary', backdrop: 'Backdrop', logo: 'Logo', thumb: 'Thumb' };
// Old looks kept colours apart from their style; now everything is style parameters.
const LEGACY_COLORS = { tint: 'tint_color', fade: 'fade_color', sash_text: 'notch_text_color', logo: 'logo_color', logo_mode: 'logo_color_mode' };
const lookStyle = l => {
  const out = {};
  for (const [k, p] of Object.entries(LEGACY_COLORS)) if (l.colors && l.colors[k]) out[p] = l.colors[k];
  return { ...out, ...(l.style || {}) };
};
const ownTitle = (c, kind) => kind === 'posters' && (c.provider === 'custom' ? !!c.own_title : !!c.language);
// Exactly the rule's size first, unknown sizes (yours, frames) next, any other size last.
const fitRank = (c, r) => (!c.width ? 1 : c.width === r.min_w && c.height === r.min_h ? 0 : 2);
const byFit = (list, r) => list.map((c, i) => [fitRank(c, r), i, c]).sort((a, b) => a[0] - b[0] || a[1] - b[1]).map(x => x[2]);
const isFrame = c => c.provider === 'frame' || !!c.frame;
const cropToObj = s => (s ? Object.fromEntries(['x', 'y', 'zoom'].map((k, i) => [k, +s.split(',')[i]])) : null);
const cropToStr = c => (c ? `${c.x},${c.y},${c.zoom}` : '');

// ── Preview: keeps the last image until the next one has loaded ─────────────
function Preview({ src, shape, onPick, picking }) {
  const [shown, setShown] = useState(src);
  const [next, setNext] = useState(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => { if (src !== shown) { setNext(src); setFailed(false); } }, [src]);
  const img = useRef(null);
  return html`<div class="preview ${shape} ${picking ? 'picking' : ''}">
    ${shown && html`<img ref=${img} src=${shown} alt="" onClick=${e => onPick && onPick(e, img.current)} crossorigin="anonymous"
      onError=${() => { setShown(null); setFailed(true); }} />`}
    ${next && html`<img class="loading" src=${next} alt="" onLoad=${() => { setShown(next); setNext(null); }}
      onError=${() => { setNext(null); setShown(null); setFailed(true); }} />`}
    ${next && html`<span class="spin" aria-label="Loading"></span>`}
    ${failed && !next && html`<span class="pv-empty">Nothing to show</span>`}
  </div>`;
}

// ── Framing: a window of *aspect* on an image; darkens only the image ───────
function CropDialog({ src, aspect, initial, actions, onClose }) {
  const [crop, setCrop] = useState({ x: 0.5, y: 0.5, zoom: 1, ...(initial || {}) });
  const img = useRef(null);
  const [geo, setGeo] = useState(null);
  const measure = useCallback(() => {
    const el = img.current; if (!el || !el.naturalWidth) return;
    const dw = el.clientWidth, dh = el.clientHeight;
    const cw = Math.min(dw, dh * aspect) / crop.zoom, ch = cw / aspect;
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
    const sl = inside ? geo.left : e.clientX - rect.left - geo.cw / 2, st = inside ? geo.top : e.clientY - rect.top - geo.ch / 2;
    const x0 = e.clientX, y0 = e.clientY;
    const move = ev => {
      const left = Math.min(Math.max(sl + ev.clientX - x0, 0), geo.dw - geo.cw);
      const top = Math.min(Math.max(st + ev.clientY - y0, 0), geo.dh - geo.ch);
      setCrop(c => ({ ...c, x: geo.dw > geo.cw ? left / (geo.dw - geo.cw) : 0.5, y: geo.dh > geo.ch ? top / (geo.dh - geo.ch) : 0.5 }));
    };
    move(e);
    stage.setPointerCapture(e.pointerId);
    stage.onpointermove = move;
    stage.onpointerup = stage.onpointercancel = () => { stage.onpointermove = null; };
  }
  const value = () => ({ x: +crop.x.toFixed(4), y: +crop.y.toFixed(4), zoom: +crop.zoom.toFixed(3) });
  return html`<div class="veil" onClick=${e => { if (e.target === e.currentTarget) onClose(); }}>
    <div class="dialog wide" role="dialog" aria-label="Frame">
      <div class="crop-stage" onPointerDown=${drag}>
        <img ref=${img} src=${src} alt="" referrerpolicy="no-referrer" draggable="false" onLoad=${measure} />
        ${geo && html`<div class="crop-win" style=${`left:${geo.left}px;top:${geo.top}px;width:${geo.cw}px;height:${geo.ch}px`}></div>`}
      </div>
      <div class="crop-bar">
        <label class="zoom">Zoom <input type="range" min="1" max="4" step="0.05" value=${crop.zoom} onInput=${e => setCrop(c => ({ ...c, zoom: +e.target.value }))} />
          <span>${crop.zoom.toFixed(1)}×</span></label>
        <div class="row">
          <button onClick=${onClose}>Cancel</button>
          ${actions.map(a => html`<button class=${a.primary ? 'primary' : ''} onClick=${() => { onClose(); a.run(value()); }}>${a.label}</button>`)}
        </div>
      </div>
    </div></div>`;
}

// The chosen image's own size: from the provider's data, or measured for your uploads.
function SizeLine({ c, path, framed }) {
  const [measured, setMeasured] = useState(null);
  const known = c && c.width ? `${c.width}×${c.height}` : '';
  useEffect(() => {
    setMeasured(null);
    if (known || !path || !path.startsWith('custom:')) return;
    const im = new Image();
    im.onload = () => setMeasured(`${im.naturalWidth}×${im.naturalHeight}`);
    im.src = fullImageUrl(path);
  }, [path, known]);
  const size = known || measured;
  if (!path) return null;
  const src = c ? (PROVIDERS[c.provider] || c.provider) : path.startsWith('custom:') ? 'Yours' : path.startsWith('jf-chapter:') ? 'Frame' : '';
  const bits = [src, size, framed ? 'framed' : ''].filter(Boolean);
  return bits.length ? html`<div class="ed-size">${bits.join(' · ')}</div>` : null;
}

// A logo made from text: previewed live, saved into the title's own logos.
let fontList = null;
function TextLogoDialog({ id, initial, onSaved, onClose }) {
  const [o, setO] = useState({ text: initial, font: 'bebas', color: 'ffffff', upper: false, outline: false, shadow: true, spacing: 0 });
  const [fontsList, setFonts] = useState(fontList);
  const [src, setSrc] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (!fontList) api('/textlogo/fonts').then(d => { fontList = d.fonts; setFonts(d.fonts); }).catch(() => {}); }, []);
  useEffect(() => {
    const h = setTimeout(() => {
      const q = new URLSearchParams({ ...o, upper: o.upper, outline: o.outline, shadow: o.shadow });
      setSrc(o.text.trim() ? `/studio/api/title/${id}/textlogo?${q}` : '');
    }, 250);
    return () => clearTimeout(h);
  }, [o]);
  useEffect(() => {
    const onKey = e => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
  const set = (k, v) => setO(x => ({ ...x, [k]: v }));
  async function save(use) {
    setBusy(true);
    try { const r = await api(`/title/${id}/textlogo`, { method: 'POST', body: o }); onClose(); await onSaved(r.path, use); }
    catch (ex) { toast(ex.message, true); }
    setBusy(false);
  }
  return html`<div class="veil" onClick=${e => { if (e.target === e.currentTarget) onClose(); }}>
    <div class="dialog" role="dialog" aria-label="Text logo">
      <h2 style="margin-bottom:12px">Text logo</h2>
      <div class="tl-preview">${src ? html`<img src=${src} alt="" />` : html`<span class="hint-sm">Type the text</span>`}</div>
      <div class="tl-form">
        <input type="text" maxlength="80" value=${o.text} onInput=${e => set('text', e.target.value)} aria-label="Text" />
        <div class="row">
          <select value=${o.font} onChange=${e => set('font', e.target.value)} aria-label="Font">
            ${(fontsList || [{ id: 'bebas', name: 'Bebas Neue' }]).map(f => html`<option value=${f.id}>${f.name}</option>`)}</select>
          <input type="color" value=${'#' + o.color} onInput=${e => set('color', e.target.value.slice(1))} aria-label="Colour" />
        </div>
        <div class="row tl-toggles">
          ${[['upper', 'Capitals'], ['outline', 'Outline'], ['shadow', 'Shadow']].map(([k, l]) => html`<label class="own-check">
            <input type="checkbox" checked=${o[k]} onChange=${e => set(k, e.target.checked)} /> ${l}</label>`)}
          <label class="own-check">Spacing <input type="range" min="0" max="0.4" step="0.02" value=${o.spacing} onInput=${e => set('spacing', +e.target.value)} /></label>
        </div>
      </div>
      <div class="row" style="justify-content:flex-end;margin-top:14px">
        <button onClick=${onClose}>Cancel</button>
        <button onClick=${() => save(false)} disabled=${busy || !o.text.trim()}>Add</button>
        <button class="primary" onClick=${() => save(true)} disabled=${busy || !o.text.trim()}>Use</button>
      </div>
    </div></div>`;
}

function MatchPanel({ t, onDone }) {
  const [q, setQ] = useState(t.item.name.replace(/\s*\(\d{4}\)\s*$/, ''));
  const [res, setRes] = useState(null);
  const type = t.item.jf_type === 'Series' ? 'tv' : 'movie';
  async function search(e) {
    if (e) e.preventDefault();
    try { setRes((await api(`/tmdb/search?type=${type}&q=${encodeURIComponent(q)}`)).results); } catch (ex) { toast(ex.message, true); }
  }
  useEffect(() => { search(); }, []);
  async function link(tmdb) {
    try { await api(`/items/${t.item.jf_id}/match`, { method: 'PUT', body: { tmdb_id: tmdb } }); onDone(); } catch (ex) { toast(ex.message, true); }
  }
  return html`<div class="card">
    <h2>Which title is this?</h2>
    <form class="row" onSubmit=${search} style="margin:10px 0"><input type="search" value=${q} onInput=${e => setQ(e.target.value)} style="flex:1" /><button class="primary">Search TMDB</button></form>
    ${res && (res.length ? html`<div class="list">${res.map(r => html`<div class="list-row">
        ${r.thumb ? html`<img src=${r.thumb} alt="" class="mini-poster" referrerpolicy="no-referrer" />` : html`<span class="mini-poster"></span>`}
        <div class="grow"><div class="name">${r.title}${r.year ? ` (${r.year})` : ''}</div><div class="meta">${r.overview}</div></div>
        <button onClick=${() => link(r.tmdb_id)}>This one</button></div>`)}</div>` : html`<div class="empty">Nothing found.</div>`)}
  </div>`;
}

// ── A candidate image ───────────────────────────────────────────────────────
function Card({ c, kind, focused, badges, onFocus, onFrame, children, extra }) {
  const meta = c.name || [PROVIDERS[c.provider] || c.provider, c.language || '', c.width ? `${c.width}×${c.height}` : ''].filter(Boolean).join(' · ');
  return html`<div class="cand ${kind} ${focused ? 'focused' : ''}">
    <button class="cand-img" onClick=${onFocus} title="Preview">
      <img loading="lazy" referrerpolicy="no-referrer" alt="" src=${c.thumb || thumbUrl(c.path, kind)}
        onError=${e => { if (e.target.src !== fullImageUrl(c.path)) e.target.src = fullImageUrl(c.path); }} />
      ${badges.length > 0 && html`<span class="cand-badges">${badges.map(([l, cl]) => html`<span class="chip ${cl}">${l}</span>`)}</span>`}
    </button>
    ${onFrame && html`<button class="frame-btn" onClick=${onFrame} title="Frame">⤢</button>`}
    <div class="cand-meta">${meta}</div>
    ${extra}
    <div class="cand-actions">${children}</div>
  </div>`;
}

function Seg({ value, options, onChange, small }) {
  return html`<div class="seg ${small ? 'small' : ''}" role="tablist">${options.map(([v, l, extra]) => html`<button role="tab"
    aria-selected=${value === v} class=${value === v ? 'on' : ''} disabled=${extra === 'disabled'} onClick=${() => value !== v && onChange(v)}>${l}</button>`)}</div>`;
}

// ── The editor ──────────────────────────────────────────────────────────────
export function Editor({ id, review }) {
  const [t, setT] = useState(null);
  const [cands, setCands] = useState(null);
  const [frames, setFrames] = useState(null);
  const [lib, setLib] = useState(null);
  const [slot, setSlot] = useState('poster');
  const [sub, setSub] = useState('art');
  const [thumbSub, setThumbSub] = useState('art');
  const [art, setArt] = useState('textless');
  const [source, setSource] = useState('all');
  const [focus, setFocus] = useState(null);         // {look: id} | {c, kind, crop}
  const [scope, setScope] = useState('look');
  const [draft, setDraft] = useState(null);         // unsaved style: {target, values}
  const [ts, setTs] = useState(Date.now());
  const [busy, setBusy] = useState(false);
  const [crop, setCrop] = useState(null);           // {src, aspect, initial, actions}
  const [pick, setPick] = useState(null);           // colour param being eyedropped
  const [link, setLink] = useState('');
  const [notch, setNotch] = useState(null);
  const [textLogo, setTextLogo] = useState(null);   // (path) => apply it here
  const saveTimer = useRef(null);

  const load = useCallback(async () => {
    try { setT(await api(`/title/${id}`)); } catch (ex) { toast(ex.message, true); }
  }, [id]);
  useEffect(() => {
    setT(null); setCands(null); setFrames(null); setFocus(null); setDraft(null); setNotch(null); setSlot('poster'); setSub('art');
    load();
    api(`/title/${id}/candidates`).then(setCands).catch(ex => setCands({ error: ex.message, candidates: { posters: [], backdrops: [], logos: [] } }));
  }, [id]);
  useEffect(() => { if (!lib) api('/style').then(setLib).catch(() => {}); }, []);
  const framesAsked = useRef(null);
  useEffect(() => {
    if ((art === 'frames' || slot === 'backdrop' || slot === 'thumb') && framesAsked.current !== id) {
      framesAsked.current = id;
      api(`/title/${id}/frames`).then(d => setFrames(d.frames)).catch(() => setFrames([]));
    }
  }, [art, slot, id]);

  // In a show's seasons, ← → step through the show and its seasons; elsewhere, the Library's list.
  const family = t && t.parent ? [t.parent, ...t.seasons.map(s => s.jf_id)] : null;
  const navList = family || nav.ids;
  const idx = navList.indexOf(id);
  const prevId = idx > 0 ? navList[idx - 1] : null;
  const nextId = idx >= 0 && idx < navList.length - 1 ? navList[idx + 1] : null;
  const reviewIdx = nav.ids.indexOf(t && t.parent ? t.parent : id);   // a season keeps its show's place
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
  const focusedLook = focus && focus.look ? looks.find(l => l.look_id === focus.look) : null;
  const editLook = focusedLook
    || (mode === 'pinned' ? pinned : mode === 'rotation' ? (rotation.find(l => l.look_id === t.today_look_id) || rotation[0]) : null);
  const all = (cands && cands.candidates) || { posters: [], backdrops: [], logos: [] };
  const autoPoster = cands && cands.auto && cands.auto.poster;
  const autoLogo = cands && cands.auto && cands.auto.logo;
  const mine = k => t.uploads.filter(u => u.kind === k).map(u => ({ path: u.path, provider: 'custom', language: null, own_title: u.own_title, name: u.name }));
  const libEff = lib ? { ...lib.defaults, ...lib.applied_params } : {};

  // ── Poster actions ──
  const lookFields = (c, kind, cropVal) => ({ poster: c.path, crop: cropVal || '', own_title: ownTitle(c, kind) });
  async function pin(c, kind, cropVal) {
    const fields = lookFields(c, kind, cropVal);
    if (pinned) {
      await call(`/looks/${pinned.look_id}`, { method: 'PUT', body: fields });
      if (mode !== 'pinned') await call(`/title/${id}`, { method: 'PUT', body: { mode: 'pinned', pinned_look_id: pinned.look_id } });
      setFocus({ look: pinned.look_id });
    } else {
      const r = await call(`/title/${id}/looks`, { method: 'POST', body: { ...fields, pin: true } });
      if (r && r.look) setFocus({ look: r.look.look_id });
    }
  }
  async function addToRotation(c, kind, cropVal) {
    const r = await call(`/title/${id}/looks`, { method: 'POST', body: { ...lookFields(c, kind, cropVal), in_rotation: true } });
    if (r && r.look) setFocus({ look: r.look.look_id });
  }
  async function toggleRotation(c, kind) {
    const existing = rotation.find(l => l.poster === c.path);
    if (existing) {
      await call(`/looks/${existing.look_id}`, { method: 'DELETE' });
      if (focus && focus.look === existing.look_id) setFocus(null);
    } else if (kind === 'backdrops') {
      frame(c, kind, 'rotate');
    } else addToRotation(c, kind);
  }
  function frame(c, kind, then) {
    const aspect = 2 / 3;
    const acts = then === 'rotate' ? [{ label: 'Add to rotation', primary: true, run: v => addToRotation(c, kind, cropToStr(v)) }]
      : then === 'pin' ? [{ label: 'Pin', primary: true, run: v => pin(c, kind, cropToStr(v)) }]
      : [{ label: 'Preview', run: v => setFocus({ c, kind, crop: cropToStr(v) }) },
         { label: '↻ Rotation', run: v => addToRotation(c, kind, cropToStr(v)) },
         { label: 'Pin', primary: true, run: v => pin(c, kind, cropToStr(v)) }];
    setCrop({ src: fullImageUrl(c.path), aspect, initial: focus && focus.c && focus.c.path === c.path ? cropToObj(focus.crop) : null, actions: acts });
  }
  function frameLook(l) {
    setCrop({ src: fullImageUrl(l.poster), aspect: 2 / 3, initial: cropToObj(l.crop),
      actions: [{ label: 'Save frame', primary: true, run: v => call(`/looks/${l.look_id}`, { method: 'PUT', body: { crop: cropToStr(v) } }) }] });
  }
  async function setMode(m) {
    if (m === 'pinned') {
      if (pinned) return call(`/title/${id}`, { method: 'PUT', body: { mode: 'pinned', pinned_look_id: pinned.look_id } });
      const base = editLook || rotation[0];
      const r = await call(`/title/${id}/looks`, { method: 'POST', body: base
        ? { poster: base.poster, crop: base.crop, own_title: base.own_title, logo: base.logo, style: lookStyle(base), pin: true }
        : { poster: '', logo: '', pin: true } });
      if (r && r.look) setFocus({ look: r.look.look_id });
      return null;
    }
    setFocus(null);
    return call(`/title/${id}`, { method: 'PUT', body: { mode: m } });
  }
  const toggleNever = (k, path) => call(`/title/${id}/never`, { method: 'PUT', body: { kind: k, ref: path, on: !never[k].has(path) } });
  async function useLogo(path, everywhere = false) {
    if (everywhere) return call(`/title/${id}/looks-logo`, { method: 'PUT', body: { logo: path } });
    if (editLook) return call(`/looks/${editLook.look_id}`, { method: 'PUT', body: { logo: path } });
    const r = await call(`/title/${id}/looks`, { method: 'POST', body: { poster: '', logo: path, pin: true } });
    if (r && r.look) setFocus({ look: r.look.look_id });
    return r;
  }

  // ── Your images ──
  const KIND_WORD = { poster: 'poster', backdrop: 'backdrop', logo: 'logo' };
  async function upload(files, k) {
    const list = [...(files || [])].filter(f => f && f.type.startsWith('image/'));
    if (!list.length) return;
    setBusy(true);
    let ok = 0;
    for (const f of list) {
      try { await api(`/title/${id}/image?kind=${k}&name=${encodeURIComponent(f.name.slice(0, 100))}`, { method: 'POST', raw: f }); ok += 1; }
      catch (ex) { toast(`${f.name}: ${ex.message}`, true); }
    }
    setBusy(false);
    await refresh();
    if (ok) { toast(`${ok} ${KIND_WORD[k]}${ok > 1 ? 's' : ''} added`); if (k !== 'logo' && slot === 'poster') setArt(k === 'backdrop' ? 'backdrops' : 'yours'); }
  }
  async function addLink(k) {
    if (!link.trim()) return;
    const r = await call(`/title/${id}/image-link`, { method: 'POST', body: { url: link.trim(), kind: k } }, 'Added');
    if (r) { setLink(''); if (k !== 'logo') setArt(k === 'backdrop' ? 'backdrops' : 'yours'); }
  }
  const drop = k => ({
    onDragOver: e => { e.preventDefault(); e.currentTarget.classList.add('drag'); },
    onDragLeave: e => e.currentTarget.classList.remove('drag'),
    onDrop: e => { e.preventDefault(); e.currentTarget.classList.remove('drag'); upload(e.dataTransfer.files, k); },
  });
  const removeUpload = c => confirm('Delete this image?') && call(`/title/${id}/uploads?path=${encodeURIComponent(c.path)}`, { method: 'DELETE' });
  const setOwnTitle = (c, on) => call(`/title/${id}/uploads`, { method: 'PUT', body: { path: c.path, own_title: on } });

  // ── Jellyfin's other images ──
  const setArtChoice = (k, body) => call(`/title/${id}/art/${k}`, { method: 'PUT', body });
  function frameArt(c, k) {
    setCrop({ src: fullImageUrl(c.path), aspect: 16 / 9, initial: null,
      actions: [{ label: 'Preview', run: v => setFocus({ art: c, crop: cropToStr(v) }) },
        { label: 'Pin', primary: true, run: v => setArtChoice(k, { mode: 'pinned', path: c.path, crop: cropToStr(v) }) }] });
  }

  // ── Style ──
  const styleTarget = scope === 'look' && editLook ? 'look' : 'title';
  const savedStyle = styleTarget === 'look' ? lookStyle(editLook) : t.title.style;
  const values = draft && draft.target === styleTarget && (styleTarget === 'title' || draft.look === editLook.look_id) ? draft.values : savedStyle;
  const inherited = styleTarget === 'look' ? { ...libEff, ...t.title.style } : libEff;
  const titleValues = draft && draft.target === 'title' ? draft.values : t.title.style;
  function setStyleKey(k, v, target = styleTarget) {
    const next = { ...(target === 'title' ? titleValues : values) };
    if (v === null || v === undefined) delete next[k]; else next[k] = String(v);
    const d = { target, look: editLook && editLook.look_id, values: next };
    setDraft(d);
    clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(async () => {
      try {
        if (d.target === 'look') await api(`/looks/${d.look}`, { method: 'PUT', body: { style: d.values, colors: {} } });
        else await api(`/title/${id}`, { method: 'PUT', body: { style: { ...d.values } } });
        await load(); setDraft(null); setTs(Date.now());
      } catch (ex) { toast(ex.message, true); }
    }, 600);
  }
  function eyedrop(e, img) {
    if (!pick || !img) return;
    const canvas = document.createElement('canvas');
    canvas.width = img.naturalWidth; canvas.height = img.naturalHeight;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(img, 0, 0);
    const r = img.getBoundingClientRect();
    const [cr, cg, cb] = ctx.getImageData(Math.floor((e.clientX - r.left) / r.width * img.naturalWidth),
      Math.floor((e.clientY - r.top) / r.height * img.naturalHeight), 1, 1).data;
    setStyleKey(pick, [cr, cg, cb].map(x => x.toString(16).padStart(2, '0')).join(''));
    setPick(null);
  }
  async function toggleSashOff(slotName) {
    const off = new Set(notch.off);
    if (off.has(slotName)) off.delete(slotName); else off.add(slotName);
    const style = { ...t.title.style };
    if (off.size) style.sash_off = [...off].join(','); else delete style.sash_off;
    await call(`/title/${id}`, { method: 'PUT', body: { style } });
    setNotch({ ...notch, off: [...off] });
  }
  async function push() {
    setBusy(true);
    try {
      const r = await api(`/title/${id}/push`, { method: 'POST', body: {} });
      const c = r.counts || {};
      toast(r.status !== 'done' ? (r.message || 'Push failed') : c.error ? `Failed: ${(r.items.find(i => i.action === 'error') || {}).detail || 'error'}`
        : c.uploaded || c.reverted ? 'Sent to Jellyfin' : c.skipped ? 'Skipped' : 'Jellyfin already has it', !!(c.error || r.status !== 'done'));
      await refresh();
    } catch (ex) { toast(ex.message, true); }
    setBusy(false);
  }
  async function markReviewed() {
    await api(`/title/${id}`, { method: 'PUT', body: { reviewed: true } }).catch(() => {});
    if (t.parent) return goTo(t.parent);   // a season: back to its show, still reviewing
    const i = nav.ids.indexOf(id);
    const next = i >= 0 && i < nav.ids.length - 1 ? nav.ids[i + 1] : null;
    if (next) goTo(next); else { toast('That was the last one'); await refresh(); }
  }

  // ── What the preview shows ──
  let previewSrc, shape = 'portrait', caption;
  if (slot === 'poster') {
    if (focus && focus.c) {
      const d = { poster: focus.c.path, crop: focus.crop || '', own_title: ownTitle(focus.c, focus.kind),
        logo: editLook ? editLook.logo : '', style: editLook ? lookStyle(editLook) : {} };
      previewSrc = `/studio/api/preview/${id}?w=500&look=${encodeURIComponent(JSON.stringify(d))}&_=${ts}`;
      caption = 'Preview · not saved';
    } else {
      previewSrc = `/studio/api/preview/${id}?w=500${editLook ? `&look_id=${editLook.look_id}` : ''}&_=${ts}`;
      caption = !editLook ? 'Automatic' : mode === 'rotation' ? (editLook.look_id === t.today_look_id ? 'Today' : `Look ${rotation.indexOf(editLook) + 1} of ${rotation.length}`) : 'Pinned';
    }
  } else {
    shape = slot === 'logo' ? 'logo' : 'wide';
    const ch = t.art[slot];
    const q = focus && focus.art ? `mode=pinned&path=${encodeURIComponent(focus.art.path)}&crop=${encodeURIComponent(focus.crop || '')}` : '';
    previewSrc = `/studio/api/preview-art/${id}/${slot}?${q}&_=${ts}`;
    caption = focus && focus.art ? 'Preview · not saved' : ch.mode === 'keep' ? 'Jellyfin keeps its own' : ch.mode === 'pinned' ? 'Pinned'
      : t.stage && slot !== 'thumb' ? 'Nothing pinned yet' : ch.library_on ? 'Automatic' : 'What Automatic would pick (off in Settings)';
  }

  // Which source image the preview shows, for its size.
  const byPath = new Map([...all.posters, ...all.backdrops, ...all.logos, ...(frames || [])].map(c => [c.path, c]));
  let sizeInfo = { path: '' };
  if (slot === 'poster') {
    const p = focus && focus.c ? focus.c.path : editLook ? editLook.poster : autoPoster && autoPoster.path;
    const framed = focus && focus.c ? !!focus.crop && focus.kind === 'backdrops' : !!(editLook && editLook.crop);
    sizeInfo = { path: p || '', c: (focus && focus.c && focus.c.path === p ? focus.c : null) || byPath.get(p), framed };
  } else {
    const ch = t.art[slot];
    const p = focus && focus.art ? focus.art.path : ch.mode === 'pinned' ? ch.path : '';
    sizeInfo = { path: p || '', c: byPath.get(p), framed: !!(focus && focus.art ? focus.crop : ch.crop) };
  }

  const chips = chipsFor({ ...t.item, mode, hands_off: !!t.title.hands_off, rotation: rotation.length, never: never.poster.size + never.logo.size, styled: false });

  // ── Poster: Art ──
  const artTabs = [['textless', 'No text'], ['titled', 'With title'], ['backdrops', 'Backdrops'], ['frames', 'Frames'], ['yours', `Yours${mine('poster').length ? ` ${mine('poster').length}` : ''}`]];
  let list = [], listKind = 'posters';
  const rule = t.art.rules.backdrop;
  if (art === 'backdrops') { list = byFit([...mine('backdrop'), ...all.backdrops], rule); listKind = 'backdrops'; }
  else if (art === 'frames') { list = (frames || []).map(f => ({ ...f, frame: true })); listKind = 'backdrops'; }
  else if (art === 'yours') list = mine('poster');
  else if (art === 'titled') list = all.posters.filter(c => c.language);
  else if (art === 'textless') list = all.posters.filter(c => !c.language);
  if (art !== 'frames' && art !== 'yours') list = list.filter(c => source === 'all' || c.provider === source || c.provider === 'custom');
  const posterBadges = c => {
    const b = [];
    if (mode === 'pinned' && pinned && pinned.poster === c.path) b.push(['Pinned', 'info']);
    if (rotation.some(l => l.poster === c.path)) b.push(['Rotation', 'info']);
    if (mode === 'auto' && autoPoster && autoPoster.path === c.path) b.push(['Auto', 'ok']);
    if (never.poster.has(c.path)) b.push(['Never', 'bad']);
    return b;
  };
  const isFocused = c => !!(focus && ((focus.c && focus.c.path === c.path) || (focus.art && focus.art.path === c.path)));
  const uploadKind = art === 'backdrops' || art === 'frames' ? 'backdrop' : 'poster';

  const uploadRow = (k, sites = [], onTextLogo = null) => html`<div class="own-row dropzone" ...${drop(k)}>
      <label class="btn">Upload<input type="file" multiple accept=${k === 'logo' ? 'image/png,image/webp' : 'image/png,image/jpeg,image/webp'} hidden
        onChange=${e => { upload(e.target.files, k); e.target.value = ''; }} /></label>
      <input type="url" placeholder=${k === 'logo' ? 'Logo link' : 'Image link'} value=${link} onInput=${e => setLink(e.target.value)} />
      <button onClick=${() => addLink(k)} disabled=${!link || busy}>Add</button>
      ${k === 'logo' && onTextLogo && html`<button onClick=${onTextLogo} disabled=${busy}>Text logo</button>`}
      ${sites.map(([label, url]) => html`<a class="hint-sm" target="_blank" rel="noopener noreferrer"
        href=${url + encodeURIComponent(t.item.name.replace(/\s*\(\d{4}\)\s*$/, ''))}>${label} ↗</a>`)}
    </div>`;

  const artPane = html`
    <div class="toolbar">
      <${Seg} small value=${art} onChange=${setArt} options=${artTabs} />
      ${['textless', 'titled', 'backdrops'].includes(art) && html`<select value=${source} onChange=${e => setSource(e.target.value)} aria-label="Source">
        <option value="all">All sources</option><option value="tmdb">TMDB</option><option value="fanart">Fanart</option><option value="tvdb">TVDB</option></select>`}
    </div>
    ${uploadRow(uploadKind, art === 'frames' ? FRAME_SITES : POSTER_SITES)}
    ${(art === 'frames' ? frames === null : !cands) ? html`<div class="empty">Loading…</div>`
      : list.length ? html`<div class="cands ${listKind}">${list.map(c => {
          const isNever = never.poster.has(c.path), inRot = rotation.some(l => l.poster === c.path);
          return html`<${Card} key=${c.path} c=${c} kind=${listKind} focused=${isFocused(c)} badges=${posterBadges(c)}
            onFocus=${() => setFocus({ c, kind: listKind, crop: listKind === 'backdrops' ? '0.5,0.5,1' : '' })}
            onFrame=${() => frame(c, listKind)}
            extra=${c.provider === 'custom' && listKind === 'posters' ? html`<label class="own-check"><input type="checkbox" checked=${!!c.own_title} onChange=${e => setOwnTitle(c, e.target.checked)} /> Has title</label>` : null}>
            <button onClick=${() => (listKind === 'backdrops' ? frame(c, listKind, 'pin') : pin(c, listKind))} disabled=${busy || isNever}>Pin</button>
            <button class=${inRot ? 'on' : ''} onClick=${() => toggleRotation(c, listKind)} disabled=${busy || isNever} title="Daily rotation">↻</button>
            ${c.provider === 'custom'
              ? html`<button onClick=${() => removeUpload(c)} disabled=${busy} title="Delete">✕</button>`
              : !isFrame(c) && html`<button class=${isNever ? 'on-bad' : ''} onClick=${() => toggleNever('poster', c.path)} disabled=${busy} title=${isNever ? 'Allow again' : 'Never use'}>⊘</button>`}
          </${Card}>`;
        })}</div>`
      : html`<div class="empty">${art === 'yours' ? 'Upload, paste a link or drop images here.'
        : art === 'frames' ? 'No frames. Jellyfin makes them when chapter image extraction is on for the library; the sites above have curated stills.' : 'None.'}</div>`}`;

  // ── Poster: Logo ──
  const logos = [...mine('logo'), ...all.logos].filter(c => source === 'all' || c.provider === source || c.provider === 'custom');
  const many = looks.length > 1;
  const logoPane = html`
    <div class="own-row">
      <button class=${editLook && !editLook.logo ? 'on' : ''} onClick=${() => useLogo('')} disabled=${busy}>Automatic</button>
      <button class=${editLook && editLook.logo === 'text' ? 'on' : ''} onClick=${() => useLogo('text')} disabled=${busy}>Title as text</button>
    </div>
    ${uploadRow('logo', [], () => setTextLogo(() => p => useLogo(p)))}
    ${!cands ? html`<div class="empty">Loading…</div>` : html`<div class="cands logos">${logos.map(c => {
      const isNever = never.logo.has(c.path), used = editLook && editLook.logo === c.path;
      const b = [];
      if (used) b.push(['Used', 'info']);
      if (!used && autoLogo === c.path && (!editLook || !editLook.logo)) b.push(['Auto', 'ok']);
      if (isNever) b.push(['Never', 'bad']);
      return html`<${Card} key=${c.path} c=${c} kind="logos" badges=${b} onFocus=${() => useLogo(c.path)}>
        <button onClick=${() => useLogo(c.path)} disabled=${busy || isNever}>Use</button>
        ${many && html`<button onClick=${() => useLogo(c.path, true)} disabled=${busy || isNever} title="Use on every look">All</button>`}
        ${c.provider === 'custom'
          ? html`<button onClick=${() => removeUpload(c)} disabled=${busy} title="Delete">✕</button>`
          : html`<button class=${isNever ? 'on-bad' : ''} onClick=${() => toggleNever('logo', c.path)} disabled=${busy} title=${isNever ? 'Allow again' : 'Never use'}>⊘</button>`}
      </${Card}>`;
    })}</div>`}`;

  // ── Poster: Style ──
  const extraGroups = t.stage ? [['Theatre', [
    { k: 'studio_template', t: 'select', label: 'Design', options: [['', 'Posters+'], ['playbill', 'Playbill']] },
    { k: 'playbill_venue', t: 'text', label: 'Venue', placeholder: 'Automatic' },
  ]]] : [];
  const stylePane = html`
    <${Seg} small value=${styleTarget} onChange=${setScope}
      options=${[['look', 'This poster', editLook ? '' : 'disabled'], ['title', 'Whole title']]} />
    ${lib ? html`<${StyleControls} values=${values} inherited=${inherited} from=${styleTarget === 'look' ? 'title' : 'library'}
      onSet=${setStyleKey} pickColor=${k => setPick(k)} groups=${[...GROUPS, ...extraGroups]} />` : html`<div class="empty">Loading…</div>`}
    ${!t.stage && html`<details class="labels" onToggle=${e => { if (e.target.open && !notch) api(`/title/${id}/notch`).then(setNotch).catch(ex => setNotch({ available: false, reason: ex.message })); }}>
      <summary>Notch labels for this title</summary>
      ${!notch ? html`<div class="empty">Loading…</div>` : !notch.available ? html`<p class="hint-sm">${notch.reason}</p>` : html`
        <p class="hint-sm">Now: <strong>${notch.shown ? notch.shown.label : 'nothing'}</strong></p>
        ${notch.candidates.length ? html`<div class="list sash-list">${notch.candidates.map(c => {
          const off = notch.off.includes(c.slot), gOff = !off && !notch.priority.includes(c.slot);
          return html`<div class="list-row ${off || gOff ? 'off' : ''}"><label class="switch" style="padding:0;flex:1">
            <input type="checkbox" checked=${!off && !gOff} disabled=${gOff} onChange=${() => toggleSashOff(c.slot)} />
            <span><span class="t">${c.label}</span><span class="d" style="display:block">${sashName(c.slot)}${gOff ? ' · off in Notch' : ''}</span></span></label></div>`;
        })}</div>` : html`<p class="hint-sm">No label applies right now.</p>`}`}
    </details>`}`;

  // ── Other images ──
  function otherPane(k) {
    const ch = t.art[k];
    const r = t.art.rules.backdrop;
    // Theatre has no backdrops of its own: StageMedia's art is offered too, framed to 16:9.
    const stageArt = t.stage ? all.posters.map(c => ({ ...c, needsFrame: true })) : [];
    const pool = k === 'logo' ? [...mine('logo'), ...all.logos]
      : byFit([...mine('backdrop'), ...all.backdrops, ...(frames || []).map(f => ({ ...f, frame: true })), ...stageArt], r);
    // A generated thumb: the style drawn on its art (automatic or pinned), with its own logo.
    const generated = k === 'thumb' && ch.mode !== 'keep' && t.art.rules.thumb.source === 'landscape';
    const part = generated ? thumbSub : 'art';
    const pinArt = c => (c.needsFrame ? frameArt(c, k)
      : setArtChoice(k, { mode: 'pinned', path: c.path, ...(generated && c.language ? { logo: 'none' } : {}) }));
    const artGrid = html`<div class="cands ${k === 'logo' ? 'logos' : 'backdrops'}">${pool.map(c => {
      const b = [];
      if (ch.mode === 'pinned' && ch.path === c.path) b.push(['Pinned', 'info']);
      if (k !== 'logo' && fitRank(c, r) === 2) b.push([`Not ${r.min_w}×${r.min_h}`, 'warn']);
      if (isFrame(c)) b.push(['Frame', '']);
      return html`<${Card} key=${c.path} c=${c} kind=${k === 'logo' ? 'logos' : 'backdrops'} focused=${isFocused(c)} badges=${b}
        onFocus=${() => (c.needsFrame ? frameArt(c, k) : setFocus({ art: c, crop: '' }))} onFrame=${k === 'logo' ? null : () => frameArt(c, k)}>
        <button onClick=${() => pinArt(c)} disabled=${busy}>Pin</button>
        ${c.provider === 'custom' && html`<button onClick=${() => removeUpload(c)} disabled=${busy} title="Delete">✕</button>`}
      </${Card}>`;
    })}</div>`;
    const logoGrid = html`
      <div class="own-row">
        ${[['', 'Poster’s'], ['text', 'Title as text'], ['none', 'None']].map(([v, l]) => html`<button class=${(ch.logo || '') === v ? 'on' : ''}
          onClick=${() => setArtChoice(k, { logo: v })} disabled=${busy} title=${v === 'none' ? 'The art already shows the title' : ''}>${l}</button>`)}
      </div>
      <div class="cands logos">${[...mine('logo'), ...all.logos].map(c => html`<${Card} key=${c.path} c=${c} kind="logos"
        badges=${ch.logo === c.path ? [['Used', 'info']] : []} onFocus=${() => setArtChoice(k, { logo: c.path })}>
        <button onClick=${() => setArtChoice(k, { logo: c.path })} disabled=${busy}>Use</button>
        ${c.provider === 'custom' && html`<button onClick=${() => removeUpload(c)} disabled=${busy} title="Delete">✕</button>`}</${Card}>`)}</div>`;
    return html`
      <${Seg} small value=${ch.mode} onChange=${m => (m === 'pinned' ? toast('Pick an image below') : setArtChoice(k, { mode: m }))}
        options=${[['auto', 'Automatic'], ['pinned', 'Pinned', ch.mode === 'pinned' ? '' : 'disabled'], ['keep', 'Keep Jellyfin’s']]} />
      ${ch.mode === 'auto' && (t.stage && k !== 'thumb' ? html`<p class="hint-sm">Theatre has no automatic ${k}: pin one below.</p>`
        : !ch.library_on && html`<p class="hint-sm">Automatic ${k}s are off in <a href="#settings">Settings</a>: Jellyfin’s stays until you pin one.</p>`)}
      ${generated && html`<${Seg} value=${thumbSub} onChange=${setThumbSub} options=${[['art', 'Art'], ['logo', 'Logo'], ['style', 'Style']]} />`}
      ${part === 'art' ? html`${uploadRow(k === 'logo' ? 'logo' : 'backdrop', k === 'logo' ? [] : FRAME_SITES,
          () => setTextLogo(() => p => setArtChoice('logo', { mode: 'pinned', path: p })))}${artGrid}`
        : part === 'logo' ? html`${uploadRow('logo', [], () => setTextLogo(() => p => setArtChoice('thumb', { logo: p })))}${logoGrid}`
        : lib && html`<${StyleControls} values=${titleValues} inherited=${libEff} from="library"
          onSet=${(key, v) => setStyleKey(key, v, 'title')} groups=${THUMB_GROUPS} />`}`;
  }

  const lookBar = slot === 'poster' && focusedLook ? html`<div class="look-bar">
      ${focusedLook.poster && html`<button onClick=${() => frameLook(focusedLook)}>⤢ Frame</button>`}
    </div>` : null;

  return html`
    ${review && html`<div class="notice info review-bar"><p>${reviewIdx >= 0 ? `${reviewIdx + 1} / ${nav.ids.length}` : ''}${t.parent ? ` · ${t.item.name}` : ''}</p>
      <div class="row"><button onClick=${() => go('library')}>Stop</button><button class="primary" onClick=${markReviewed}>Looks good ✓</button></div></div>`}
    <div class="ed-head">
      <div class="grow">
        ${t.parent_item
          ? html`<button class="back-show" onClick=${() => goTo(t.parent)}>← ${t.parent_item.name}</button>`
          : html`<div class="crumbs"><a href="#library">Library</a></div>`}
        <h1>${t.item.name}${t.item.year && !t.parent ? html` <span class="dim-text">${t.item.year}</span>` : ''}</h1>
        <div class="head-chips">${chips.map(([l, c]) => html`<span class="chip ${c}">${l}</span>`)}${t.item.pushed_at ? html`<span class="dim-text">sent ${ago(t.item.pushed_at)}</span>` : ''}</div>
      </div>
      <div class="row nav-btns"><button onClick=${() => prevId && goTo(prevId)} disabled=${!prevId} aria-label="Previous">←</button>
        <button onClick=${() => nextId && goTo(nextId)} disabled=${!nextId} aria-label="Next">→</button></div>
    </div>

    ${t.seasons.length > 0 && html`<div class="strip seasons">
      ${[{ jf_id: t.parent || t.item.jf_id, label: 'Show', tag: t.parent_item ? t.parent_item.jf_image_tag : t.item.jf_image_tag },
         ...t.seasons.map(s => ({ jf_id: s.jf_id, label: s.number === 0 ? 'SP' : `S${s.number}`, tag: s.jf_image_tag, name: s.name }))]
        .map(s => html`<button class="strip-item ${s.jf_id === id ? 'on' : ''}" onClick=${() => s.jf_id !== id && goTo(s.jf_id)} title=${s.name || 'The show'}>
          <img src=${`/studio/api/thumb/${s.jf_id}?h=240&tag=${encodeURIComponent(s.tag || '')}`} alt="" />
          <span class="chip ${s.label === 'Show' ? 'info' : ''}">${s.label}</span></button>`)}
    </div>`}

    ${!matched && html`<${MatchPanel} t=${t} onDone=${() => { refresh(); api(`/title/${id}/candidates`).then(setCands); }} />`}

    <div class="editor">
      <aside class="ed-side">
        <${Preview} src=${previewSrc} shape=${shape} picking=${!!pick} onPick=${eyedrop} />
        <div class="ed-caption">${pick ? 'Click the preview to pick a colour' : caption}</div>
        <${SizeLine} ...${sizeInfo} />
        ${lookBar}
        <div class="row side-actions">
          <button class="primary" onClick=${push} disabled=${busy || !matched || t.title.hands_off}>Push now</button>
          <button class=${t.title.hands_off ? 'on' : ''} onClick=${() => call(`/title/${id}`, { method: 'PUT', body: { hands_off: !t.title.hands_off } })}
            title="Studio leaves this title's images alone">Hands off</button>
        </div>
        <div class="now-in-jf">
          <img class=${shape} src=${`/studio/api/thumb/${t.item.jf_id}?h=240&type=${JF_TYPE[slot]}&_=${ts}`} alt="" onError=${e => { e.target.style.visibility = 'hidden'; }} />
          <div><div class="hint-sm" style="margin:0">In Jellyfin</div>
            ${(looks.length || t.never.poster.length || t.never.logo.length || Object.keys(t.title.style).length) ? html`<button class="link danger"
              onClick=${() => confirm('Forget every choice for this title?') && call(`/title/${id}/reset`, { method: 'POST', body: {} })}>Reset title</button>` : ''}</div>
        </div>
      </aside>

      <section class="ed-main">
        <${Seg} value=${slot} onChange=${v => { setSlot(v); setFocus(null); }} options=${SLOTS} />


        ${slot === 'poster' && html`
          <div class="mode-row">
            <${Seg} small value=${mode} onChange=${setMode} options=${[['auto', 'Automatic'], ['pinned', 'Pinned'], ['rotation', 'Daily rotation']]} />
            ${mode === 'rotation' && html`<span class="hint-sm">${rotation.length ? `${rotation.length} looks, shuffled daily` : 'Add posters with ↻'}</span>`}
          </div>
          ${(mode === 'rotation' ? rotation : mode === 'pinned' && pinned ? [pinned] : []).length > 0 && html`<div class="strip">
            ${(mode === 'rotation' ? rotation : [pinned]).map(l => html`<div class="strip-cell" key=${l.look_id}>
              <button class="strip-item ${editLook && editLook.look_id === l.look_id && !(focus && focus.c) ? 'on' : ''}"
                onClick=${() => { setFocus({ look: l.look_id }); setDraft(null); }}>
                <img src=${l.poster ? thumbUrl(l.poster, l.crop ? 'backdrops' : 'posters') : `/studio/api/preview/${id}?w=500&look_id=${l.look_id}`} alt="" referrerpolicy="no-referrer" />
                ${mode === 'rotation' && (l.look_id === t.today_look_id ? html`<span class="chip ok">Today</span>` : t.upcoming[0] === l.look_id ? html`<span class="chip">Next</span>` : '')}
              </button>
              ${mode === 'rotation' && html`<button class="strip-x" title="Remove from rotation" aria-label="Remove from rotation" disabled=${busy}
                onClick=${() => { if (focus && focus.look === l.look_id) setFocus(null); call(`/looks/${l.look_id}`, { method: 'DELETE' }); }}>✕</button>`}
            </div>`)}</div>`}
          <${Seg} value=${sub} onChange=${setSub} options=${[['art', 'Art'], ['logo', 'Logo'], ['style', 'Style']]} />
          ${cands && cands.error && html`<div class="notice warn"><p>${cands.error}</p></div>`}
          ${sub === 'art' ? artPane : sub === 'logo' ? logoPane : stylePane}`}
        ${slot !== 'poster' && otherPane(slot)}
      </section>
    </div>
    ${crop && html`<${CropDialog} ...${crop} onClose=${() => setCrop(null)} />`}
    ${textLogo && html`<${TextLogoDialog} id=${id} initial=${(t.parent_item ? t.parent_item.name : t.item.name).replace(/\s*\(\d{4}\)\s*$/, '')}
      onClose=${() => setTextLogo(null)} onSaved=${async (p, use) => { await refresh(); if (use) await textLogo(p); else toast('Added to your logos'); }} />`}`;
}
