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

## Tests

`python3 -m pytest tests/ -q` runs on Linux only (cairo, skia, fcntl). This PC has no Docker/WSL, so rely on the Tests workflow, or a throwaway container on the server.

## Upstream hooks

Every fork change to an upstream file:

- `.github/workflows/docker.yml`: image name, `studio` branch, amd64, version build args (fork rewrite).
- `.github/workflows/tests.yml`: runs on `studio`.
- `.github/workflows/pr-base-guard.yml`: deleted (upstream's release-branch guard).
- `dockerfile`: `GIT_COMMIT`/`BUILD_DATE` args → `STUDIO_*` env.

## Upstream merge

`git fetch upstream && git checkout studio && git merge upstream/dev`, resolve conflicts at the hooks above, CI green, push, `update.sh` on the server.
