"""Jellyfin REST client for Studio (async httpx, no SDK).

The quality-token logic is ported from jellyfin_sync.py (the host-side script
Studio replaces); see that file's docstring for the reasoning behind each rule.
"""
from __future__ import annotations

import asyncio
import base64
import re
import time
from dataclasses import dataclass, field

import httpx

_AUTH = ('MediaBrowser Client="PostersPlus Studio", Device="server", '
         'DeviceId="postersplus-studio", Version="1.0", Token="{token}"')
_PAGE = 200
# Seconds between tries while Jellyfin is restarting (the last one repeats).
_RETRY_DELAYS = (2.0, 4.0, 8.0, 15.0)
ITEM_FIELDS = "ProviderIds,MediaSources,MediaStreams,Path,ProductionYear,Chapters"


class JellyfinError(Exception):
    pass


@dataclass
class Library:
    id: str
    name: str
    collection_type: str   # "movies", "tvshows", "homevideos", "" (mixed) ...


@dataclass
class Item:
    id: str
    name: str
    type: str              # Movie, Series, Video
    year: int | None
    tmdb_id: str | None
    imdb_id: str | None
    tvdb_id: str | None
    stage_show_id: str | None
    image_tag: str | None  # ImageTags.Primary: changes whenever the Primary image does
    raw: dict = field(repr=False, default_factory=dict)

    def tag(self, kind: str) -> str | None:
        """The current tag of another image type: backdrop (the first), logo, thumb."""
        if kind == "backdrop":
            tags = self.raw.get("BackdropImageTags") or []
            return tags[0] if tags else None
        return (self.raw.get("ImageTags") or {}).get({"logo": "Logo", "thumb": "Thumb"}[kind])


def item_from_json(d: dict) -> Item:
    ids = {str(k).lower(): str(v) for k, v in (d.get("ProviderIds") or {}).items() if v}
    return Item(
        id=str(d.get("Id") or ""),
        name=d.get("Name") or d.get("OriginalTitle") or "?",
        type=d.get("Type") or "",
        year=d.get("ProductionYear"),
        tmdb_id=ids.get("tmdb") if (ids.get("tmdb") or "").isdigit() else None,
        imdb_id=ids.get("imdb") if (ids.get("imdb") or "").startswith("tt") else None,
        tvdb_id=ids.get("tvdb"),
        stage_show_id=ids.get("stagemediashowid") if (ids.get("stagemediashowid") or "").isdigit() else None,
        image_tag=(d.get("ImageTags") or {}).get("Primary"),
        raw=d,
    )


class Client:
    """One per operation (a scan, a run, a test): cheap to make, closes cleanly.

    With *wait* (seconds) the client sits out a Jellyfin restart instead of
    failing: see `_request`.  `down` is set once that wait ran out."""

    def __init__(self, base_url: str, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None,
                 wait: float = 0.0):
        if not base_url or not api_key:
            raise JellyfinError("Jellyfin isn't set up yet: add its address and API key in Settings.")
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": _AUTH.format(token=api_key)},
            timeout=httpx.Timeout(30.0, connect=8.0),
            transport=transport,
        )
        self._wait = wait
        self.down = False

    async def __aenter__(self) -> "Client":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kw) -> httpx.Response:
        """One request.  A refused connection or a 503 (Jellyfin starting up)
        means the request never ran, so it is safe to send again, uploads and
        deletes included: it is retried until Jellyfin answers or *wait* is
        used up.  After that the client stops waiting (`down`), so what is
        left of a run fails at once instead of waiting once per title."""
        deadline = time.monotonic() + self._wait
        attempt = 0
        while True:
            error = None
            try:
                resp = await self._http.request(method, path, **kw)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                error = exc
            if error is None and resp.status_code != 503:
                return resp
            if not self._wait or self.down or time.monotonic() >= deadline:
                self.down = bool(self._wait)
                if error is not None:
                    raise error
                return resp
            await asyncio.sleep(_RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)])
            attempt += 1

    async def _get(self, path: str, **params) -> dict | list:
        try:
            resp = await self._request("GET", path, params={k: v for k, v in params.items() if v is not None})
        except httpx.HTTPError as exc:
            raise JellyfinError(f"Can't reach Jellyfin ({type(exc).__name__})") from exc
        if resp.status_code == 401:
            raise JellyfinError("Jellyfin rejected the API key")
        if resp.status_code >= 400:
            raise JellyfinError(f"Jellyfin answered HTTP {resp.status_code} for {path}")
        return resp.json()

    async def server_info(self) -> dict:
        info = await self._get("/System/Info")
        return {"name": info.get("ServerName"), "version": info.get("Version")}

    async def libraries(self) -> list[Library]:
        data = await self._get("/Library/VirtualFolders")
        rows = data if isinstance(data, list) else (data.get("Items") or [])
        return [Library(str(r.get("ItemId") or ""), r.get("Name") or "?", (r.get("CollectionType") or "").lower())
                for r in rows if r.get("ItemId")]

    async def _paged(self, path: str, **params):
        start = 0
        while True:
            data = await self._get(path, **params, StartIndex=start, Limit=_PAGE)
            items = data.get("Items") or []
            if not items:
                return
            for d in items:
                yield d
            start += len(items)
            total = data.get("TotalRecordCount")
            if total is None or start >= total:
                return

    async def library_items(self, library: Library) -> list[Item]:
        """The top-level titles of a library: shows for TV, music videos for a
        music-video library (how Jellyfin files concerts), films/videos otherwise."""
        types = {"tvshows": "Series", "musicvideos": "MusicVideo"}.get(library.collection_type, "Movie,Video")
        return [item_from_json(d) async for d in self._paged(
            "/Items", ParentId=library.id, Recursive="true", IncludeItemTypes=types, Fields=ITEM_FIELDS)]

    async def seasons(self, series_id: str) -> list[dict]:
        """A show's seasons: [{id, number, name, image_tag}], specials as number 0."""
        data = await self._get(f"/Shows/{series_id}/Seasons", Fields="ProviderIds")
        out = []
        for d in data.get("Items") or []:
            if d.get("Type") != "Season" or d.get("IndexNumber") is None:
                continue
            out.append({"id": str(d["Id"]), "number": int(d["IndexNumber"]), "name": d.get("Name") or "",
                        "image_tag": (d.get("ImageTags") or {}).get("Primary")})
        return out

    async def recordings(self, series_id: str) -> list[dict]:
        """A theatre show's recordings as the Encora plugin files them: each
        episode's production (its season: Broadway, West End…), theatre (its
        studio) and date.  [] when Jellyfin can't say."""
        try:
            eps = [d async for d in self._paged(f"/Shows/{series_id}/Episodes", Fields="Studios,PremiereDate")]
        except JellyfinError:
            return []
        return [{"production": e.get("SeasonName") or "",
                 "venue": ((e.get("Studios") or [{}])[0] or {}).get("Name") or "",
                 "date": (e.get("PremiereDate") or "")[:10]} for e in eps]

    async def item(self, item_id: str) -> Item:
        data = await self._get("/Items", Ids=item_id, Fields=ITEM_FIELDS)
        rows = data.get("Items") or []
        if not rows:
            raise JellyfinError("That item is no longer in Jellyfin")
        return item_from_json(rows[0])

    async def representative_episode(self, series_id: str) -> dict | None:
        """S01E01, else the lowest real episode (see jellyfin_sync.representative_episode)."""
        try:
            eps = [d async for d in self._paged(f"/Shows/{series_id}/Episodes", Fields=ITEM_FIELDS)]
        except JellyfinError:
            return None
        if not eps:
            return None
        for ep in eps:
            if ep.get("ParentIndexNumber") == 1 and ep.get("IndexNumber") == 1:
                return ep
        real = [e for e in eps if (e.get("ParentIndexNumber") or 0) > 0] or eps
        return min(real, key=lambda e: (e.get("ParentIndexNumber") or 0, e.get("IndexNumber") or 0))

    async def primary_image(self, item_id: str, max_height: int | None = 450) -> tuple[bytes, str]:
        """The item's poster, resized by Jellyfin, or with max_height=None the
        stored file exactly as it is."""
        params = {"maxHeight": max_height, "quality": 85} if max_height else {}
        try:
            resp = await self._request("GET", f"/Items/{item_id}/Images/Primary", params=params)
        except httpx.HTTPError as exc:
            raise JellyfinError(f"Can't reach Jellyfin ({type(exc).__name__})") from exc
        if resp.status_code != 200:
            raise JellyfinError(f"No image ({resp.status_code})")
        return resp.content, resp.headers.get("content-type", "image/jpeg")

    async def upload_primary(self, item_id: str, image: bytes, content_type: str) -> None:
        await self.upload_image(item_id, "Primary", image, content_type)

    async def upload_image(self, item_id: str, image_type: str, image: bytes, content_type: str,
                           index: int | None = None) -> None:
        """Jellyfin wants the body base64-encoded and a real image MIME type.
        Backdrops go to an index (0 = the main one) so they replace, not add."""
        path = f"/Items/{item_id}/Images/{image_type}" + (f"/{index}" if index is not None else "")
        try:
            resp = await self._request("POST", path, content=base64.b64encode(image),
                                       headers={"Content-Type": content_type})
        except httpx.HTTPError as exc:
            raise JellyfinError(f"Upload failed ({type(exc).__name__})") from exc
        if resp.status_code >= 400:
            raise JellyfinError(f"Upload refused: HTTP {resp.status_code}")

    async def replace_backdrop(self, item_id: str, image: bytes, content_type: str) -> None:
        """Replace the item's backdrop with *image*, the way an upload replaces
        a poster or a logo.

        Jellyfin keeps a list of backdrops and ignores the index on an upload:
        the image is always added at the end, so the old one stayed in front.
        So the new one is uploaded first (nothing is lost if that fails), then
        every other backdrop is removed, which leaves ours as the only one.

        Which one is ours is told by its bytes (Jellyfin stores an upload as it
        was sent).  Its tags can't say: an upload gives the backdrops already
        there new tags too, and going by "the tag that wasn't there before"
        deleted our own upload and kept Jellyfin's (seen on the live server,
        2026-10-02)."""
        await self.upload_image(item_id, "Backdrop", image, content_type)
        count = len((await self.item(item_id)).raw.get("BackdropImageTags") or [])
        ours = None
        for index in range(count - 1, -1, -1):        # added at the end: found on the first look
            if await self._stored(item_id, f"Backdrop/{index}") == image:
                ours = index
                break
        if ours is None:
            raise JellyfinError("Jellyfin can't give back the backdrop it was just sent (if the title is on a drive "
                                "that was unplugged, restart Jellyfin)")
        for index in range(count - 1, -1, -1):        # last first: deleting shifts the ones after
            if index != ours:
                await self._send("DELETE", f"/Items/{item_id}/Images/Backdrop/{index}")
        if await self._stored(item_id, "Backdrop/0") != image:
            raise JellyfinError("Jellyfin kept another backdrop in front of the one sent")

    async def _stored(self, item_id: str, image: str) -> bytes | None:
        """An image exactly as Jellyfin stores it, or None when it has none it can read."""
        try:
            resp = await self._request("GET", f"/Items/{item_id}/Images/{image}")
        except httpx.HTTPError as exc:
            raise JellyfinError(f"Can't reach Jellyfin ({type(exc).__name__})") from exc
        return resp.content if resp.status_code == 200 else None

    async def _send(self, method: str, path: str, **kw) -> None:
        try:
            resp = await self._request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise JellyfinError(f"Can't reach Jellyfin ({type(exc).__name__})") from exc
        if resp.status_code >= 400:
            raise JellyfinError(f"Jellyfin answered HTTP {resp.status_code} for {method} {path}")

    async def image(self, item_id: str, image_type: str, max_height: int | None = None) -> tuple[bytes, str]:
        """Any image of an item (Backdrop means the first); max_height=None is the stored file."""
        params = {"maxHeight": max_height, "quality": 85} if max_height else {}
        suffix = "/0" if image_type == "Backdrop" else ""
        try:
            resp = await self._request("GET", f"/Items/{item_id}/Images/{image_type}{suffix}", params=params)
        except httpx.HTTPError as exc:
            raise JellyfinError(f"Can't reach Jellyfin ({type(exc).__name__})") from exc
        if resp.status_code != 200:
            raise JellyfinError(f"No {image_type.lower()} ({resp.status_code})")
        return resp.content, resp.headers.get("content-type", "image/jpeg")


# ── Quality tokens from the file's own media info (ported) ──────────────────

_REMUX_RE = re.compile(r"remux", re.I)
_WEBDL_RE = re.compile(r"\bweb[-_. ]?dl\b", re.I)
_RANGE = {"dovi": "DV", "doviwithhdr10": "DV", "doviwithhdr10plus": "DV", "doviwithhlg": "DV",
          "doviwithel": "DV", "doviwithelhdr10plus": "DV", "hdr10plus": "HDR10+", "hdr10": "HDR10"}


def _best_source(item: dict) -> dict | None:
    sources = item.get("MediaSources") or []
    if not sources:
        streams = item.get("MediaStreams")
        return {"Path": item.get("Path") or "", "MediaStreams": streams} if streams else None

    def area(src: dict) -> int:
        for s in src.get("MediaStreams") or []:
            if (s.get("Type") or "").lower() == "video":
                return (s.get("Width") or 0) * (s.get("Height") or 0)
        return 0
    return max(sources, key=area)


def quality_tokens(item: dict) -> list[str]:
    src = _best_source(item)
    if src is None:
        return []
    tokens: set[str] = set()
    streams = src.get("MediaStreams") or []
    video = [s for s in streams if (s.get("Type") or "").lower() == "video"]
    audio = [s for s in streams if (s.get("Type") or "").lower() == "audio"]
    if video:
        w, h = video[0].get("Width") or 0, video[0].get("Height") or 0
        if w >= 3800 or h >= 2000:
            tokens.add("4K")
        elif w >= 1900 or h >= 1000:
            tokens.add("1080P")
        mapped = _RANGE.get((video[0].get("VideoRangeType") or "").strip().lower())
        if mapped:
            tokens.add(mapped)
    for s in audio:
        title = f"{s.get('DisplayTitle') or ''} {s.get('Title') or ''}".upper()
        if "ATMOS" in title:
            tokens.add("ATMOS")
        if "DTS:X" in title or "DTS-X" in title or "DTSX" in title:
            tokens.add("DTSX")
    hay = " ".join([src.get("Path") or ""] + [f"{s.get('DisplayTitle') or ''} {s.get('Title') or ''}" for s in video])
    if _REMUX_RE.search(hay):
        tokens.add("REMUX")
    elif _WEBDL_RE.search(hay):
        tokens.add("WEBDL")
    return sorted(tokens)
