import { html, render, useState, useEffect, useRef, useCallback } from './vendor/preact-htm.js';
import { api, toast, Toast, n, ago, when, duration } from './common.js';
import { Library } from './library.js';
import { Editor } from './editor.js';
import { Style } from './style.js';

function useHash() {
  const [h, setH] = useState(location.hash.slice(1) || 'library');
  useEffect(() => {
    const on = () => { setH(location.hash.slice(1) || 'library'); window.scrollTo(0, 0); };
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);
  return h;
}

const ACTION_LABEL = {
  uploaded: ['Uploaded', 'ok'], reverted: ['Put back (was replaced)', 'warn'], would_upload: ['Would upload', 'info'],
  unchanged: ['Unchanged', ''], skipped: ['Skipped', ''], error: ['Error', 'bad'],
};
const REASON = { new: 'first upload', changed: 'poster changed', reverted: 'Jellyfin had replaced it', forced: 'forced' };
const STATUS_LABEL = {
  ok: ['Up to date', 'ok'], new: ['Not uploaded yet', 'info'], needs_match: ['Needs a TMDB match', 'warn'],
  left_alone: ['Left alone', ''], error: ['Error', 'bad'],
};

// ── Login ────────────────────────────────────────────────────────────────────
function Login({ onIn, enabled }) {
  const [key, setKey] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  async function submit(e) {
    e.preventDefault();
    setBusy(true); setErr('');
    try { await api('/login', { method: 'POST', body: { key } }); onIn(); }
    catch (ex) { setErr(ex.message === 'Invalid admin key' ? 'That key is not right.' : ex.message); }
    setBusy(false);
  }
  return html`<form class="login" onSubmit=${submit}>
    <h1>PostersPlus <span style="color:var(--accent)">Studio</span></h1>
    ${enabled
      ? html`<p>Log in with the instance's admin key (ADMIN_KEY in the server's .env). This browser stays logged in for 30 days.</p>
        ${err && html`<p class="error">${err}</p>`}
        <input type="password" autocomplete="current-password" placeholder="Admin key" value=${key}
          onInput=${e => setKey(e.target.value)} autofocus />
        <button class="primary" disabled=${busy || !key}>${busy ? 'Checking…' : 'Log in'}</button>`
      : html`<p>The admin key isn't set on the server, so Studio is locked. Add <code>ADMIN_KEY</code> (12+ characters) to the server's <code>.env</code> and restart.</p>`}
  </form>`;
}

// ── Activity ─────────────────────────────────────────────────────────────────
function StatCards({ items, total }) {
  const cards = [
    ['Titles managed', total, ''],
    ['Up to date', items.ok, 'ok'],
    ['Not uploaded yet', items.new, 'muted'],
    ['Need a match', items.needs_match, 'warn'],
    ['Errors', items.error, 'bad'],
    ['Left alone', items.left_alone, 'muted'],
  ];
  return html`<div class="grid-stats">${cards.map(([l, v, c]) =>
    html`<div class="stat ${v ? c : 'muted'}"><div class="n">${n(v)}</div><div class="l">${l}</div></div>`)}</div>`;
}

function RunProgress({ p, onCancel }) {
  const pct = p.total ? Math.round((p.done / p.total) * 100) : 0;
  const counts = Object.entries(p.counts || {}).map(([k, v]) => `${v} ${(ACTION_LABEL[k] || [k])[0].toLowerCase()}`).join(' · ');
  return html`<div class="card">
    <div class="row" style="justify-content:space-between">
      <h2>${p.dry_run ? 'Preview run' : 'Run'} in progress</h2>
      <button class="danger" onClick=${onCancel}>Stop</button>
    </div>
    <div class="bar"><div style="width:${pct}%"></div></div>
    <div class="meta" style="color:var(--text-2)">${p.phase}${p.total ? ` — ${n(p.done)} of ${n(p.total)}` : ''}${p.current ? ` · ${p.current}` : ''}</div>
    ${counts && html`<div style="margin-top:6px;color:var(--text-2);font-size:14px">${counts}</div>`}
  </div>`;
}

function runSummary(r) {
  const c = r.counts || {};
  const parts = [];
  if (c.uploaded) parts.push(`${c.uploaded} uploaded`);
  if (c.reverted) parts.push(`${c.reverted} put back`);
  if (c.would_upload) parts.push(`${c.would_upload} would upload`);
  if (c.error) parts.push(`${c.error} errors`);
  if (c.unchanged) parts.push(`${c.unchanged} unchanged`);
  if (c.skipped) parts.push(`${c.skipped} skipped`);
  return parts.join(' · ') || (r.message || 'nothing to do');
}

function RunsList({ runs }) {
  if (!runs.length) return html`<div class="list"><div class="empty">No runs yet.</div></div>`;
  return html`<div class="list">${runs.map(r => {
    const chip = r.status === 'running' ? ['Running', 'info'] : r.status === 'failed' ? ['Failed', 'bad']
      : r.status === 'cancelled' ? ['Stopped', 'warn'] : (r.counts || {}).error ? ['Done with errors', 'warn'] : ['Done', 'ok'];
    return html`<div class="list-row click" onClick=${() => { location.hash = `run/${r.id}`; }}>
      <div class="grow">
        <div class="name">${when(r.started_at)} · ${r.trigger === 'schedule' ? 'Nightly' : r.trigger === 'apply' ? 'New style' : 'Manual'}${r.dry_run ? ' preview' : ''}${r.scope === 'selection' ? ' (selection)' : ''}</div>
        <div class="meta">${r.status === 'failed' ? r.message : runSummary(r)}${r.finished_at ? ` · took ${duration(r.started_at, r.finished_at)}` : ''}</div>
      </div>
      <span class="chip ${chip[1]}">${chip[0]}</span>
    </div>`;
  })}</div>`;
}

function Problems() {
  const [rows, setRows] = useState(null);
  useEffect(() => {
    Promise.all([api('/items?status=error'), api('/items?status=needs_match')])
      .then(([a, b]) => setRows([...a.items, ...b.items])).catch(() => setRows([]));
  }, []);
  if (!rows || !rows.length) return null;
  return html`<div class="card">
    <h2>Needs attention</h2>
    <p class="sub">Titles Studio couldn't do. Open one to fix it.</p>
    <div class="list">${rows.slice(0, 50).map(r => html`<div class="list-row click" onClick=${() => { location.hash = `title/${r.jf_id}`; }}>
      <div class="grow"><div class="name">${r.name}${r.year ? ` (${r.year})` : ''}</div>
        <div class="meta">${r.library_name}${r.last_error ? ` · ${r.last_error}` : ''}</div></div>
      <span class="chip ${STATUS_LABEL[r.status][1]}">${STATUS_LABEL[r.status][0]}</span>
    </div>`)}</div>
  </div>`;
}

function Activity({ status, refresh }) {
  const [busy, setBusy] = useState(false);
  if (!status) return html`<div class="empty">Loading…</div>`;
  const p = status.progress;
  async function start(dry) {
    setBusy(true);
    try { await api('/run', { method: 'POST', body: { dry_run: dry } }); refresh(); }
    catch (ex) { toast(ex.message, true); }
    setBusy(false);
  }
  async function cancel() {
    try { await api('/run/cancel', { method: 'POST', body: {} }); toast('Stopping after the current titles…'); }
    catch (ex) { toast(ex.message, true); }
  }
  const next = status.next_run ? new Date(status.next_run) : null;
  return html`
    <div class="page-head">
      <div><h1>Activity</h1>
        <p>${status.schedule_enabled
          ? html`Next nightly run: <strong>${next.toLocaleString(undefined, { weekday: 'long', hour: '2-digit', minute: '2-digit' })}</strong>`
          : 'The nightly run is off.'} Library last read ${ago(status.last_scan_at)}.</p></div>
      <div class="row">
        <button onClick=${() => start(true)} disabled=${busy || p.running || !status.configured}
          title="Renders everything and shows what would change, without touching Jellyfin">Preview run</button>
        ${status.uploads_enabled && html`<button class="primary" onClick=${() => start(false)} disabled=${busy || p.running || !status.configured}>Run now</button>`}
      </div>
    </div>
    ${!status.configured && html`<div class="notice warn"><p>Connect Studio to Jellyfin to get started.</p>
      <button class="primary" onClick=${() => { location.hash = 'settings'; }}>Open Settings</button></div>`}
    ${status.configured && !status.uploads_enabled && html`<div class="notice info"><p><strong>Uploads are off.</strong> Runs are previews: they render every poster and show what would change, but never touch Jellyfin. Turn uploads on in Settings when the previews look right.</p></div>`}
    ${p.running && html`<${RunProgress} p=${p} onCancel=${cancel} />`}
    <${StatCards} items=${status.items} total=${status.total} />
    <${Problems} key=${status.runs[0] && status.runs[0].id} />
    <div class="card"><h2 style="margin-bottom:14px">Recent runs</h2><${RunsList} runs=${status.runs} /></div>`;
}

function RunDetail({ id }) {
  const [run, setRun] = useState(null);
  const [all, setAll] = useState(false);
  useEffect(() => { api(`/runs/${id}`).then(setRun).catch(ex => toast(ex.message, true)); }, [id]);
  if (!run) return html`<div class="empty">Loading…</div>`;
  const shown = all ? run.items : run.items.filter(i => i.action !== 'unchanged');
  return html`
    <div class="page-head"><div>
      <a href="#activity">← Activity</a>
      <h1 style="margin-top:8px">${when(run.started_at)}</h1>
      <p>${run.trigger === 'schedule' ? 'Nightly run' : 'Manual run'}${run.dry_run ? ' (preview, nothing uploaded)' : ''} · ${runSummary(run)}${run.finished_at ? ` · took ${duration(run.started_at, run.finished_at)}` : ''}</p>
    </div></div>
    ${run.status === 'failed' && html`<div class="notice bad"><p>${run.message}</p></div>`}
    <div class="row" style="margin-bottom:12px">
      <label class="switch" style="padding:0"><input type="checkbox" checked=${all} onChange=${e => setAll(e.target.checked)} />
        <span class="t">Show unchanged titles too</span></label>
    </div>
    <div class="list">${shown.length ? shown.map(i => {
      const [label, cls] = ACTION_LABEL[i.action] || [i.action, ''];
      return html`<div class="list-row"><div class="grow"><div class="name">${i.name}</div>
        ${i.detail && html`<div class="meta">${REASON[i.detail] || i.detail}</div>`}</div>
        <span class="chip ${cls}">${label}</span></div>`;
    }) : html`<div class="empty">${run.items.length ? 'Every title was already up to date.' : 'No titles in this run.'}</div>`}</div>`;
}

// ── Settings ─────────────────────────────────────────────────────────────────
function Section({ title, sub, children }) {
  return html`<div class="card"><h2>${title}</h2>${sub && html`<p class="sub">${sub}</p>`}${children}</div>`;
}

function JellyfinSettings({ s, reload }) {
  const [url, setUrl] = useState(s.jellyfin_url || '');
  const [key, setKey] = useState('');
  const [test, setTest] = useState(null);
  const [busy, setBusy] = useState(false);
  async function doTest() {
    setBusy(true); setTest(null);
    try { setTest(await api('/jellyfin/test', { method: 'POST', body: { jellyfin_url: url, jellyfin_api_key: key || undefined } })); }
    catch (ex) { setTest({ ok: false, error: ex.message }); }
    setBusy(false);
  }
  async function save() {
    setBusy(true);
    try { await api('/settings', { method: 'PUT', body: { jellyfin_url: url, jellyfin_api_key: key || undefined } }); setKey(''); toast('Jellyfin connection saved'); reload(); }
    catch (ex) { toast(ex.message, true); }
    setBusy(false);
  }
  return html`<${Section} title="Jellyfin" sub="Where your library lives. Studio talks to it from the server; your browser never sees the key.">
    <div class="field"><label for="jf-url">Server address</label>
      <input id="jf-url" type="url" placeholder="http://192.168.1.55:8096" value=${url} onInput=${e => setUrl(e.target.value)} /></div>
    <div class="field"><label for="jf-key">API key</label>
      <input id="jf-key" type="password" autocomplete="off" placeholder=${s.jellyfin_api_key_set ? `Saved (${s.jellyfin_api_key_hint}) — type to replace` : 'Paste an API key'}
        value=${key} onInput=${e => setKey(e.target.value)} />
      <div class="hint">In Jellyfin: Dashboard → API Keys → +. You can reuse the one the old sync script uses.</div></div>
    ${test && html`<div class="notice ${test.ok ? 'ok' : 'bad'}"><p>${test.ok
      ? html`Connected to <strong>${test.server.name}</strong> (Jellyfin ${test.server.version}), ${test.libraries} libraries.`
      : test.error}</p></div>`}
    <div class="row"><button onClick=${doTest} disabled=${busy || !url}>Test connection</button>
      <button class="primary" onClick=${save} disabled=${busy || !url}>Save</button></div>
  <//>`;
}

function LibrarySettings() {
  const [libs, setLibs] = useState(null);
  const [err, setErr] = useState('');
  useEffect(() => { api('/libraries').then(d => setLibs(d.libraries)).catch(ex => setErr(ex.message)); }, []);
  function update(id, patch) { setLibs(libs.map(l => l.id === id ? { ...l, ...patch } : l)); }
  async function save() {
    const body = {}; for (const l of libs) body[l.id] = { enabled: l.enabled, unmatched: l.unmatched };
    try { await api('/libraries', { method: 'PUT', body: { libraries: body } }); toast('Libraries saved. The next run reads them again.'); }
    catch (ex) { toast(ex.message, true); }
  }
  return html`<${Section} title="Libraries" sub="Which Jellyfin libraries Studio manages, and what to do with titles no database knows (like YouTube concert videos).">
    ${err ? html`<p class="error">${err}</p>` : !libs ? html`<div class="empty">Loading…</div>` : html`
      <div class="list" style="margin-bottom:14px">${libs.map(l => html`<div class="list-row">
        <label class="switch" style="padding:0;flex:1"><input type="checkbox" checked=${l.enabled} onChange=${e => update(l.id, { enabled: e.target.checked })} />
          <span><span class="t">${l.name}</span><span class="d" style="display:block">${l.type}${l.items ? ` · ${n(l.items)} titles` : ''}</span></span></label>
        <select disabled=${!l.enabled} value=${l.unmatched} onChange=${e => update(l.id, { unmatched: e.target.value })} aria-label="Unmatched titles in ${l.name}">
          <option value="flag">Unmatched: show under Needs attention</option>
          <option value="leave">Unmatched: leave alone</option>
        </select></div>`)}</div>
      <button class="primary" onClick=${save}>Save libraries</button>`}
  <//>`;
}

function SyncSettings({ s, reload }) {
  async function put(body, msg) {
    try { await api('/settings', { method: 'PUT', body }); toast(msg); reload(); }
    catch (ex) { toast(ex.message, true); }
  }
  return html`<${Section} title="Sync" sub="How and when posters reach Jellyfin.">
    <label class="switch"><input type="checkbox" checked=${s.uploads_enabled}
        onChange=${e => put({ uploads_enabled: e.target.checked }, e.target.checked ? 'Uploads on: runs now update Jellyfin' : 'Uploads off: runs are previews')} />
      <span><span class="t">Upload posters to Jellyfin</span>
        <span class="d" style="display:block">Off: every run is a preview. On: a poster is uploaded only when it actually changed, or when Jellyfin replaced ours.</span></span></label>
    <label class="switch"><input type="checkbox" checked=${s.schedule_enabled}
        onChange=${e => put({ schedule_enabled: e.target.checked }, e.target.checked ? `Nightly run on, at ${s.schedule_time}` : 'Nightly run off')} />
      <span><span class="t">Run every night</span>
        <span class="d" style="display:block">If the server was off at that time, the run happens as soon as it's back.</span></span></label>
    <div class="row" style="margin:14px 0 4px">
      <div class="field" style="margin:0"><label for="sched">Time (${s.timezone})</label>
        <input id="sched" type="time" value=${s.schedule_time} style="width:140px"
          onChange=${e => put({ schedule_time: e.target.value }, `Nightly run at ${e.target.value}`)} /></div>
      <div class="field" style="margin:0"><label for="res">Poster size</label>
        <select id="res" value=${s.resolution} onChange=${e => put({ resolution: +e.target.value }, 'Poster size saved. The next run re-uploads every poster.')}>
          ${s.resolutions.map(r => html`<option value=${r}>${r} × ${r * 1.5}${r === 1000 ? ' (recommended)' : ''}</option>`)}
        </select></div>
    </div>
    ${s.resolution > s.server_max_resolution && html`<div class="notice warn" style="margin-top:10px"><p>
      The server only allows posters up to <strong>${s.server_max_resolution} wide</strong>, so they come out at that size.
      Set <code>MAX_POSTER_RESOLUTION=${s.resolution}</code> in the server's compose file and run the update script.</p></div>`}
  <//>`;
}

function StageSettings({ s, reload }) {
  const [key, setKey] = useState('');
  async function save(value) {
    try {
      await api('/settings', { method: 'PUT', body: { stagemedia_key: value } });
      setKey(''); reload();
      toast(value ? 'StageMedia key saved. Run a preview from Activity to pick up your theatre titles.' : 'StageMedia key removed');
    } catch (ex) { toast(ex.message, true); }
  }
  return html`<${Section} title="Theatre (StageMedia)" sub="Recordings the Encora plugin imports have no TMDB entry. With a StageMedia key, Studio makes their posters from StageMedia's artwork.">
    <div class="field"><label for="sm-key">StageMedia API key</label>
      <input id="sm-key" type="password" autocomplete="off" value=${key} onInput=${e => setKey(e.target.value)}
        placeholder=${s.stagemedia_key_set ? `Saved (${s.stagemedia_key_hint}) — type to replace` : 'The key your Encora plugin uses'} />
      <div class="hint-sm">Also switch the Theatre library on under Libraries.</div></div>
    <div class="row"><button class="primary" onClick=${() => save(key)} disabled=${!key}>Save</button>
      ${s.stagemedia_key_set && html`<button class="danger" onClick=${() => save('')}>Remove</button>`}</div>
  <//>`;
}

function StyleSettings() {
  return html`<${Section} title="Poster style" sub="The look every poster gets, with live previews on your own titles.">
    <button onClick=${() => { location.hash = 'style'; }}>Open the style editor</button>
  <//>`;
}

function VersionCard() {
  const [v, setV] = useState(null);
  useEffect(() => { api('/version').then(setV).catch(() => {}); }, []);
  if (!v) return null;
  return html`<${Section} title="Version">
    <p style="margin:0 0 8px">Running <code>${v.commit.slice(0, 7)}</code>, built ${v.built === 'unknown' ? 'locally' : new Date(v.built).toLocaleString()}.</p>
    ${v.update_available
      ? html`<div class="notice info"><p><strong>An update is available</strong>${v.latest_message ? `: ${v.latest_message}` : ''}. On the server run <code>${v.update_command}</code>.</p></div>`
      : v.latest ? html`<p style="margin:0;color:var(--text-2)">Up to date.</p>` : null}
  <//>`;
}

function Settings({ onLogout }) {
  const [s, setS] = useState(null);
  const reload = useCallback(() => api('/settings').then(setS).catch(ex => toast(ex.message, true)), []);
  useEffect(() => { reload(); }, []);
  if (!s) return html`<div class="empty">Loading…</div>`;
  return html`
    <div class="page-head"><div><h1>Settings</h1><p>Changes apply straight away; no restart needed.</p></div>
      <button onClick=${() => { location.hash = 'activity'; }}>Go to Activity</button></div>
    <${JellyfinSettings} s=${s} reload=${reload} />
    ${s.jellyfin_url && s.jellyfin_api_key_set && html`<${LibrarySettings} />`}
    <${SyncSettings} s=${s} reload=${reload} />
    <${StageSettings} s=${s} reload=${reload} />
    <${StyleSettings} />
    <${VersionCard} />
    <${Section} title="Advanced" sub="The original PostersPlus pages, for everything Studio doesn't cover yet.">
      <div class="row"><a href="/admin" target="_blank"><button>Admin dashboard</button></a>
        <a href="/" target="_blank"><button>Configurator</button></a>
        <button class="danger" onClick=${onLogout}>Log out</button></div>
    <//>`;
}

// ── App ──────────────────────────────────────────────────────────────────────
function App() {
  const [session, setSession] = useState(null);
  const [status, setStatus] = useState(null);
  const route = useHash();
  const timer = useRef(null);

  const checkSession = () => api('/session').then(setSession).catch(() => setSession({ logged_in: false, enabled: true }));
  useEffect(() => {
    checkSession();
    const out = () => setSession(s => ({ ...(s || {}), logged_in: false }));
    window.addEventListener('studio:logout', out);
    return () => window.removeEventListener('studio:logout', out);
  }, []);

  const refresh = useCallback(async () => {
    try { setStatus(await api('/status')); } catch (_) { /* shown by the next poll */ }
  }, []);
  useEffect(() => {
    if (!session || !session.logged_in) return undefined;
    refresh();
    const tick = () => { refresh(); timer.current = setTimeout(tick, status && status.progress.running ? 2500 : 15000); };
    timer.current = setTimeout(tick, 2500);
    return () => clearTimeout(timer.current);
  }, [session, status && status.progress.running]);
  // Opening a page shows its current state, not the last poll's (e.g. right
  // after saving the Jellyfin connection in Settings).
  useEffect(() => { if (session && session.logged_in) refresh(); }, [route]);

  if (!session) return null;
  if (!session.logged_in) return html`<${Login} enabled=${session.enabled} onIn=${checkSession} /><${Toast} />`;

  async function logout() { await api('/logout', { method: 'POST', body: {} }).catch(() => {}); setSession({ ...session, logged_in: false }); }
  const [path, query] = route.split('?');
  const [page, arg] = path.split('/');
  const problems = status ? (status.items.error || 0) : 0;
  const nav = (id, label, extra) => html`<a href="#${id}" class=${page === id || (id === 'activity' && page === 'run') || (id === 'library' && page === 'title') ? 'active' : ''}>${label}${extra}</a>`;
  return html`<div class="shell">
    <nav class="nav">
      <div class="brand">Posters+ <span>Studio</span></div>
      ${nav('library', 'Library', status && status.items.needs_match ? html`<span class="chip warn">${status.items.needs_match}</span>` : '')}
      ${nav('style', 'Style', status && status.style_draft ? html`<span class="chip warn" title="Changes not applied yet">draft</span>` : '')}
      ${nav('activity', 'Activity', problems ? html`<span class="chip bad">${problems}</span>` : (status && status.progress.running ? html`<span class="chip info">running</span>` : ''))}
      ${nav('settings', 'Settings', '')}
      <div class="spacer"></div>
      <div class="foot">${status ? `v ${status.version.commit.slice(0, 7)}` : ''}</div>
    </nav>
    <main class="main">
      ${page === 'settings' ? html`<${Settings} onLogout=${logout} />`
        : page === 'run' ? html`<${RunDetail} id=${arg} />`
        : page === 'activity' ? html`<${Activity} status=${status} refresh=${refresh} />`
        : page === 'style' ? html`<${Style} refreshStatus=${refresh} />`
        : page === 'title' ? html`<${Editor} id=${arg} review=${query === 'review'} key=${arg} />`
        : status && !status.configured ? html`<${Activity} status=${status} refresh=${refresh} />`
        : html`<${Library} />`}
    </main>
    <${Toast} />
  </div>`;
}

render(html`<${App} />`, document.getElementById('app'));
