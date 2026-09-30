"""The sync engine: scan Jellyfin, render every managed title, upload what changed.

A poster is uploaded only when the rendered image differs from the one we last
uploaded (sha256 of the bytes), or when Jellyfin's image tag shows someone
replaced ours (a revert, which is put back).  So new sashes, art changes and
rotations reach Jellyfin by themselves, and unchanged titles cost a cache hit.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlencode

import httpx

from . import db, prefs, rules
from .jellyfin import Client, Item, JellyfinError, Library, quality_tokens

logger = logging.getLogger("studio")

LOOPBACK = "http://127.0.0.1:8000"
RENDER_CONCURRENCY = 2
# A title Jellyfin keeps replacing this many times gets a note to check its settings.
REVERT_WARN_AT = 3

# Item statuses shown in the UI.
OK, NEW, NEEDS_MATCH, LEFT_ALONE, ERROR, REVERTED = "ok", "new", "needs_match", "left_alone", "error", "reverted"


@dataclass
class Progress:
    running: bool = False
    run_id: int | None = None
    dry_run: bool = False
    phase: str = ""
    total: int = 0
    done: int = 0
    current: str = ""
    counts: dict = field(default_factory=dict)
    cancel: bool = False

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "cancel"}


progress = Progress()
_run_lock = asyncio.Lock()


def jellyfin_client(**kw) -> Client:
    return Client(prefs.get("jellyfin_url"), prefs.get("jellyfin_api_key"), **kw)


_shared: tuple[tuple[str, str], Client] | None = None


def shared_client() -> Client:
    """One long-lived client for the many small calls the UI makes (thumbnails),
    replaced when the connection settings change.  Never closed by callers."""
    global _shared
    conf = (prefs.get("jellyfin_url"), prefs.get("jellyfin_api_key"))
    if _shared is None or _shared[0] != conf:
        _shared = (conf, Client(*conf))
    return _shared[1]


# ── Scan ────────────────────────────────────────────────────────────────────

def is_stage(row: dict) -> bool:
    """A theatre recording Studio draws from StageMedia (no TMDB/IMDb id)."""
    return bool(row.get("stage_show_id")) and not (row.get("tmdb_id") or row.get("manual_tmdb_id") or row.get("imdb_id"))


def _item_status(row: dict, policy: dict) -> str:
    from . import stage
    if is_stage(row) and stage.enabled():
        matched = True
    else:
        matched = bool(row.get("tmdb_id") or row.get("manual_tmdb_id") or row.get("imdb_id"))
    if not matched:
        return LEFT_ALONE if policy["unmatched"] == "leave" else NEEDS_MATCH
    if row.get("status") in (NEEDS_MATCH, LEFT_ALONE):
        return NEW if not row.get("pushed_hash") else OK
    return row.get("status") or NEW


async def scan(client: Client) -> dict:
    """Refresh the library index from Jellyfin.  Returns counts."""
    now = time.time()
    libraries = await client.libraries()
    seen: set[str] = set()
    counts = {"libraries": 0, "items": 0}
    for lib in libraries:
        policy = prefs.library_policy(lib.id, lib.name)
        if not policy["enabled"]:
            continue
        counts["libraries"] += 1
        for item in await client.library_items(lib):
            if not item.id or item.type not in ("Movie", "Series", "Video", "MusicVideo"):
                continue
            seen.add(item.id)
            counts["items"] += 1
            _upsert(item, lib, policy, now)
    # Items that left Jellyfin (or whose library was switched off) stay in the
    # table, marked absent, so their history survives a library re-scan.
    present = db.query("SELECT jf_id FROM items WHERE present = 1")
    for row in present:
        if row["jf_id"] not in seen:
            db.execute("UPDATE items SET present = 0 WHERE jf_id = ?", (row["jf_id"],))
    db.set_setting("last_scan_at", now)
    return counts


def _upsert(item: Item, lib: Library, policy: dict, now: float) -> None:
    old = db.query_one("SELECT * FROM items WHERE jf_id = ?", (item.id,))
    row = dict(old or {})
    row.update({
        "jf_id": item.id, "library_id": lib.id, "library_name": lib.name, "jf_type": item.type,
        "name": item.name, "year": item.year, "tmdb_id": item.tmdb_id, "imdb_id": item.imdb_id,
        "tvdb_id": item.tvdb_id, "stage_show_id": item.stage_show_id, "jf_image_tag": item.image_tag,
    })
    if item.type != "Series":
        row["quality"] = ",".join(quality_tokens(item.raw))
    row["status"] = _item_status(row, policy)
    if old is None:
        db.execute(
            "INSERT INTO items (jf_id, library_id, library_name, jf_type, name, year, tmdb_id, imdb_id, "
            "tvdb_id, stage_show_id, quality, jf_image_tag, status, added_at, seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (item.id, lib.id, lib.name, item.type, item.name, item.year, item.tmdb_id, item.imdb_id,
             item.tvdb_id, item.stage_show_id, row.get("quality") or "", item.image_tag, row["status"], now, now),
        )
    else:
        db.execute(
            "UPDATE items SET library_id=?, library_name=?, jf_type=?, name=?, year=?, tmdb_id=?, imdb_id=?, "
            "tvdb_id=?, stage_show_id=?, quality=?, jf_image_tag=?, status=?, present=1, seen_at=? WHERE jf_id=?",
            (lib.id, lib.name, item.type, item.name, item.year, item.tmdb_id, item.imdb_id, item.tvdb_id,
             item.stage_show_id, row.get("quality") or "", item.image_tag, row["status"], now, item.id),
        )


# ── Render ──────────────────────────────────────────────────────────────────

def _style_uses_quality(style: str) -> bool:
    """Whether the style draws quality badges, using the renderer's own rule."""
    try:
        import main  # loaded by uvicorn as "main"; imported lazily to avoid a cycle
        return bool(main._uses_quality(main.build_request_config(dict(parse_qsl(style)))))
    except Exception:
        return True


def poster_url(row: dict, style: str, *, resolution: int, with_quality: bool, access_key: str = "",
               extra: dict | None = None) -> str:
    """*extra* is the title's own parameters (rules.resolve); they win over the style."""
    params = dict(parse_qsl(style, keep_blank_values=True))
    params.update(extra or {})
    tmdb_id = row.get("manual_tmdb_id") or row.get("tmdb_id")
    if tmdb_id:
        params["tmdb_id"] = tmdb_id
    if row.get("imdb_id"):
        params["imdb_id"] = row["imdb_id"]
    params["type"] = "tv" if row.get("jf_type") == "Series" else "movie"
    params["primary_client"] = "jellyfin"
    if resolution != 500:
        params["resolution"] = str(resolution)
    if with_quality:
        # "NONE" is not a known token: it tells the renderer "checked, nothing",
        # so it never falls back to guessing quality from stream addons.
        params["quality"] = row.get("quality") or "NONE"
    if access_key:
        params["access_key"] = access_key
    return f"{LOOPBACK}/poster?{urlencode(params)}"


async def render(http: httpx.AsyncClient, url: str) -> tuple[bytes, str]:
    resp = await http.get(url)
    if resp.status_code != 200:
        detail = ""
        try:
            detail = resp.json().get("detail", "")
        except ValueError:
            pass
        raise RuntimeError(f"Render failed: HTTP {resp.status_code} {detail}".strip())
    ctype = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
    if ctype not in ("image/jpeg", "image/png", "image/webp"):
        raise RuntimeError(f"Render returned {ctype}")
    return resp.content, ctype


# ── Run ─────────────────────────────────────────────────────────────────────

@dataclass
class Decision:
    action: str   # "unchanged" | "upload"
    reason: str   # "new" | "changed" | "reverted" | "forced" | ""


def decide(row: dict, image_hash: str, *, force: bool = False) -> Decision:
    """Upload when the image differs from what we last sent, or Jellyfin's tag
    says our image was replaced since."""
    if force:
        return Decision("upload", "forced")
    if not row.get("pushed_hash"):
        return Decision("upload", "new")
    if row.get("pushed_tag") and row.get("jf_image_tag") and row["jf_image_tag"] != row["pushed_tag"]:
        return Decision("upload", "reverted")
    if image_hash != row["pushed_hash"]:
        return Decision("upload", "changed")
    return Decision("unchanged", "")


def _selected(item_ids: list[str] | None) -> list[dict]:
    rows = db.query("SELECT * FROM items WHERE present = 1 ORDER BY library_name, name COLLATE NOCASE")
    if item_ids is not None:
        wanted = set(item_ids)
        rows = [r for r in rows if r["jf_id"] in wanted]
    return rows


async def run(*, trigger: str, dry_run: bool, item_ids: list[str] | None = None,
              force: bool = False, jf_transport=None, render_transport=None) -> int:
    """One sync run.  Returns the run id.  Raises RuntimeError when one is already running."""
    if _run_lock.locked():
        raise RuntimeError("A run is already in progress")
    async with _run_lock:
        if not dry_run and not prefs.get("uploads_enabled"):
            dry_run = True   # uploads off: a run is always a preview
        run_id = db.start_run(trigger, "selection" if item_ids is not None else "library", dry_run)
        progress.__init__(running=True, run_id=run_id, dry_run=dry_run, phase="Reading Jellyfin")
        counts: dict[str, int] = {}
        status, message = "done", None
        try:
            async with jellyfin_client(transport=jf_transport) as jf:
                if item_ids is None:
                    await scan(jf)
                rows = _selected(item_ids)
                style = prefs.get("style_applied")
                resolution = int(prefs.get("resolution") or 1000)
                with_quality = _style_uses_quality(style)
                try:
                    import config as _cfg
                    access_key = _cfg.ACCESS_KEY or ""
                except Exception:
                    access_key = ""
                progress.phase, progress.total = "Rendering", len(rows)
                sem = asyncio.Semaphore(RENDER_CONCURRENCY)
                async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=5.0),
                                             transport=render_transport) as http:

                    async def one(row: dict) -> None:
                        async with sem:
                            if progress.cancel:
                                return
                            progress.current = row["name"]
                            action = await _process(row, jf, http, style, resolution, with_quality,
                                                    access_key, dry_run, force, run_id,
                                                    advance=trigger == "schedule")
                            counts[action] = counts.get(action, 0) + 1
                            progress.done += 1
                            progress.counts = dict(counts)

                    await asyncio.gather(*(one(r) for r in rows))
            if progress.cancel:
                status, message = "cancelled", "Stopped by you"
        except JellyfinError as exc:
            status, message = "failed", str(exc)
        except Exception as exc:
            logger.exception("Studio run failed")
            status, message = "failed", f"{type(exc).__name__}: {exc}"
        finally:
            db.finish_run(run_id, status, counts, message)
            progress.running, progress.current, progress.phase = False, "", ""
        logger.info(f"Studio run {run_id} ({trigger}{', dry run' if dry_run else ''}): {status} {counts}")
        return run_id


async def _process(row, jf, http, style, resolution, with_quality, access_key, dry_run, force, run_id,
                   advance: bool = False) -> str:
    name, jf_id = row["name"], row["jf_id"]
    if row["status"] in (NEEDS_MATCH, LEFT_ALONE):
        db.log_run_item(run_id, jf_id, name, "skipped",
                        "No TMDB match" if row["status"] == NEEDS_MATCH else "Left alone (no match)")
        return "skipped"
    try:
        res = rules.resolve(rules.title_key(row), advance=advance)
        if res.skip:
            db.log_run_item(run_id, jf_id, name, "skipped", res.reason)
            return "skipped"
        if with_quality and row["jf_type"] == "Series" and not row.get("quality"):
            ep = await jf.representative_episode(jf_id)
            row["quality"] = ",".join(quality_tokens(ep)) if ep else ""
        if is_stage(row):
            from . import stage
            image, ctype = await stage.render(row, style, res.params, resolution)
        else:
            image, ctype = await render(http, poster_url(row, style, resolution=resolution, with_quality=with_quality,
                                                         access_key=access_key, extra=res.params))
        image_hash = hashlib.sha256(image).hexdigest()
        decision = decide(row, image_hash, force=force)
        if decision.action == "unchanged":
            if row["status"] != OK:
                db.execute("UPDATE items SET status = ?, last_error = NULL WHERE jf_id = ?", (OK, jf_id))
            return "unchanged"
        if dry_run:
            db.log_run_item(run_id, jf_id, name, "would_upload", decision.reason)
            return "would_upload"
        await jf.upload_primary(jf_id, image, ctype)
        fresh = await jf.item(jf_id)
        reverts = (row.get("revert_count") or 0) + (1 if decision.reason == "reverted" else 0)
        db.execute(
            "UPDATE items SET pushed_hash=?, pushed_tag=?, jf_image_tag=?, pushed_at=?, status=?, "
            "last_error=NULL, revert_count=? WHERE jf_id=?",
            (image_hash, fresh.image_tag, fresh.image_tag, time.time(), OK, reverts, jf_id),
        )
        detail = decision.reason
        if decision.reason == "reverted" and reverts >= REVERT_WARN_AT:
            detail = (f"reverted ({reverts} times so far) — Jellyfin keeps replacing it; check the "
                      "library's 'Replace existing images' setting")
        db.log_run_item(run_id, jf_id, name, "reverted" if decision.reason == "reverted" else "uploaded", detail)
        return "reverted" if decision.reason == "reverted" else "uploaded"
    except Exception as exc:
        msg = str(exc) or type(exc).__name__
        db.execute("UPDATE items SET status = ?, last_error = ? WHERE jf_id = ?", (ERROR, msg[:500], jf_id))
        db.log_run_item(run_id, jf_id, name, "error", msg[:500])
        return "error"


# ── Schedule ────────────────────────────────────────────────────────────────

def next_run_at(now: datetime | None = None) -> datetime | None:
    if not prefs.get("schedule_enabled"):
        return None
    now = now or datetime.now()
    hh, mm = (int(x) for x in prefs.get("schedule_time").split(":"))
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target > now:
        return target
    if db.get_setting("last_scheduled_date") == now.date().isoformat():
        return target + timedelta(days=1)
    return now   # today's was missed (server down): it runs now


def due(now: datetime) -> bool:
    """Today's scheduled run is due: the time has passed and it hasn't run today."""
    if not prefs.get("schedule_enabled"):
        return False
    hh, mm = (int(x) for x in prefs.get("schedule_time").split(":"))
    return (now.hour, now.minute) >= (hh, mm) and db.get_setting("last_scheduled_date") != now.date().isoformat()


def mark_schedule_enabled(now: datetime | None = None) -> None:
    """Turning the schedule on after today's time shouldn't start a run at once."""
    now = now or datetime.now()
    hh, mm = (int(x) for x in prefs.get("schedule_time").split(":"))
    if (now.hour, now.minute) >= (hh, mm):
        db.set_setting("last_scheduled_date", now.date().isoformat())


async def scheduler_loop() -> None:
    # Give the server a moment to come up (the renders go through it).
    await asyncio.sleep(90)
    while True:
        try:
            now = datetime.now()
            if due(now) and not _run_lock.locked():
                db.set_setting("last_scheduled_date", now.date().isoformat())
                await run(trigger="schedule", dry_run=False)
        except Exception:
            logger.exception("Studio scheduler")
        await asyncio.sleep(30)
