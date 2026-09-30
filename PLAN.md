# PostersPlus Studio — plan

Status (2026-09-30): **Phases 0–5 done and live** (theatre needs the user's StageMedia key; season posters are behind a Settings switch). **Phase 6**: bulk actions, backups and the user guide (STUDIO.md) done; remote access through the Cloudflare tunnel waits for the user (see section 12). Execute phase by phase; each phase ends with something testable on the real server.

Deviations from the plan as written:
- Staging ran in the main container with uploads switched off (Studio's default) rather than a second `postersplus-dev` container: read-only toward Jellyfin, same effect, no duplicate cache.
- The explicit-`quality=` cache hook wasn't needed: Studio sends `quality=` only when the style draws quality, which the current style doesn't.
- Rotation, Never lists and colours (Phase 3) shipped together with Phase 2, as they share one rules model and one set of renderer hooks.
- Chosen notch colours render as picked: a Studio-only `notch_text_color` (honoured by the frosted notch through a `force_text` kwarg in `awards.py`) and "match" tinting when `tint_color` is set.
- Concerts are `MusicVideo` items in Jellyfin; `MAX_POSTER_RESOLUTION=1000` is set in the server's compose file.

Fork `PietroCanovai/PostersPlus` of `UmbraProjects/PostersPlus` (upstream, branch `dev`). Server `serverino-lazarus` (192.168.1.55, Ubuntu 24.04, x86_64, TZ Europe/Rome), stack in `/home/serverino/docker/postersplus` (`compose.yaml`, host port 8183 → container 8000, cache volume `./postersplus-cache:/app/cache`). This PC reaches the server's filesystem as `Z:` over Samba.

---

## 1. Goals

1. **Updates without going crazy**: one command on the server after any change.
2. **Reliable**: every night the library ends up exactly as the rules say, and problems are visible.
3. **A better UI**: readable, clear, simple; works on desktop and phone.
4. **Per-title control**: for every movie/show, choose posters and logos to **never** use, posters to put in a **daily rotation** (each with its own look), a pinned choice, and per-title **colours** and style. Everything else stays automatic.
5. **Whole library in one click**, always honouring those per-title rules.

## 2. Decisions (from the Q&A)

| Topic | Decision |
|---|---|
| Architecture | Everything inside the PostersPlus container: a new **Studio** page (`:8183/studio`). The host `jellyfin_sync.py` + venv + systemd timer are retired once Studio's scheduler is proven. |
| Updates | GitHub Actions builds `ghcr.io/pietrocanovai/postersplus` on every push; server runs `bash ~/docker/postersplus/update.sh`. |
| Server access | Claude gets a dedicated SSH key (user adds the public key once). Claude still asks before touching the live container, Jellyfin or the tunnel. |
| Upstream | Follow upstream; merge `upstream/dev` on request. Fork code isolated in new files, few small hooks. |
| Rotation | **Shuffle, no repeats per cycle**, one step per day. |
| Rotation entries | Each rotation poster is a **look**: poster + its own logo, colours, crop, logo placement. |
| Colours per title/look | Notch/sash tint, bottom fade colour, text colours, logo colour. |
| Untouched titles | One fixed automatic pick (as today), plus a global switch "auto-rotate untouched titles", default **off**. |
| Item types | Movies, TV shows, **Seasons** (later phase), **Theatre** via StageMedia. |
| Libraries | Movies, TV, Shorts, Concerts, Theatre. **Concerts that can't be matched (YouTube videos) are left alone**, silently. |
| Global style change | Marks titles **outdated**; nothing reaches Jellyfin until you press **Apply**. |
| Reverts | Detect a poster Jellyfin (or anyone) replaced, and **re-push** it; log it. |
| SpatialPosters | Only the UI/behaviour reference; it doesn't push to this server. |
| Theatre look | Posters+ style over StageMedia art (first); a **Playbill-style** template as an alternative per-show look (later). |
| UI language | English. |
| Access | Desktop, phone/tablet at home, and remotely via the **Cloudflare Tunnel** with **Cloudflare Access + Studio login**. |
| Resolution | **1000×1500** for Jellyfin. |
| Media mounts | Remove the Movies/TV mounts from `compose.yaml` (PostersPlus never reads them). |
| Alerts | Activity page only (red badge + clear log). |

## 3. How PostersPlus works today (facts this plan relies on)

- **Renderer**: `GET /poster?tmdb_id=…&type=…&<style params>` (main.py `get_poster`). It fetches TMDB metadata (tmdb.py `fetch_poster_metadata`: best textless poster by `_select_textless_poster`, backdrop fallback, `poster_pools` of top 5), applies Fanart/TVDB sources, then **operator art** (art_overrides.py), picks a logo (`logo_priority`), and composites overlays in `build_poster(image, score, genre, cfg, logo=…, discovery_meta=…)`. Every visual setting is a URL parameter parsed into `RequestConfig` (`build_request_config`); the composite cache key hashes the parsed config (`_render_config_signature`).
- **Colours**: every frosted element (notch, bar, poster-coloured sash) tints from **one** sample, `_frost_tint = dominant_frost_rgb(...)` in `_build_poster`; tinted vignettes sample the same art. `sash_text_color` / `rating_text_color` already exist as URL params. Dark logos are auto-lightened; there is no forced logo colour yet.
- **Artwork tab** (`/admin` → Artwork, art_overrides.py): one pinned image per slot per title (`textless`, `original` per language, `logo` per language, `landscape`, `landscape_original`), custom uploads / pasted links (ThePosterDB works), backdrop crop. SQLite table `art_overrides` in `cache.db`. Custom images live in `CUSTOM_ART_DIR` as `custom:<hash>.jpg|png`. Path validation (`provider_of`) allows only TMDB paths, fanart.tv/TVDB hosts and stored custom files (no SSRF).
- **Admin auth** (admin.py): `ADMIN_KEY` via `X-Admin-Key` header, lockout after 8 bad keys per client IP (uses `request.client`, i.e. needs `FORWARDED_ALLOW_IPS` behind a proxy).
- **Current Jellyfin sync**: host script `jellyfin_sync.py` (identical to the repo copy), systemd `postersplus-sync.timer` at 04:00, recipe = `POSTERSPLUS_URL` in `sync.env`. Current look (from the setup notes): `top_gradient=off, sash_mode=notch, fallback_to_imdb=true, rating_display_mode=0 (no rating), badge_display_mode=0 (no badges), logo_priority=native,english,original,neutral,text, fallback_bg_style=photoreal, logo_max_w_ratio=0.74, logo_max_h_ratio=0.24, logo_bottom_ratio=0.05, logo_bottom_anchor=true, sash_badge_size_w=1.40, sash_badge_size_h=1.20, sash_priority=default,-foreign,-true_story,-short_film,-physical,-streaming,-cinema,-production`.
- **Instance settings** (`postersplus-cache/settings.json`): TMDB, MDBList, Fanart, TVDB keys set. `.env`: ADMIN_KEY only. No ACCESS_KEY. `IMAGE_FORMAT` unset → **WebP**.
- **Theatre**: Jellyfin's Encora plugin stores `StageMediaShowId` in ProviderIds. StageMedia API: `GET https://stagemedia.me/api/images?show_id=<id>` with `Authorization: Bearer <key>` → `{posters: [...]}`. (SpatialPosters `web/src/lib/stagemedia.ts` is a working reference.)
- **Cloudflare Tunnel**: `cloudflared` runs as a host systemd service, locally managed config `/etc/cloudflared/config.yml` (13 ingress rules, none for PostersPlus yet).

## 4. Problems to fix

1. **The nightly run refreshes almost nothing.** `sync_item` skips a title when ids + quality + recipe are unchanged, so new sashes (New Season, awards, trending), Artwork-tab changes and any rotation never reach Jellyfin without `--force`.
2. **WebP uploaded as `image/jpeg`.** Content-Type is hardcoded; works today, fragile.
3. **Silent reverts.** A Jellyfin refresh can replace a pushed poster; the script can't tell.
4. **Sync renders always bypass the composite cache** (`quality=` is always sent), so a full run renders every title from scratch.
5. **Two things to update** (image + host script/venv/systemd). Setup notes say `docker-compose.yml`; the real file is `compose.yaml`.
6. The existing admin/configurator UIs are built for public multi-user Stremio instances: powerful but noisy for a single Jellyfin library.

---

## 5. Architecture

```
Jellyfin ──API──► Studio library index ──► per-title rules (looks, rotation deck, never, style)
                                                │
                                   resolver: today's look per item
                                                │
                  render (loopback /poster, or build_poster for theatre/seasons)
                                                │
                      sha256(image) ≠ last pushed, or Jellyfin tag changed?
                                                │ yes
                               upload to Jellyfin → record hash + Jellyfin image tag
```

### 5.1 Where the code lives

New files (fork-owned, upstream never touches them):
- `studio/` Python package: `__init__.py` (register router + background task), `db.py` (schema/migrations for `studio.db`), `jellyfin.py` (async REST client; quality-token logic ported from `jellyfin_sync.py`), `library.py` (scan, matching, policies), `candidates.py` (TMDB/Fanart/TVDB/StageMedia/custom candidates), `rules.py`, `resolver.py` (pure), `render.py`, `engine.py` (runs, scheduler, push/revert), `stagemedia.py`, `api.py` (FastAPI routes under `/studio/api/*`), `auth.py` (session login).
- `studio/web/` static UI: `index.html`, ES modules, CSS, vendored **Preact + htm** (no build step, no CDN), fonts bundled locally (Inter, OFL).
- `tests/test_studio_*.py`.
- `CLAUDE.md` (kept updated with every change), this `PLAN.md`.

Small, marked hooks in upstream files (each a few lines calling into `studio/`, listed in CLAUDE.md so merges are easy):
- `main.py`: include the Studio router and background task; pass renderer callables to `studio` at startup (same pattern as `admin.register`, avoids circular imports).
- `main.py` `get_poster` / `build_request_config` / `_build_poster`: the new render params in 5.4.
- `main.py`: make explicit-`quality=` renders cacheable (quality into the cache key instead of skipping the cache).
- `.github/workflows/*`: image name, branch, platform.

### 5.2 Data model — `/app/cache/studio.db` (separate SQLite, WAL)

Separate from upstream's `cache.db` so upstream schema changes and pruning never touch user rules, and backup is one file.

- **items** — one row per Jellyfin item: `jf_id` (PK), library id/name, Jellyfin type (Movie/Series/Season), name, year, parent series + season number (seasons), `tmdb_id`, `imdb_id`, `tvdb_id`, `stage_show_id`, `manual_tmdb_id` (your link for unmatched items), quality tokens, `jf_image_tag` (Primary tag seen at last scan), `pushed_hash`, `pushed_tag`, `pushed_look_id`, `pushed_at`, `status` (ok / outdated / error / reverted / needs_match / left_alone / hands_off), `last_error`, `present`, `reviewed_at`.
- **titles** — rules owner, keyed by `title_key`: `tmdb:movie:<id>`, `tmdb:tv:<id>`, `tmdb:tv:<id>:s<n>`, `stage:<show_id>`, or `jf:<id>` when there is no external id. Duplicate Jellyfin items (e.g. 4K + 1080p) share one title. Fields: mode (auto / pinned / rotation), `pinned_look_id`, `hands_off`, title-level `style_overrides` (JSON), `template` (theatre: posters_plus / playbill), rotation `deck` (JSON list of look ids), `deck_pos`, `current_look_id`, `rotated_on` (local date).
- **looks** — `look_id`, `title_key`, poster ref (TMDB path, fanart/TVDB URL, `custom:…`, StageMedia URL) + kind (poster / backdrop) + crop (x, y, zoom), `has_own_title` (serve as original art, no logo), logo ref (auto / none / text / path / custom), logo placement (size, bottom offset), colours (notch tint, fade colour, sash text, rating text, logo colour), look-level `style_overrides`, `in_rotation`.
- **never** — (`title_key`, kind poster/logo/backdrop, ref).
- **runs** + **run_items** — history for the Activity page (keep the last ~60 runs).
- **settings** — Jellyfin URL + API key (secret, never sent back to the page), libraries + policies, schedule (04:00, Europe/Rome), resolution (1000), StageMedia key (secret), `auto_rotate_untouched` (off), **draft** and **applied** global style (param strings) + versions.

### 5.3 Resolver — "today's look" (pure function, fully unit-tested)

```
if hands_off or library policy says leave alone      → skip
if pinned look (and its poster isn't in never)        → that look
elif rotation pool (looks in rotation, minus never):
     1 look                                           → it
     ≥2 looks → shuffle-no-repeat deck:
        deck advances once per local day, only by the nightly run (previews never advance it);
        when the deck is used up or the pool changed → reshuffle (seeded), and the new deck's
        first look ≠ the one just shown
elif auto_rotate_untouched                            → same deck over the top textless candidates minus never
else                                                  → automatic pick; if that poster/logo is in never,
                                                        the next-best candidate by PostersPlus's own ranking
params = applied global style ⊕ title overrides ⊕ look overrides ⊕ look colours ⊕ explicit art
```

The editor shows **today** and **the next few days** for rotating titles.

### 5.4 Rendering and renderer hooks

- Movies/shows/shorts/concerts: Studio calls its own `/poster` on loopback with the applied style + resolver params + `resolution=1000` + quality tokens (only fetched when the style uses quality; your current style shows none).
- New `/poster` params (all validated with the same allow-list as the Artwork tab; defaults leave cache keys unchanged via `_SIGNATURE_OMIT_AT_DEFAULT`):
  - `art_poster`, `art_kind` (poster/backdrop), `art_crop` (x,y,zoom), `art_original` (art already carries the title → no logo). Applied at the operator-art point in `get_poster`, beating every automatic pick.
  - `art_logo` (path / custom / `none` / `text`).
  - `tint=RRGGBB` → replaces the `_frost_tint` sample (notch, bar, poster-coloured sash, ribbon).
  - `fade_color=RRGGBB` → colour of the tinted bottom/top vignette instead of the sampled one.
  - `logo_color=RRGGBB` (+ mode: solid / tint) → recolour the logo before compositing (new).
  - Text colours: existing `sash_text_color`, `rating_text_color`.
- Instance settings: `IMAGE_FORMAT=jpeg`, `JPEG_QUALITY=92`, `MAX_POSTER_RESOLUTION=1000`. Studio still sends the response's real Content-Type to Jellyfin.
- Theatre and seasons (no TMDB poster pipeline): `studio/render.py` builds a `RequestConfig` from the applied style and calls `build_poster` directly with the chosen image (cropped 2:3), the chosen logo, and an optional custom notch label. Playbill template = a separate compositor in `studio/`, designed from the Playbills folder.

### 5.5 Sync engine

- Runs in the app's background leader (one worker), scheduled 04:00 Europe/Rome (`TZ` set in compose); if the server was down, it catches up on start (like `Persistent=true`).
- Each run: **scan** Jellyfin (items, ids, quality, current image tags) → **advance decks** due today (nightly run only) → for each managed item: resolve → render → hash → **upload if the hash differs from `pushed_hash` or Jellyfin's tag differs from `pushed_tag`** (a revert → re-push, logged) → re-read the item's `ImageTags.Primary` → record.
- Uses the **applied** style only; a draft style never leaks to Jellyfin.
- Render concurrency capped (2) so the server stays responsive; a failed item never stops the run; transient errors retry next run; a title reverting 3+ times in a week gets a "keeps reverting — check Jellyfin's *Replace existing images*" note.
- Manual triggers: Run now (changed only / everything / selection / one title), **Dry run** (render + compare, no upload: "47 would change").
- Upload: base64 body to `POST /Items/{id}/Images/Primary` (as today).

### 5.6 Library and matching

- Managed libraries: Movies, TV (+ seasons in Phase 5), Shorts, Concerts, Theatre; each can be switched off in Settings.
- Matching from Jellyfin ProviderIds. Unmatched items:
  - Movies / TV / Shorts → **Needs match**: search TMDB in Studio and link (stored in Studio only).
  - Concerts → left alone, silently (matched concerts are handled like movies).
  - Theatre → matched by `StageMediaShowId`; items without one are left alone.
- Existing Artwork-tab choices are imported as pinned looks.

### 5.7 Global style

- The current look (section 3) is imported as the first **applied** style.
- Editing creates a **draft**: plain-language controls for what your look uses (logo size/position, fades, notch style/size/colour, sash list and order, rating/badges on or off, fallback art), plus "raw parameters" and "import a configurator URL".
- Live previews on a sample of your own titles. Saving a draft marks the library **outdated**; **Apply** promotes it and runs the library with progress.

### 5.8 Security and access

- Studio login with ADMIN_KEY (same lockout logic), then a signed, HttpOnly session cookie (30 days) so the phone doesn't re-type the key; CSRF-safe (SameSite=Strict + custom header). Secrets are never sent back to the page.
- Jellyfin and StageMedia are called server-side only; the browser gets Jellyfin thumbnails through a Studio proxy, never the API key.
- Remote: one new tunnel hostname → `http://localhost:8183`, protected by **Cloudflare Access** (your email/Google). `FORWARDED_ALLOW_IPS` = the Docker gateway, so lockouts see real client IPs. Set an `ACCESS_KEY` for `/poster` as defence in depth (Studio adds it on loopback).
- Custom art stays allow-listed (no arbitrary URL fetches from params).

---

## 6. UI (Studio)

Dark, calm, large readable type, plain words, one main action per screen, instant feedback. Responsive: phone = single column, 3 tiles per row, editor stacks preview above controls, 44 px touch targets. The old admin and configurator stay reachable under **Advanced**.

1. **Library** (home): grid of every managed item showing **the poster Jellyfin has now**; chips (Pinned · Rotating ×N · Custom style · Hands off · Outdated · Error · Reverted · Needs match); filters (library, status, reviewed or not), search, sort; multi-select bulk actions (Push now, Hands off, Reset rules).
2. **Title editor**: big live preview of what Jellyfin will get (today / next days / "Jellyfin now" comparison).
   - **Posters**: every candidate (TMDB, Fanart, TVDB, StageMedia, uploads, pasted links, ThePosterDB, backdrops → crop to 2:3) with a three-state control: **Auto · In rotation · Never**, plus **Pin**.
   - **Logos**: same three states, upload PNG, "no logo", "text title".
   - **Look**: per rotation poster (or the pinned one): logo, colours (notch tint, fade, text, logo; auto or custom, eyedropper from the poster), crop, logo size/position.
   - **Style**: title-level overrides of the global style; Reset to global.
   - Actions: **Push now**, Revert to automatic, Hands off.
3. **Review mode**: step through the library one title at a time (arrow keys / swipe) with quick actions (Looks good ✓ · Edit · Hands off) and progress ("142 of 312 reviewed").
4. **Style**: global style editor with sample previews, outdated count, **Apply**.
5. **Activity**: next run, Run now / Dry run, live progress, history, per-item results, problems list (red badge in the nav).
6. **Settings**: Jellyfin (URL, API key, Test), libraries and policies, schedule, resolution, StageMedia key, auto-rotate switch, rules backup (export/import JSON; nightly automatic backup kept 14 days), version and "update available", links to Advanced.

## 7. Updating and deployment

- Git: branch **`studio`** (fork default branch) = `upstream/dev` + our work. `dev`/`main` mirror upstream.
- CI (fork): enable Actions; `docker.yml` builds on push to `studio`, image `ghcr.io/pietrocanovai/postersplus:latest` + `:sha-<short>`, **linux/amd64 only** (faster), commit + date baked in for Studio's version display; `tests.yml` runs pytest on every push; `pr-base-guard.yml` disabled in the fork. GHCR package made public (repo is AGPL-public anyway), so the server needs no registry login.
- Server `compose.yaml`: image → `ghcr.io/pietrocanovai/postersplus:latest`; remove the media mounts; add `TZ=Europe/Rome`; later `FORWARDED_ALLOW_IPS`.
- `~/docker/postersplus/update.sh`: pull → up -d → wait for `/health` → print running version → prune old images; remembers the previous image so `update.sh --rollback` restores it in one line.
- Studio shows "update available" by comparing its baked-in commit with the fork's branch head.

## 8. Testing and rollout

- Unit tests for everything pure: resolver (deck shuffle/no-repeat/reshuffle, pinned/never precedence, auto fallback), style merging, param validation (allow-list), quality tokens (ported), push/revert decisions.
- Renderer hook tests: new params render and cache separately; cache keys unchanged at defaults (existing cache-key tests stay green).
- Integration tests with a small **mock Jellyfin** (items, image tags, base64 upload check).
- The suite runs on Linux only (cairo, skia, fcntl): GitHub Actions on every push, plus a throwaway test container on the server over SSH for quick iteration. This PC has no Docker/WSL.
- UI checks: headless Chromium (Playwright) from this PC against the dev instance, screenshots at desktop and phone widths.
- **Staging**: a `postersplus-dev` container (port 8184, its own cache copy) running Studio in **dry-run** against the real Jellyfin (read-only), to compare planned posters with current ones before anything is switched.

## 9. Phases

**Phase 0 — Fork plumbing (no behaviour change)**
`studio` branch, CLAUDE.md; CI builds/pushes our image; SSH key; `update.sh` (+ rollback); compose: our image, TZ, media mounts removed; settings `IMAGE_FORMAT=jpeg`, `JPEG_QUALITY=92`. The old systemd sync keeps running.
*Done when*: the server runs our image, a trivial commit reaches the server with one command, and the old sync still works.

**Phase 1 — Sync engine inside the app**
`studio/` skeleton, `studio.db`, Jellyfin client + library scan (Movies, TV, Shorts, Concerts), quality tokens, loopback render with the imported style at 1000×1500, hash-based uploads, tag-based revert detection + re-push, runs + scheduler + catch-up, Run now / Dry run; cacheable `quality=` renders. Minimal Studio: login, Activity, Settings.
Rollout: dev container dry-run → compare → enable in the main container (the first run uploads everything once, at 1000×1500) → disable and remove the systemd timer + venv (old script kept as a file for a while).
*Done when*: a week of nightly runs with no unexpected uploads, reverts get re-pushed and logged, and Activity explains every result.

**Phase 2 — Library + Title editor (pins)**
Library grid (Jellyfin thumbnails via proxy), filters, Needs match + manual TMDB link, title editor with live preview, all candidates (+ uploads, links, ThePosterDB, backdrop crop), pin poster/logo, Push now, Revert, Hands off, Review mode; import Artwork-tab choices; hooks `art_poster`/`art_kind`/`art_crop`/`art_original`/`art_logo`.
*Done when*: any single title can be fixed in under a minute and shows in Jellyfin right away.

**Phase 3 — Rotation, Never, looks and colours**
Three-state chips for posters and logos; looks with their own logo/colours/crop/placement; shuffle-no-repeat decks advanced nightly; today/next preview; Never honoured by the automatic pick; hooks `tint`, `fade_color`, `logo_color` (+ existing text colours); "auto-rotate untouched titles" switch (off).
*Done when*: rotating titles change daily with no repeats within a cycle, never-listed images never appear, and each look renders with its own colours.

**Phase 4 — Global style editor + Apply**
Draft/applied styles, plain-language controls, raw params, configurator-URL import, sample previews, outdated count, Apply with progress.
*Done when*: editing the style never touches Jellyfin until Apply, and Apply updates the whole library with every rule intact.

**Phase 5 — Theatre and Seasons**
Theatre: StageMedia key + candidates, per-show uploaded logos, Posters+ style via `build_poster`, rotation/never as for films; then the Playbill template (design spike from the Playbills folder), selectable per show.
Seasons: season candidates (TMDB/Fanart/TVDB season art + the show's art), the show's logo + a "Season N" label, per-season rules that default to the show's.
*Done when*: theatre and season posters are managed exactly like movies.

**Phase 6 — Remote access + polish**
Session cookies, `FORWARDED_ALLOW_IPS`, tunnel hostname + Cloudflare Access (with your OK), `ACCESS_KEY`; bulk actions, keyboard shortcuts, phone polish, rules backup/export, README section, upstream-merge playbook. (Remote access can move earlier if you want it sooner.)

**Phase 7 — Your own images, and control over the notch** (requested 2026-09-30)

*7a. A library of your own images per title.* **Done 2026-09-30.** Before: an upload becomes the pinned poster and replaces the previous one; the "Yours" tab only lists images a look still uses, so earlier uploads vanish. Instead:
- A per-title uploads table (`uploads`: title_key, kind poster/backdrop/logo, `custom:` path, name, added_at). Every upload or pasted link is kept there whether or not it's in use, and can be deleted.
- Upload several at once (multi-file picker, drag and drop). Uploading only adds to the library; you then Pin, Rotate or use it like any other candidate.
- Uploads of every kind: posters (with or without their own title), **backdrops** (framed to 2:3 like TMDB's), **logos** (PNG with transparency). Each kind appears in its own tab next to the providers' images.
- Custom images stay out of the Artwork tab's clean-up of unused `custom:` files (the fork checks its own table too).

*7b. What the notch can say, from Studio.* **Done 2026-09-30** (Notch page, notable lists, per-title label switches, "could show"). The notch shows the first label in the priority list that applies to a title. The data behind each label:
- **Awards** (Oscar/Emmy/Globe wins and nominations, Metacritic must-see, cult, true story, age rating): MDBList, keyed by IMDb id.
- **Festival prizes**: PostersPlus's own festival data (`festivals.py`).
- **Notable studio / director / cast**: curated lists in `discovery.py`, overridable in `/app/cache/discovery_overrides.json` (the admin dashboard's *Sash lists* view and its API `/admin/api/sash-lists`, with TMDB search for people and companies).
- **Trending**: TMDB's daily trending list (or a custom MDBList/TMDB-shaped source).
- **New season, returning, premiere, finale, airing/ended/cancelled, cinema/streaming/disc**: TMDB dates and status, plus MDBList's digital release date.
- **Watchlist**: MDBList, SIMKL, Trakt or PMDB when `WATCHLIST_SOURCE` is set.

Planned in Studio:
- A **Notch** page: every label with an on/off switch and its order (moved from the Style page), a description of what triggers it and where the data comes from, and a live example of a title that gets it.
- The **curated lists** (directors, studios, cast) editable in Studio: search TMDB for a person or company, add or remove them, change the label they show (e.g. "Ghibli"). This drives the existing `discovery_overrides.json`.
- **Per title**: turn off labels for that title only (e.g. no "Trending" on this one), besides the custom notch text that already exists. Implemented as a per-title `sash_priority` override in the title's style.
- **Custom labels**: fixed text per title (done: *Notch text*), and a check of which labels a title qualifies for right now (via the renderer's `debug=1` metadata), shown in the editor as "Could show: Oscar Winner · Notable Director · Trending #12".

**Phase 8 — Editor rework, Jellyfin's other images, frames** (requested 2026-09-30)

*8a. Fixes.* No toast for rotate add/remove; clicking a rotation look (strip) reliably selects and previews it; a newly added look becomes the selected one; clicking any candidate previews it as it would look (logo, style), without saving; the crop dialog darkens only the image outside the frame, never its own buttons; posters can be framed too (not only backdrops); "Use for all looks" for a logo; find out why some previews are slow and fix it.

*8b. Style at every level, one control set.* Library (Style page) → title (all its posters) → one poster (look). The same grouped controls everywhere (logo size and position, fades, tint, notch, colours), each showing where its value comes from, with reset. In the editor a scope switch: *This poster / Whole title*.

*8c. Editor and mobile pass.* One cohesive layout: preview on the left (sticky; on mobile on top), image slot tabs, compact mode switch, looks strip, sub-tabs Art / Logo / Style. Less text, no duplicate buttons, large touch targets, works at 360 px.

*8d. Jellyfin's other images.* Besides the Primary poster: **Backdrop**, **Logo** and **Thumb**, per title (Pin / Automatic / Leave Jellyfin's), plus library rules in Settings for the automatic pick: backdrops within a size you set (e.g. at least 1920×1080, 16:9, textless first), logo by the style's language order, thumb as PostersPlus's landscape render or a titled backdrop. Uploaded only when changed, reverts put back, like posters.

*8e. Frames.* Screen grabs as a source: Jellyfin's own chapter images (frames from your files, when Jellyfin has extracted them), TMDB episode stills for shows, and the providers' backgrounds. Usable as backdrops, thumbs, or framed into posters.

## 10. Upstream merge playbook

`git fetch upstream` → `git checkout studio` → `git merge upstream/dev` → resolve conflicts (expected only at the hook points listed in CLAUDE.md) → CI green → push → `update.sh` on the server. Never rewrite upstream code we don't need to; keep hooks tiny and calling into `studio/`.

## 11. Risks and open items

- `main.py` (~550 KB) changes often upstream; hooks there may conflict. Mitigation: tiny hooks, tests around them.
- Jellyfin behaviour to verify on the real server in Phase 1: image tags after upload, whether scans with *Replace existing images* overwrite uploads, library settings.
- Season art on TMDB is mostly text-bearing; the show's textless art + a label is the likely default.
- Forced logo colour on multi-colour logos: solid vs tint needs a visual check.
- Seasons multiply the item count (hundreds of renders); hash compare + composite cache keep nightly runs cheap after the first.
- StageMedia API limits and key; Encora id coverage.
- Cloudflare Access is configured in your Cloudflare dashboard (steps provided when we get there).

## 12. Needed from you, when we start

- Phase 0: add Claude's SSH public key to the server; OK to enable Actions + make the GHCR package public on the fork; OK to edit `compose.yaml` and switch the container image.
- Phase 1: paste the Jellyfin API key into Studio's Settings (the one in `sync.env` or a new one).
- Phase 5: StageMedia API key in Studio's Settings.
- Phase 6: Cloudflare Access setup in your Cloudflare dashboard; OK for the tunnel hostname.
