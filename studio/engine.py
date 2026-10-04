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

from . import db, identity, prefs, rules
from .jellyfin import Client, Item, JellyfinError, Library, quality_tokens

logger = logging.getLogger("studio")

LOOPBACK = "http://127.0.0.1:8000"
RENDER_CONCURRENCY = 2
# How long a run waits for a Jellyfin that stopped answering (a restart after a
# plugin or image update takes seconds) before giving the run up.
JELLYFIN_WAIT = 300.0
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
    """A theatre title Studio draws from StageMedia: it has the Encora plugin's
    StageMediaShowId.  That wins over a TMDB id Jellyfin guessed (a show named
    like a film gets the film's id); only a match you set yourself beats it."""
    return identity.kind(row) == identity.STAGE


def in_process(row: dict) -> bool:
    """Drawn by Studio itself (studio.stage) rather than through /poster:
    theatre, and titles no database knows, made from an image of yours."""
    return identity.kind(row) != identity.POSTER


def _item_status(row: dict, policy: dict) -> str:
    # Identified, or deliberately not ("my own images"), or already given an image to use.
    matched = (identity.kind(row) != identity.OWN or identity.source(row) == "none"
               or rules.has_own_art(rules.title_key(row)))
    if row.get("jf_type") == "Season":
        matched = bool(identity.tmdb_id(row))   # season art comes from TMDB
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
            if item.type == "Series" and item.stage_show_id:
                # Theatre shows' "seasons" are productions (Broadway, West End): kept
                # for the Playbill venue, never turned into season posters.
                try:
                    names = list(dict.fromkeys(s["name"] for s in await client.seasons(item.id) if s["name"]))
                    from . import playbill
                    venue = playbill.pick_venue(await client.recordings(item.id))
                    db.execute("UPDATE items SET productions = ?, venue = COALESCE(NULLIF(?, ''), venue) WHERE jf_id = ?",
                               ("|".join(names), venue, item.id))
                except JellyfinError:
                    pass
            elif item.type == "Series" and prefs.get("seasons_enabled"):
                try:
                    for s in await client.seasons(item.id):
                        seen.add(s["id"])
                        counts["items"] += 1
                        _upsert_season(s, item, lib, policy, now)
                except JellyfinError as exc:
                    logger.warning(f"Studio: seasons of {item.name!r} not read: {exc}")
    # Items that left Jellyfin (or whose library was switched off) stay in the
    # table, marked absent, so their history survives a library re-scan.
    present = db.query("SELECT jf_id FROM items WHERE present = 1")
    for row in present:
        if row["jf_id"] not in seen:
            db.execute("UPDATE items SET present = 0 WHERE jf_id = ?", (row["jf_id"],))
    db.set_setting("last_scan_at", now)
    return counts


async def _refresh_tags(jf: Client, row: dict) -> None:
    """Jellyfin's current image tags for one item, as the scan would record them."""
    from . import artwork
    try:
        item = await jf.item(row["jf_id"])
    except JellyfinError:
        return
    if item.image_tag != row.get("jf_image_tag"):
        row["jf_image_tag"] = item.image_tag
        db.execute("UPDATE items SET jf_image_tag = ? WHERE jf_id = ?", (item.image_tag, row["jf_id"]))
    for kind in artwork.KINDS:
        artwork.record_seen(row["jf_id"], kind, item.tag(kind))


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
    from . import artwork
    for kind in artwork.KINDS:
        artwork.record_seen(item.id, kind, item.tag(kind))
    if old is not None:
        _keep_your_choices(old, row)
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


def _keep_your_choices(old: dict, row: dict) -> None:
    """Jellyfin changed its mind about which title this is (a re-identify, a
    plugin): what you set for it must not silently stop applying.

    Your rules are filed under the title's key.  When Jellyfin's new ids give
    another key and you had set something under the old one, the item stays
    the title it was: the old id is recorded as a match of yours (changeable
    on its page).  A title that had no id before takes its rules along to the
    new key instead.  *row* is updated in place, and saved."""
    if identity.source(old) != "auto":
        return                       # identified by you already: Jellyfin's ids don't decide
    was, now = rules.title_key(old), rules.title_key(row)
    if was == now or not rules.is_customised(was):
        return
    if was.startswith("jf:"):
        if rules.move_title(was, now):
            logger.info(f"Studio: {row['name']!r} got an id from Jellyfin; your choices moved to {now}")
        return
    if was.startswith("stage:"):
        fields = {"match_source": "stage", "manual_stage_id": was.split(":", 1)[1]}
    elif was.startswith("tmdb:"):
        fields = {"match_source": "tmdb", "manual_tmdb_id": was.split(":")[2]}
    else:
        return
    row.update(fields)
    db.execute(f"UPDATE items SET {', '.join(f'{k} = ?' for k in fields)} WHERE jf_id = ?",
               (*fields.values(), row["jf_id"]))
    logger.info(f"Studio: Jellyfin now files {row['name']!r} under {now}; it has choices of yours, so it stays {was}")


def _upsert_season(s: dict, series: Item, lib: Library, policy: dict, now: float) -> None:
    """A season row: the show's ids, its own Jellyfin id, image tag and number."""
    name = f"{series.name} — {s['name'] or ('Specials' if s['number'] == 0 else 'Season ' + str(s['number']))}"
    old = db.query_one("SELECT * FROM items WHERE jf_id = ?", (s["id"],))
    show = db.query_one("SELECT match_source, manual_tmdb_id FROM items WHERE jf_id = ?", (series.id,)) or {}
    row = dict(old or {})
    # A season follows its show's identity, the one you set by hand included.
    row.update({"jf_id": s["id"], "jf_type": "Season", "tmdb_id": series.tmdb_id, "imdb_id": None,
                "match_source": show.get("match_source") or "", "manual_tmdb_id": show.get("manual_tmdb_id")})
    status = _item_status(row, policy)
    if status == NEEDS_MATCH:
        status = LEFT_ALONE   # the show is what needs matching, not each of its seasons
    if old is None:
        db.execute(
            "INSERT INTO items (jf_id, library_id, library_name, jf_type, name, year, tmdb_id, match_source, "
            "manual_tmdb_id, tvdb_id, jf_image_tag, status, parent_jf_id, season_number, added_at, seen_at) "
            "VALUES (?, ?, ?, 'Season', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (s["id"], lib.id, lib.name, name, series.year, series.tmdb_id, row["match_source"], row["manual_tmdb_id"],
             series.tvdb_id, s["image_tag"], status, series.id, s["number"], now, now))
    else:
        db.execute(
            "UPDATE items SET library_id=?, library_name=?, name=?, year=?, tmdb_id=?, match_source=?, "
            "manual_tmdb_id=?, tvdb_id=?, jf_image_tag=?, status=?, parent_jf_id=?, season_number=?, present=1, "
            "seen_at=? WHERE jf_id=?",
            (lib.id, lib.name, name, series.year, series.tmdb_id, row["match_source"], row["manual_tmdb_id"],
             series.tvdb_id, s["image_tag"], status, series.id, s["number"], now, s["id"]))


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
    if params.get("shape") != "landscape":
        # Thumb (landscape) settings live in the same styles; a poster ignores
        # them, and leaving them out keeps its render cache key unchanged.
        params = {k: v for k, v in params.items() if not k.startswith("landscape_")}
    params.update(identity.render_ids(row))
    params["type"] = "tv" if row.get("jf_type") in ("Series", "Season") else "movie"
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


def looks_the_same(a: bytes, b: bytes, *, level: int = 48, max_pixels: int = 50) -> bool:
    """Whether two renders of a poster differ only by compression noise.

    A render can come out a few levels different with nothing changed (the art
    was decoded from PostersPlus's re-encoded disk copy instead of the fresh
    download, say).  Measured on real 1000x1500 posters (2026-09-30): that
    noise peaks at ~17 levels with no pixel past 48, while the smallest real
    change — one digit of a notch label — moves 2,200+ pixels past 48.  So
    "fewer than *max_pixels* pixels differ by more than *level*" is the same
    poster.  Anything that can't be decoded counts as different."""
    try:
        import io
        from PIL import Image, ImageChops
        ia, ib = (Image.open(io.BytesIO(x)).convert("RGB") for x in (a, b))
        if ia.size != ib.size:
            return False
        hist = ImageChops.difference(ia, ib).convert("L").histogram()
        return sum(hist[level + 1:]) < max_pixels
    except Exception:
        return False


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
    if row.get("pushed_tag") and row.get("jf_image_tag") != row["pushed_tag"]:
        return Decision("upload", "reverted")   # replaced, or removed: Jellyfin has no poster at all
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
            async with jellyfin_client(transport=jf_transport, wait=JELLYFIN_WAIT) as jf:
                if item_ids is None:
                    await scan(jf)
                rows = _selected(item_ids)
                if item_ids is not None:
                    # A push of chosen titles skips the scan: look at what Jellyfin
                    # holds right now, so an image it replaced is sent again.
                    for row in rows:
                        await _refresh_tags(jf, row)
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
                            if progress.cancel or jf.down:
                                return
                            progress.current = row["name"]
                            action = await _process(row, jf, http, style, resolution, with_quality,
                                                    access_key, dry_run, force, run_id,
                                                    advance=trigger == "schedule")
                            counts[action] = counts.get(action, 0) + 1
                            if action not in ("skipped", "error"):
                                for extra in await _process_art(row, jf, dry_run, force, run_id,
                                                                advance=trigger == "schedule"):
                                    counts[extra] = counts.get(extra, 0) + 1
                            progress.done += 1
                            progress.counts = dict(counts)

                    await asyncio.gather(*(one(r) for r in rows))
                gone = jf.down
            if progress.cancel:
                status, message = "cancelled", "Stopped by you"
            elif gone:
                status = "failed"
                message = (f"Jellyfin stopped answering and didn't come back in {int(JELLYFIN_WAIT // 60)} minutes: "
                           "the titles not reached are left for the next run")
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
    try:
        res = rules.resolve(rules.title_key(row), advance=advance)
        if res.skip:
            db.log_run_item(run_id, jf_id, name, "skipped", res.reason)
            return "skipped"
        own = identity.kind(row) == identity.OWN
        if own and (row["jf_type"] == "Season" or not res.params.get("art_poster")):
            # Nothing to look it up by and no image of yours: Jellyfin's poster stays.
            db.log_run_item(run_id, jf_id, name, "skipped",
                            "Left alone (not identified)" if row["status"] == LEFT_ALONE or row["jf_type"] == "Season"
                            else "Not identified, and no image of yours pinned: do either on its page")
            return "skipped"
        if with_quality and row["jf_type"] == "Series" and not row.get("quality"):
            ep = await jf.representative_episode(jf_id)
            row["quality"] = ",".join(quality_tokens(ep)) if ep else ""
        params = res.params
        if row["jf_type"] == "Season":
            from . import seasons
            params = await seasons.params_for(row, params)
        if in_process(row):
            from . import stage
            try:
                image, ctype = await stage.render(row, style, params, resolution)
            except stage.NoArt as exc:
                if row["status"] == ERROR:
                    db.execute("UPDATE items SET status = ?, last_error = NULL WHERE jf_id = ?", (NEW, jf_id))
                db.log_run_item(run_id, jf_id, name, "skipped", str(exc))
                return "skipped"
        else:
            from . import artwork
            params = await artwork.realize(params)
            image, ctype = await render(http, poster_url(row, style, resolution=resolution, with_quality=with_quality,
                                                         access_key=access_key, extra=params))
        image_hash = hashlib.sha256(image).hexdigest()
        decision = decide(row, image_hash, force=force)
        if decision.reason == "changed":
            # Only the bytes may have changed: compare with what Jellyfin holds.
            try:
                current, _ = await jf.primary_image(jf_id, max_height=None)
            except JellyfinError:
                current = None
            if current is not None and looks_the_same(current, image):
                if not dry_run:
                    db.execute("UPDATE items SET pushed_hash = ?, status = ?, last_error = NULL WHERE jf_id = ?",
                               (image_hash, OK, jf_id))
                return "unchanged"
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


async def _process_art(row, jf, dry_run, force, run_id, advance: bool = False) -> list[str]:
    """Backdrop, Logo and Thumb for one item, where Studio manages them.
    Same rules as the poster: send only what changed (noise ignored), put
    back what Jellyfin replaced.

    What Jellyfin holds is read and compared each time, instead of trusting
    its image tags: they said "ours is still there" for backdrops that were
    Jellyfin's own, and "replaced" for ones that weren't."""
    from . import artwork
    key, jf_id, name = rules.title_key(row), row["jf_id"], row["name"]
    actions = []
    for kind in artwork.KINDS:
        if not artwork.managed(key, kind):
            continue
        label = artwork.JF_TYPE[kind]
        try:
            res = await artwork.resolve(row, kind, advance=advance)
            if res is None:
                continue
            data, ctype = res
            image_hash = hashlib.sha256(data).hexdigest()
            st = artwork.state(jf_id, kind)
            try:
                current, _ = await jf.image(jf_id, label)
            except JellyfinError:
                current = None
            if not force and current is not None and (current == data or looks_the_same(current, data)):
                if not dry_run and st.get("pushed_hash") != image_hash:
                    artwork.record_pushed(jf_id, kind, image_hash, st.get("seen_tag"))
                continue
            # The image we sent before and nothing changed on our side: Jellyfin replaced it.
            reverted = not force and st.get("pushed_hash") == image_hash
            reason = "forced" if force else "reverted" if reverted else "changed" if st.get("pushed_hash") else "new"
            if dry_run:
                db.log_run_item(run_id, jf_id, name, "would_upload", f"{label}: {reason}")
                actions.append("would_upload")
                continue
            if kind == "backdrop":
                await jf.replace_backdrop(jf_id, data, ctype)
            else:
                await jf.upload_image(jf_id, label, data, ctype)
                try:
                    await jf.image(jf_id, label)
                except JellyfinError:
                    raise JellyfinError(f"Jellyfin can't give back the {kind} it was just sent (if the title is on "
                                        "a drive that was unplugged, restart Jellyfin)") from None
            fresh = await jf.item(jf_id)
            artwork.record_pushed(jf_id, kind, image_hash, fresh.tag(kind))
            act = "reverted" if reverted else "uploaded"
            db.log_run_item(run_id, jf_id, name, act, f"{label}: {reason}")
            actions.append(act)
        except Exception as exc:
            db.log_run_item(run_id, jf_id, name, "error", f"{label}: {str(exc)[:300] or type(exc).__name__}")
            actions.append("error")
    return actions


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
                try:
                    from . import backup
                    backup.write_daily()
                except Exception:
                    logger.exception("Studio: nightly rules backup failed")
        except Exception:
            logger.exception("Studio scheduler")
        await asyncio.sleep(30)
