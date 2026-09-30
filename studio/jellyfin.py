"""Jellyfin REST client for Studio (async httpx, no SDK).

The quality-token logic is ported from jellyfin_sync.py (the host-side script
Studio replaces); see that file's docstring for the reasoning behind each rule.
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field

import httpx

_AUTH = ('MediaBrowser Client="PostersPlus Studio", Device="server", '
         'DeviceId="postersplus-studio", Version="1.0", Token="{token}"')
_PAGE = 200
ITEM_FIELDS = "ProviderIds,MediaSources,MediaStreams,Path,ProductionYear"


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
    """One per operation (a scan, a run, a test): cheap to make, closes cleanly."""

    def __init__(self, base_url: str, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None):
        if not base_url or not api_key:
            raise JellyfinError("Jellyfin isn't set up yet: add its address and API key in Settings.")
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": _AUTH.format(token=api_key)},
            timeout=httpx.Timeout(30.0, connect=8.0),
            transport=transport,
        )

    async def __aenter__(self) -> "Client":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._http.aclose()

    async def _get(self, path: str, **params) -> dict | list:
        try:
            resp = await self._http.get(path, params={k: v for k, v in params.items() if v is not None})
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

    async def primary_image(self, item_id: str, max_height: int = 450) -> tuple[bytes, str]:
        try:
            resp = await self._http.get(f"/Items/{item_id}/Images/Primary",
                                        params={"maxHeight": max_height, "quality": 85})
        except httpx.HTTPError as exc:
            raise JellyfinError(f"Can't reach Jellyfin ({type(exc).__name__})") from exc
        if resp.status_code != 200:
            raise JellyfinError(f"No image ({resp.status_code})")
        return resp.content, resp.headers.get("content-type", "image/jpeg")

    async def upload_primary(self, item_id: str, image: bytes, content_type: str) -> None:
        """Jellyfin wants the body base64-encoded and a real image MIME type."""
        try:
            resp = await self._http.post(f"/Items/{item_id}/Images/Primary",
                                         content=base64.b64encode(image),
                                         headers={"Content-Type": content_type})
        except httpx.HTTPError as exc:
            raise JellyfinError(f"Upload failed ({type(exc).__name__})") from exc
        if resp.status_code >= 400:
            raise JellyfinError(f"Upload refused: HTTP {resp.status_code}")


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
