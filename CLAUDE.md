# CLAUDE.md

Fork of [UmbraProjects/PostersPlus](https://github.com/UmbraProjects/PostersPlus), turned into **PostersPlus Studio**: a Jellyfin-first poster manager (library browser, per-title rules, nightly sync) inside the PostersPlus container. The full roadmap and every agreed decision is in [PLAN.md](PLAN.md).

- Fork `PietroCanovai/PostersPlus` (`origin`), upstream remote `upstream`. Work branch **`studio`** (fork default). `dev`/`main` only mirror upstream.
- **Keep this file updated** whenever architecture, deploy steps, rules or gotchas change. It is part of every change, not a follow-up.

## Rules

- Fork code goes in **new files** (`studio/`, `deploy/`, `tests/test_studio_*.py`). Upstream files get only small, marked hooks, listed under *Upstream hooks* below, so `git merge upstream/dev` stays easy.
- Never read or print secret values on the server: `.env`, `sync.env*`, `postersplus-cache/settings.json`. Key names only.
- Ask before anything that touches the live container, Jellyfin, or the Cloudflare tunnel.
- `.sh` files are LF (`.gitattributes`). Check new scripts for `\r`.

## Server

`serverino-lazarus`, 192.168.1.55, Ubuntu 24.04 x86_64, TZ Europe/Rome. Stack: `/home/serverino/docker/postersplus` (`compose.yaml`, `.env`, `postersplus-cache/`, `update.sh`). Port 8183 → 8000. This PC maps the server root as `Z:` (Samba). Jellyfin at :8096.

## Deploy

- Push to `studio` → GitHub Actions builds `ghcr.io/pietrocanovai/postersplus:latest` + `:sha-<short>` (amd64 only). `.md` / `deploy/**` changes don't rebuild.
- Server: `bash ~/docker/postersplus/update.sh` (pull, restart, wait for healthy, print version, prune). `--rollback` restores the image that ran before the last update (kept as `:previous`).
- `deploy/compose.yaml` and `deploy/update.sh` are the source of truth for the server copies; copy changes over (Samba or scp) and keep them in step.
- The image carries `STUDIO_GIT_COMMIT` / `STUDIO_BUILD_DATE` (dockerfile build args).

## Studio architecture (`studio/`)

- `__init__.py`: `install(app)` (router + `/studio/static`), `start()`/`stop()` from main's lifespan. The scheduler runs in the worker holding `/app/cache/.studio.lock` (flock). In-memory run progress is per worker, so keep `WORKERS=1`.
- `db.py`: `/app/cache/studio.db` (WAL, separate from upstream's `cache.db`): `settings`, `items` (Jellyfin index + `pushed_hash`/`pushed_tag`), `runs`, `run_items` (last 60 runs).
- `prefs.py`: live settings with safe defaults: **uploads off** (every run is a preview) and **schedule off** until switched on in Settings. `DEFAULT_STYLE` = the look imported from the old `sync.env`. Library defaults by name: Movies/TV/Shorts on (unmatched → Needs match), Concerts on (unmatched → left alone), others off.
- `jellyfin.py`: async REST client, quality tokens ported from `jellyfin_sync.py`.
- `engine.py`: scan → render each managed item through `/poster` on loopback (`127.0.0.1:8000`) → upload only if sha256 ≠ `pushed_hash`, or Jellyfin's `ImageTags.Primary` ≠ `pushed_tag` (revert → put back). `quality=` is only sent when the style draws quality (`main._uses_quality`), so renders stay cacheable. Scheduler: 04:00 local (`TZ`), catches up after downtime; enabling it after today's time waits for tomorrow.
- `auth.py`: ADMIN_KEY via `admin._authorise` (shared lockout) → signed HttpOnly SameSite=Strict cookie (30 days, path `/studio`); non-GET calls need `X-Studio: 1`.
- `api.py`: `/studio` page, `/studio/api/*` (login, session, status, runs, items, run/cancel, settings, jellyfin/test, libraries, scan, thumb proxy, version + GitHub update check).
- `rules.py`: per-title rules. A *title* (`title_key`: `tmdb:movie:<id>`, `tmdb:tv:<id>`, or `jf:<id>` when unmatched) is shared by every Jellyfin copy of it. Modes `auto` / `pinned` (one look) / `rotation` (looks with `in_rotation`). A *look* = poster (+crop, +`own_title`) + logo (`''` auto, `text`, or a path) + colours + style. `never` lists (poster/logo). `resolve()` → render params: title style < look params; Never → `art_exclude`/`art_logo_exclude`. Rotation = shuffled deck, no repeats per cycle, advanced only by the nightly run (`advance=True`), reshuffled when the pool changes, never opening on the look just shown.
- `hooks.py`: the renderer side (imported by main.py): parses/validates the new `/poster` params with the Artwork tab's allow-list (`art_overrides.provider_of`) and implements them. Params: `art_poster`, `art_crop`, `art_original`, `art_logo`, `art_exclude`, `art_logo_exclude`, `tint_color`, `fade_color`, `logo_color` (+`logo_color_mode`), `notch_text_color`, `notch_label` (replaces the automatic notch text; hook right after `sash_result` in `_build_poster`). Invalid values are ignored, like the rest of the URL parser.
- `candidates.py`: TMDB/Fanart/TVDB posters, backdrops, logos for the editor (main's Artwork-tab helpers), cached 30 min.
- `stage.py`: theatre (Encora plugin → `StageMediaShowId`, no TMDB/IMDb). `engine.is_stage(row)`; `title_key` `stage:<show_id>` (all recordings of a show share rules); managed only once `prefs.stagemedia_key` is set (else left alone). Posters from `https://stagemedia.me/api/images?show_id=` (Bearer key, list cached 24 h, images cached in `/app/cache/studio_stage/`); the key only ever goes to stagemedia.me, other hosts go through `art_overrides.download_custom_url` (public-address check). Rendered in-process: image framed 2:3 with `art_overrides.Crop`, then `main.build_poster(..., logo=, fallback_title=<show name>)` + `main._encode_poster`, under `main._get_render_semaphore()`. Thumbnails via `/studio/api/stage-thumb` (only images of a known show list or a saved look).
- `seasons.py`: season posters (Settings switch `seasons_enabled`, off by default). The scan adds `Season` rows (`parent_jf_id`, `season_number`, the show's `tmdb_id`); `title_key` `tmdb:tv:<id>:s<n>`. `params_for(row, own)`: the show's title style < defaults (`notch_label` "Season N"/"Specials"; TMDB's best season poster, textless first, else a titled one as `art_original`, skipping Never) < the season's own rules. Rendered through `/poster` as type tv. The Library grid hides seasons; a show's editor lists them.
- `api_style.py`: global style. `prefs.style_applied` is what Jellyfin gets; `style_draft` (settings key) is edited and previewed (`/preview/<id>?style=draft`) until **Apply** (`/style/apply {run}` promotes it, keeps `style_previous` for `/style/undo`, optionally starts a run with trigger `apply`). `/style/import` takes a configurator URL. Sample titles for previews are kept in `style_samples`. The page's control defaults come from `main.build_request_config({})`.
- `api_library.py`: `/studio/api/library`, `title/<jf_id>` (+`/candidates`, `/looks`, `/never`, `/image`, `/image-link`, `/push`, `/reset`), `looks/<id>`, `preview/<jf_id>` (loopback render: today's look, a saved `look_id`, or an unsaved `look` JSON), `tmdb/search`, `items/<jf_id>/match`.
- `web/`: Preact + htm vendored (`vendor/preact-htm.js`), Inter bundled (`fonts/`), no build step. Candidate thumbnails load from the providers' CDNs (TMDB, fanart.tv, TVDB); everything else is local. Modules: `common.js` (api, toast, nav list), `library.js`, `editor.js`, `style.js`, `app.js` (shell, Activity, Settings). Hash routes: `#library` (home), `#title/<jf_id>[?review]`, `#style`, `#activity`, `#run/<id>`, `#settings`. CSS: `studio.css` (shell, forms) + `library.css` (grid, editor, style page, dialogs). Static files are served `Cache-Control: no-cache`.

## Tests

`python3 -m pytest tests/ -q` runs on Linux only (cairo, skia, fcntl). This PC has no Docker/WSL, so rely on the Tests workflow, or a throwaway container on the server. The Studio tests alone run on Windows too (`python -m pytest tests/test_studio_engine.py`, needs httpx + fastapi): they use a fake Jellyfin and renderer via `httpx.MockTransport`. JS: `node --check` on a `.mjs` copy of `app.js`.

## Upstream hooks

Every fork change to an upstream file:

- `.github/workflows/docker.yml`: image name, `studio` branch, amd64, version build args (fork rewrite).
- `.github/workflows/tests.yml`: runs on `studio`.
- `.github/workflows/pr-base-guard.yml`: deleted (upstream's release-branch guard).
- `dockerfile`: `GIT_COMMIT`/`BUILD_DATE` args → `STUDIO_*` env.
- `main.py`: after `app.include_router(_admin.router)`: `import studio as _studio; _studio.install(app)`. In `lifespan`: `_studio.start()` before `yield`, `await _studio.stop()` right after.
- `main.py` render hooks (all marked `fork hook`): `import studio.hooks as _studio_hooks` (next to `import art_overrides`); `RequestConfig` fork fields (art_*, tint/fade/logo colours) + their defaults in `_SIGNATURE_OMIT_AT_DEFAULT`; `_studio_hooks.apply_params` at the end of `build_request_config`; `skip_excluded` before `_use_backdrop` in `get_poster`; `poster_override` at the operator-art block; the logo override/exclude at the top of `_resolve_logo`; `tint_color` after the `_frost_tint` sample in `_build_poster` (and `_frost_ref = "match"` so a chosen colour isn't pastelled); `notch_text_color` at the notch/sash calls (`force_text=`, a new `awards.draw_award_badge` kwarg that makes the frosted style honour `text_color`); `fade_color` at the top/bottom gradient branches; `recolor_logo` at the top of `_build_poster`.

## Upstream merge

`git fetch upstream && git checkout studio && git merge upstream/dev`, resolve conflicts at the hooks above, CI green, push, `update.sh` on the server.
