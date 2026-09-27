"""Rating provider badges: each chosen provider's own score behind its logo,
in place of the ★ and the single weighted score (rating_badges=imdb,tomatoes).

Drawn by three of the rating display modes — Clean (the badges replace the
star), Minimalist (small icons before each score) and the frosted Bar
(spread across it in place of its label, RPDB-style) — and not by the Rating
Bar, whose score is the bar itself.

Nothing trademarked ships in the repo.  Each mark is fetched once, pinned by
SHA-1 so an edit upstream can never change a poster, and kept in the cache
volume beside the graphic badges' marks:

  IMDb, Rotten Tomatoes (Tomatometer fresh / rotten, Popcornmeter up / down),
  Metacritic, Letterboxd, Trakt, MyAnimeList, AniList
      Wikimedia Commons, all public domain (below the threshold of originality)
  TMDB    TMDB's own short logo, from its logos & attribution page
  Kitsu   Simple Icons (CC0), set white on a square of Kitsu's brand colour

Roger Ebert has no logo anywhere to fetch, so it is a small text chip.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import os
import time
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from config import SCORE_NORMALISERS
from graphic_badges import ASSET_DIR, _USER_AGENT, _runs

logger = logging.getLogger(__name__)

_FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")

# Every source a badge can be drawn for, in the configurator's default order.
PROVIDERS = (
    "imdb", "tomatoes", "popcorn", "metacritic", "metacriticuser", "letterboxd",
    "trakt", "tmdb", "rogerebert", "myanimelist", "anilist", "kitsu",
)
_MAX_BADGES = 6

SCALES = ("native", "normalized")


@dataclass(frozen=True)
class _Source:
    url: str
    sha1: str   # of the exact file these marks were built against
    ext: str


def _commons(title: str, sha1: str) -> _Source:
    return _Source("https://commons.wikimedia.org/wiki/Special:FilePath/" + title.replace(" ", "_"),
                   sha1, os.path.splitext(title)[1])


_FILES = {
    "imdb":        _commons("IMDB Logo 2016.svg",                    "0a1b5cd02f8aecdf4b9bbf1bda2950487e5c29c0"),
    "rt_fresh":    _commons("Rotten Tomatoes.svg",                   "5099a1d8f0dccfa0584f25387d08ecc1434cb375"),
    "rt_rotten":   _commons("Rotten Tomatoes rotten.svg",            "4b2867d30f31941105f80f544acf1e283946e4e8"),
    "rt_upright":  _commons("Rotten Tomatoes positive audience.svg", "63bbcf6deb1bf8ec8268052e1cc41681794fed93"),
    "rt_spilled":  _commons("Rotten Tomatoes negative audience.svg", "83ecbf269edf953837f6c930af510043e42e3964"),
    "metacritic":  _commons("Metacritic logo Roundel.svg",           "f36dd4ffb2045f91a84ae91cc73dd1fd33369cde"),
    "letterboxd":  _commons("Letterboxd 2018 logo (vertical).svg",   "3c22c25f6e1ce3a26c0bd59e7f70729ddbaddb6b"),
    "trakt":       _commons("Trakt.tv-favicon.svg",                  "87ee5c6b8e0e4370837299905c1f105f32b23843"),
    "myanimelist": _commons("MyAnimeList favicon.svg",               "a4ef3a65aaed28a1e32f7c070230427f1e3ca8b6"),
    "anilist":     _commons("AniList logo.svg",                      "9ca4ba567100a290d007d43c6beae38728061909"),
    "tmdb":        _Source("https://www.themoviedb.org/assets/2/v4/logos/v2/"
                           "blue_short-8e7b30f73a4020692ccca9c88bafe5dcb6f8a62a4c6bc55cd9ba82bb2cd95f6c.svg",
                           "07c6b2c2481d9581f640c5f00448114bd3829930", ".svg"),
    "kitsu":       _Source("https://cdn.jsdelivr.net/npm/simple-icons@16.32.0/icons/kitsu.svg",
                           "317e69e7dcc993124443042f080eff50b0179401", ".svg"),
}

# Rotten Tomatoes' own thresholds: a Tomatometer of 60 or more is fresh, and
# a Popcornmeter of 60 or more an upright bucket.  (Certified Fresh has no
# free mark to draw.)
_RT_FRESH = 60


def _mark_key(provider: str, value) -> str | None:
    """The file a provider's badge is drawn from, for this score."""
    if provider == "tomatoes":
        return "rt_fresh" if value >= _RT_FRESH else "rt_rotten"
    if provider == "popcorn":
        return "rt_upright" if value >= _RT_FRESH else "rt_spilled"
    if provider in ("metacritic", "metacriticuser"):
        return "metacritic"
    if provider == "rogerebert":
        return None
    return provider


def _asset_path(key: str) -> str:
    f = _FILES[key]
    return os.path.join(ASSET_DIR, f"{f.sha1}{f.ext}")


def _keys_for(providers) -> set[str]:
    keys: set[str] = set()
    for p in providers:
        if p == "tomatoes":
            keys |= {"rt_fresh", "rt_rotten"}
        elif p == "popcorn":
            keys |= {"rt_upright", "rt_spilled"}
        elif p != "rogerebert":
            keys.add(_mark_key(p, 0))
    return keys


def assets_ready(providers) -> bool:
    return all(os.path.exists(_asset_path(k)) for k in _keys_for(providers))


_fetch_lock = asyncio.Lock()
# A failed file is left alone this long, so an unreachable or throttling host
# (Commons answers a burst with 429) isn't asked again on every request.
_RETRY_AFTER = 600.0
_failed_at: dict[str, float] = {}


async def ensure_assets(client, providers) -> bool:
    """Download any missing mark the request's providers draw.  Cheap once
    they are on disk; a failed download leaves that badge out rather than
    failing the render.  True when every mark they need is on disk."""
    if assets_ready(providers):
        return True
    async with _fetch_lock:
        os.makedirs(ASSET_DIR, exist_ok=True)
        fetched = False
        for key in sorted(_keys_for(providers)):
            path = _asset_path(key)
            if os.path.exists(path) or time.monotonic() - _failed_at.get(key, -_RETRY_AFTER) < _RETRY_AFTER:
                continue
            f = _FILES[key]
            try:
                resp = await client.get(f.url, headers={"User-Agent": _USER_AGENT},
                                        follow_redirects=True, timeout=15)
                resp.raise_for_status()
            except Exception as exc:
                _failed_at[key] = time.monotonic()
                logger.warning(f"Rating badges: fetch failed for {key}: {exc}")
                continue
            if hashlib.sha1(resp.content).hexdigest() != f.sha1:
                # Upstream now serves a different file.  Keep drawing without
                # it rather than put an unreviewed logo on every poster.
                _failed_at[key] = time.monotonic()
                logger.warning(f"Rating badges: {key} no longer matches its pinned SHA-1; skipped")
                continue
            tmp = path + ".part"
            with open(tmp, "wb") as fh:
                fh.write(resp.content)
            os.replace(tmp, path)
            fetched = True
            logger.info(f"Rating badges: cached {key}")
        if fetched:
            _mark_rgba.cache_clear()
            badge.cache_clear()
    return assets_ready(providers)


# ---------------------------------------------------------------------------
# Marks
# ---------------------------------------------------------------------------

_WORK_H = 400   # raster height every mark is cut from


def _crop(a: np.ndarray) -> np.ndarray:
    on = a[..., 3] > 8
    if not on.any():
        return a[:1, :1]
    rows, cols = np.flatnonzero(on.any(axis=1)), np.flatnonzero(on.any(axis=0))
    return a[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]


def _svg_rgba(path: str, h: int = _WORK_H) -> np.ndarray:
    import cairosvg
    png = cairosvg.svg2png(url=path, output_height=h)
    return np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"))


_KITSU_RGB = (0xFD, 0x75, 0x5C)   # Simple Icons' brand colour for Kitsu


@lru_cache(maxsize=None)
def _mark_rgba(key: str) -> Image.Image | None:
    """A mark at _WORK_H, cropped to its ink, or None when its file isn't on disk."""
    path = _asset_path(key)
    if not os.path.exists(path):
        return None
    try:
        if key == "letterboxd":
            # The three dots, without the wordmark set under them.
            a = _svg_rgba(path, _WORK_H * 3)
            top = _runs(a[..., 3].max(axis=1) > 8)[0]
            a = a[top[0]:top[1]]
        elif key == "kitsu":
            # A white glyph on a rounded square, like the app-icon marks it
            # sits beside (Trakt, MyAnimeList, AniList).
            glyph = Image.fromarray(_crop(_svg_rgba(path, round(_WORK_H * 0.62))))
            tile = Image.new("RGBA", (_WORK_H * 4, _WORK_H * 4), (0, 0, 0, 0))
            ImageDraw.Draw(tile).rounded_rectangle(
                (0, 0, tile.width - 1, tile.height - 1), radius=_WORK_H * 4 // 5, fill=(*_KITSU_RGB, 255))
            tile = tile.resize((_WORK_H, _WORK_H), Image.Resampling.LANCZOS)
            white = Image.new("RGBA", glyph.size, (255, 255, 255, 0))
            white.putalpha(glyph.getchannel("A"))
            tile.alpha_composite(white, ((_WORK_H - glyph.width) // 2, (_WORK_H - glyph.height) // 2))
            return tile
        else:
            a = _svg_rgba(path)
        return Image.fromarray(np.ascontiguousarray(_crop(a)))
    except Exception as exc:
        logger.error(f"Rating badges: {key} mark failed: {exc}")
        return None


# Marks come in every shape, so they're sized by area rather than height,
# as the network and studio logos are: each covers about the area of a box
# _AREA_W row heights wide and one high — no taller than the row, so a square
# icon and IMDb's wide box carry the same weight — and no wider than _MAX_W.
_AREA_W = 1.25
_MAX_W  = 2.6


def _size(w: int, h: int, row_h: int) -> tuple[int, int]:
    aspect = w / h
    bh = min(row_h, (row_h * row_h * _AREA_W / aspect) ** 0.5)
    bw = min(bh * aspect, row_h * _MAX_W)
    return max(1, round(bw)), max(1, round(bw / aspect))


_EBERT_INK  = (255, 255, 255, 255)
_EBERT_FILL = (34, 34, 38, 235)
_EBERT_EDGE = (150, 150, 158, 255)   # so the chip holds its shape on dark art


def _ebert_chip(row_h: int) -> Image.Image:
    """RogerEbert.com publishes no mark, so its badge is its name in a chip."""
    h = max(4, round(row_h * 0.78))
    try:
        font = ImageFont.truetype(os.path.join(_FONTS_DIR, "Inter-Bold.ttf"), max(4, round(h * 0.62)))
    except IOError:
        font = ImageFont.load_default()
    text = "Ebert"
    l, t, r, b = font.getbbox(text)
    w = (r - l) + round(h * 0.6)
    ss = 4
    chip = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
    ImageDraw.Draw(chip).rounded_rectangle((0, 0, w * ss - 1, h * ss - 1), radius=round(h * ss * 0.24),
                                           fill=_EBERT_FILL, outline=_EBERT_EDGE, width=max(ss, round(h * ss * 0.06)))
    chip = chip.resize((w, h), Image.Resampling.LANCZOS)
    ImageDraw.Draw(chip).text(((w - (r - l)) / 2 - l, (h - (b - t)) / 2 - t), text, font=font, fill=_EBERT_INK)
    return chip


@lru_cache(maxsize=256)
def badge(provider: str, fresh: bool, row_h: int) -> Image.Image | None:
    """The badge for *provider* in a row *row_h* tall; *fresh* picks the
    Tomatometer / Popcornmeter state.  None when its mark isn't on disk."""
    if provider == "rogerebert":
        return _ebert_chip(row_h)
    key = _mark_key(provider, _RT_FRESH if fresh else 0)
    src = _mark_rgba(key) if key else None
    if src is None:
        return None
    return src.resize(_size(src.width, src.height, row_h), Image.Resampling.LANCZOS)


# ---------------------------------------------------------------------------
# Scores
# ---------------------------------------------------------------------------

# How each provider writes its own score (value as MDBList reports it).
_PERCENT = {"tomatoes", "popcorn", "trakt", "tmdb", "anilist", "kitsu"}


def _native(provider: str, value: float) -> str:
    if provider in _PERCENT:
        return f"{round(value)}%"
    if provider == "metacritic":
        return str(round(value))
    if provider == "myanimelist":
        return f"{value:.2f}"
    # IMDb and Metacritic users out of 10, Letterboxd out of 5, Ebert out of 4.
    return f"{value:.1f}"


def score_text(provider: str, value: float, scale: str, out_of_10: bool) -> str:
    """*value* as the badge prints it: the provider's own form ("7.8", "92%",
    "3.9") or, normalised, the scale the weighted score is shown on."""
    if scale == "native":
        return _native(provider, value)
    score = round(SCORE_NORMALISERS[provider](value))
    if out_of_10:
        return "10" if score >= 100 else f"{score / 10:.1f}"
    return str(score)


def parse_providers(raw: str | None) -> str:
    """rating_badges in canonical spelling: known providers, first mention
    kept, at most _MAX_BADGES.  "" when none."""
    seen: list[str] = []
    for token in (raw or "").lower().replace(" ", "").split(","):
        if token in PROVIDERS and token not in seen:
            seen.append(token)
    return ",".join(seen[:_MAX_BADGES])


def entries(ratings: dict | None, providers: str) -> list[tuple[str, float]]:
    """(provider, value) for each chosen provider the title has a score from,
    in the chosen order."""
    if not ratings or not providers:
        return []
    out = []
    for p in providers.split(","):
        value = ratings.get(p)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out.append((p, float(value)))
    return out


# ---------------------------------------------------------------------------
# Runs: a line of text and badges laid out left to right
# ---------------------------------------------------------------------------
#
# A run is a list of pieces — ("text", str), ("badge", Image) or ("gap", px) —
# so each mode can put the badges where its ★ was without knowing how they
# are sized or spaced.

_BADGE_ROW   = 0.95   # badge row height, of the font size
_SCORE_GAP   = 0.24   # badge → its score, of the font size
_ENTRY_GAP   = 0.55   # one score → the next badge, of the font size


def rating_run(items: list[tuple[str, float]], font_size: float, scale: str,
               out_of_10: bool) -> list[tuple]:
    """The pieces for *items*.  A badge whose mark isn't on disk falls back
    to the provider's score alone rather than dropping the score."""
    row_h = max(4, round(font_size * _BADGE_ROW))
    run: list[tuple] = []
    for i, (provider, value) in enumerate(items):
        if i:
            run.append(("gap", font_size * _ENTRY_GAP))
        im = badge(provider, value >= _RT_FRESH, row_h)
        if im is not None:
            run += [("badge", im), ("gap", font_size * _SCORE_GAP)]
        run.append(("text", score_text(provider, value, scale, out_of_10)))
    return run


def run_width(run: list[tuple], measure) -> float:
    """Width of *run*, with *measure(text)* the width of a text piece."""
    total = 0.0
    for kind, v in run:
        total += measure(v) if kind == "text" else v.width if kind == "badge" else v
    return total


def draw_run(image: Image.Image, draw: ImageDraw.ImageDraw, run: list[tuple], x: float,
             y: float, font, fill, measure) -> float:
    """Draw *run* with its text's top at *y* (as ImageDraw.text takes it) and
    every badge centred on the digits.  Returns the x it ended at."""
    l, t, r, b = font.getbbox("0")
    digit_cy = y + (t + b) / 2
    for kind, v in run:
        if kind == "text":
            draw.text((round(x), y), v, font=font, fill=fill)
            x += measure(v)
        elif kind == "badge":
            image.alpha_composite(v, (round(x), round(digit_cy - v.height / 2)))
            x += v.width
        else:
            x += v
    return x
