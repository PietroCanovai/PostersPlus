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
- `web/`: Preact + htm vendored (`vendor/preact-htm.js`), Inter bundled (`fonts/`), no build step, no external requests. Hash routes: `#activity`, `#run/<id>`, `#settings`.

## Tests

`python3 -m pytest tests/ -q` runs on Linux only (cairo, skia, fcntl). This PC has no Docker/WSL, so rely on the Tests workflow, or a throwaway container on the server. The Studio tests alone run on Windows too (`python -m pytest tests/test_studio_engine.py`, needs httpx + fastapi): they use a fake Jellyfin and renderer via `httpx.MockTransport`. JS: `node --check` on a `.mjs` copy of `app.js`.

## Upstream hooks

Every fork change to an upstream file:

- `.github/workflows/docker.yml`: image name, `studio` branch, amd64, version build args (fork rewrite).
- `.github/workflows/tests.yml`: runs on `studio`.
- `.github/workflows/pr-base-guard.yml`: deleted (upstream's release-branch guard).
- `dockerfile`: `GIT_COMMIT`/`BUILD_DATE` args → `STUDIO_*` env.
- `main.py`: after `app.include_router(_admin.router)`: `import studio as _studio; _studio.install(app)`. In `lifespan`: `_studio.start()` before `yield`, `await _studio.stop()` right after.

## Upstream merge

`git fetch upstream && git checkout studio && git merge upstream/dev`, resolve conflicts at the hooks above, CI green, push, `update.sh` on the server.
