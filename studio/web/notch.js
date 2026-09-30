import { html, useState, useEffect } from './vendor/preact-htm.js';
import { api, toast, go } from './common.js';

// What each label means and where PostersPlus gets it.
export const SASH_INFO = {
  watchlist: ['On your watchlist', 'The title is on the watchlist PostersPlus follows.', 'Your watchlist service (set WATCHLIST_SOURCE in the admin dashboard)'],
  wins: ['Oscar / Emmy winner', 'Won Best Picture (films) or Outstanding Drama, Comedy or Limited Series (TV).', 'MDBList award data'],
  gg_wins: ['Golden Globe winner', 'Won a top Golden Globe (film or TV drama, comedy, limited).', 'PostersPlus’s award data'],
  festival: ['Festival prize', 'Top prize at Cannes, Venice, Berlin, Locarno or Sundance (Palme d’Or, Golden Lion…).', 'PostersPlus’s festival data'],
  pic_noms: ['Oscar / Emmy nominee', 'Nominated for Best Picture or Outstanding series.', 'MDBList award data'],
  metacritic: ['Metacritic must-see', 'A Metacritic Must-See.', 'MDBList'],
  gg_noms: ['Golden Globe nominee', 'Nominated for a top Golden Globe.', 'PostersPlus’s award data'],
  studio: ['Notable studio', 'Made by a studio on your list below (A24, Ghibli, Pixar…).', 'TMDB production companies + your list'],
  director: ['Notable director', 'Directed by someone on your list below.', 'TMDB credits + your list'],
  cast: ['Notable cast', 'Stars someone on your list below.', 'TMDB credits + your list'],
  trending: ['Trending', 'In the top ranks of today’s trending list (“#3 Today”).', 'TMDB trending (refreshed daily)'],
  new_season: ['New season', 'A show’s season 2 or later just premiered or is about to.', 'TMDB episode dates'],
  returning: ['Returning', 'A show comes back from a break with new episodes soon.', 'TMDB episode dates'],
  premiere: ['Premiere', 'A show that first aired in the last two weeks.', 'TMDB'],
  just_added: ['Just added', 'A film that recently came out digitally.', 'TMDB / MDBList release dates'],
  season_finale: ['Season finale', 'A show’s latest season just ended.', 'TMDB episode dates'],
  cult: ['Cult classic', 'On the cult-classics list.', 'MDBList keywords'],
  foreign: ['Foreign language', 'Not originally in English.', 'TMDB original language'],
  newly_streaming: ['Newly streaming', 'Older recency signal (new on streaming).', 'TMDB / MDBList'],
  true_story: ['True story', 'Based on a true story.', 'MDBList keywords'],
  short_film: ['Short film', 'A short runtime.', 'TMDB runtime'],
  mini_series: ['Mini series', 'A limited series.', 'TMDB'],
  binge_ready: ['Binge ready', 'A finished show you can watch in one go.', 'TMDB'],
  trending_broad: ['Trending (lower ranks)', 'Ranks just below the Trending label (41–100).', 'TMDB trending'],
  cinema: ['In cinemas', 'A film only in cinemas so far (with the date it reaches home, when known).', 'TMDB release dates + MDBList'],
  streaming: ['Streaming', 'A film out digitally.', 'TMDB release dates + MDBList'],
  physical: ['Disc release', 'A film out on disc.', 'TMDB release dates'],
  production: ['In production', 'Not released anywhere yet.', 'TMDB status'],
  ended: ['Ended', 'A show that finished its run.', 'TMDB status'],
  cancelled: ['Cancelled', 'A show that was cancelled.', 'TMDB status'],
  airing: ['Airing', 'A show with episodes going out now.', 'TMDB episode dates'],
  renewed: ['Renewed', 'A show between seasons with the next one announced.', 'TMDB'],
};
export const sashName = s => (SASH_INFO[s] ? SASH_INFO[s][0] : s.replace(/_/g, ' ').replace(/^./, c => c.toUpperCase()));

function Labels({ s, onChange }) {
  const active = s.sash.active;
  const inactive = s.sash.all.filter(x => !active.includes(x));
  const move = (i, d) => { const a = [...active]; const j = i + d; if (j < 0 || j >= a.length) return; [a[i], a[j]] = [a[j], a[i]]; onChange(a); };
  const row = (slot, on, i) => {
    const [name, what, src] = SASH_INFO[slot] || [sashName(slot), '', ''];
    return html`<div class="list-row ${on ? '' : 'off'}">
      <label class="switch" style="padding:0;flex:1"><input type="checkbox" checked=${on}
          onChange=${() => onChange(on ? active.filter(x => x !== slot) : [...active, slot])} />
        <span><span class="t">${name}</span><span class="d" style="display:block">${what}${src ? html` <em>· ${src}</em>` : ''}</span></span></label>
      ${on && html`<button class="icon" onClick=${() => move(i, -1)} disabled=${i === 0} aria-label="Move up">↑</button>
        <button class="icon" onClick=${() => move(i, 1)} disabled=${i === active.length - 1} aria-label="Move down">↓</button>`}
    </div>`;
  };
  return html`<div class="list sash-list tall">${active.map((x, i) => row(x, true, i))}${inactive.map(x => row(x, false))}</div>`;
}

const SECTION_NAMES = { directors: 'Directors', studios: 'Studios', cast: 'Cast' };

function NotableLists() {
  const [data, setData] = useState(null);
  const [section, setSection] = useState('directors');
  const [edits, setEdits] = useState({});       // section -> entries being edited
  const [q, setQ] = useState('');
  const [results, setResults] = useState(null);
  useEffect(() => { api('/notch/lists').then(setData).catch(ex => toast(ex.message, true)); }, []);
  if (!data) return html`<div class="empty">Loading…</div>`;
  const sec = data.sections[section];
  const entries = edits[section] || sec.entries;
  const dirty = !!edits[section];
  const setEntries = list => setEdits({ ...edits, [section]: list });
  async function save(value, msg) {
    try {
      const d = await api('/notch/lists', { method: 'PUT', body: { changes: { [section]: value } } });
      setData({ ...data, sections: d.sections });
      const next = { ...edits }; delete next[section]; setEdits(next);
      toast(msg);
    } catch (ex) { toast(ex.message, true); }
  }
  async function search(e) {
    e.preventDefault();
    if (!q.trim()) return;
    try { setResults((await api(`/notch/search?section=${section}&q=${encodeURIComponent(q)}`)).results); }
    catch (ex) { toast(ex.message, true); }
  }
  const add = r => {
    if (entries.some(x => x.name === r.name)) { toast('Already on the list'); return; }
    setEntries([{ name: r.name, label: r.name }, ...entries]); setResults(null); setQ('');
  };
  return html`<div>
    <div class="seg small" style="margin-bottom:12px">${Object.entries(SECTION_NAMES).map(([k, l]) =>
      html`<button class=${section === k ? 'on' : ''} onClick=${() => { setSection(k); setResults(null); }}>${l} (${(edits[k] || data.sections[k].entries).length})</button>`)}</div>
    <p class="hint-sm">A title gets the <strong>Notable ${section === 'studios' ? 'studio' : section === 'cast' ? 'cast' : 'director'}</strong> label when TMDB credits someone on this list (names must match TMDB exactly: add them through the search). The label is what the notch prints.
      ${sec.custom ? ' This is your own list.' : ' This is PostersPlus’s built-in list.'}</p>
    ${data.tmdb && html`<form class="row" onSubmit=${search} style="margin-bottom:10px">
      <input type="search" placeholder=${`Search TMDB for ${section === 'studios' ? 'a studio' : 'a person'}`} value=${q} onInput=${e => setQ(e.target.value)} style="flex:1;max-width:420px" />
      <button>Search</button></form>`}
    ${results && html`<div class="list" style="margin-bottom:12px">${results.length ? results.slice(0, 8).map(r => html`<div class="list-row">
        ${r.thumb ? html`<img src=${r.thumb} alt="" class="mini-poster" referrerpolicy="no-referrer" />` : html`<span class="mini-poster"></span>`}
        <div class="grow"><div class="name">${r.name}</div><div class="meta">${[r.department, (r.known_for || []).join(', '), r.country].filter(Boolean).join(' · ')}</div></div>
        <button onClick=${() => add(r)}>Add</button></div>`) : html`<div class="empty">Nothing found.</div>`}</div>`}
    <div class="list sash-list tall">${entries.map((x, i) => html`<div class="list-row">
      <div class="grow"><div class="name">${x.name}</div></div>
      <input type="text" value=${x.label} maxlength=${data.max_label} aria-label=${`Label for ${x.name}`} style="max-width:220px"
        onInput=${e => setEntries(entries.map((y, j) => (j === i ? { ...y, label: e.target.value } : y)))} />
      <button class="icon" onClick=${() => setEntries(entries.filter((_, j) => j !== i))} aria-label=${`Remove ${x.name}`}>✕</button>
    </div>`)}</div>
    <div class="row" style="margin-top:12px">
      <button class="primary" onClick=${() => save(entries, 'List saved: posters update in the next run')} disabled=${!dirty}>Save ${SECTION_NAMES[section].toLowerCase()}</button>
      ${dirty && html`<button onClick=${() => { const n = { ...edits }; delete n[section]; setEdits(n); }}>Undo changes</button>`}
      ${sec.custom && !dirty && html`<button class="danger" onClick=${() => confirm('Go back to PostersPlus’s built-in list?') && save(null, 'Built-in list restored')}>Back to the built-in list</button>`}
    </div>
  </div>`;
}

export function Notch({ refreshStatus }) {
  const [s, setS] = useState(null);
  useEffect(() => { api('/style').then(setS).catch(ex => toast(ex.message, true)); }, []);
  if (!s) return html`<div class="empty">Loading…</div>`;
  async function setActive(active) {
    const params = { ...s.params, sash_priority: active.length ? active.join(',') : s.sash.all.map(x => '-' + x).join(',') };
    setS({ ...s, sash: { ...s.sash, active } });
    try { const d = await api('/style', { method: 'PUT', body: { params } }); setS(d); refreshStatus(); }
    catch (ex) { toast(ex.message, true); }
  }
  async function apply() {
    try { await api('/style/apply', { method: 'POST', body: { run: true } }); setS(await api('/style')); refreshStatus(); toast('Applied. Studio is updating your library (see Activity).'); }
    catch (ex) { toast(ex.message, true); }
  }
  return html`
    <div class="page-head">
      <div><h1>Notch</h1><p>The label at the top of a poster: the first label on this list that applies to the title. Each title can also switch labels off or set its own text in its editor.</p></div>
      ${s.has_draft && html`<div class="row"><button onClick=${() => go('style')}>See the previews</button><button class="primary" onClick=${apply}>Apply to library</button></div>`}
    </div>
    ${s.has_draft && html`<div class="notice warn"><p><strong>Not live yet.</strong> Label changes are part of the style draft: Jellyfin gets them when you apply it.</p></div>`}
    <div class="style-layout">
      <section class="card"><h2>Labels</h2><p class="sub">Switch labels on or off and move them up to give them priority.</p>
        <${Labels} s=${s} onChange=${setActive} /></section>
      <section class="card"><h2>Notable lists</h2><p class="sub">Who counts as a notable director, studio or cast member, and what the notch says for them.</p>
        <${NotableLists} /></section>
    </div>`;
}
