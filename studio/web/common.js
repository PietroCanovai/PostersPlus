import { html, useState, useEffect } from './vendor/preact-htm.js';

export async function api(path, { method = 'GET', body, raw } = {}) {
  const opts = { method, headers: { 'X-Studio': '1' }, credentials: 'same-origin' };
  if (raw !== undefined) { opts.body = raw; opts.headers['Content-Type'] = raw.type || 'application/octet-stream'; }
  else if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers['Content-Type'] = 'application/json'; }
  const resp = await fetch('/studio/api' + path, opts);
  let data = null;
  try { data = await resp.json(); } catch (_) { /* empty body */ }
  if (resp.status === 401) window.dispatchEvent(new Event('studio:logout'));
  if (!resp.ok) throw new Error((data && data.detail) || `HTTP ${resp.status}`);
  return data;
}

let toastTimer;
export function toast(msg, bad = false) {
  window.dispatchEvent(new CustomEvent('studio:toast', { detail: { msg, bad } }));
}
export function Toast() {
  const [t, setT] = useState(null);
  useEffect(() => {
    const on = e => { setT(e.detail); clearTimeout(toastTimer); toastTimer = setTimeout(() => setT(null), 3500); };
    window.addEventListener('studio:toast', on);
    return () => window.removeEventListener('studio:toast', on);
  }, []);
  return t ? html`<div class="toast ${t.bad ? 'bad' : ''}" role="status">${t.msg}</div>` : null;
}

export const n = x => (x || 0).toLocaleString();
export function ago(ts) {
  if (!ts) return 'never';
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(ts * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}
export function when(ts) {
  return new Date(ts * 1000).toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}
export function duration(a, b) {
  if (!a || !b) return '';
  const s = Math.round(b - a);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}
export function go(hash) { location.hash = hash; }

// The list the Library last showed, so the editor's previous/next and review
// mode walk the same titles in the same order.
export const nav = { ids: [], label: '' };

// A candidate path → a URL the browser can show (full size, for cropping).
const isStage = p => /^https:\/\/([^/]+\.)?stagemedia\.me\//i.test(p);
const stageThumb = (p, w) => `/studio/api/stage-thumb?w=${w}&url=${encodeURIComponent(p)}`;

const frameUrl = (p, h) => { const [, item, n] = p.split(':'); return `/studio/api/thumb/${item}?type=Chapter/${n}&h=${h}`; };
export function fullImageUrl(path) {
  if (!path) return '';
  if (path.startsWith('jf-chapter:')) return frameUrl(path, 900);
  if (path.startsWith('custom:')) return '/custom-art/' + path.slice(7);
  if (isStage(path)) return stageThumb(path, 800);
  if (/^https?:/.test(path)) return path;
  return 'https://image.tmdb.org/t/p/w1280' + path;
}
export function thumbUrl(path, kind = 'posters') {
  if (!path) return '';
  if (path.startsWith('jf-chapter:')) return frameUrl(path, 300);
  if (path.startsWith('custom:')) return '/custom-art/' + path.slice(7);
  if (isStage(path)) return stageThumb(path, 342);
  if (/^https?:/.test(path)) {
    if (path.includes('assets.fanart.tv/fanart/')) return path.replace('/fanart/', '/preview/');
    return path;
  }
  const size = kind === 'logos' ? (path.endsWith('.svg') ? 'original' : 'w300') : kind === 'backdrops' ? 'w300' : 'w342';
  return `https://image.tmdb.org/t/p/${size}${path}`;
}
