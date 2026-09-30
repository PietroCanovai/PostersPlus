"""Studio preferences, stored in studio.db and applied live (no restart).

Safe by default: a fresh Studio never writes to Jellyfin until uploads are
switched on in Settings, and never runs on its own until the schedule is.
"""
from __future__ import annotations

import re
import secrets
from urllib.parse import parse_qsl, urlencode, urlsplit

from . import db

# The look the host-side sync used on 2026-09-30 (sync.env POSTERSPLUS_URL),
# imported as the first applied style.
DEFAULT_STYLE = (
    "top_gradient=off&sash_mode=notch&fallback_to_imdb=true&rating_display_mode=0"
    "&logo_priority=native%2Cenglish%2Coriginal%2Cneutral%2Ctext&fallback_bg_style=photoreal"
    "&logo_max_w_ratio=0.74&logo_max_h_ratio=0.24&logo_bottom_ratio=0.05&logo_bottom_anchor=true"
    "&sash_badge_size_w=1.40&sash_badge_size_h=1.20"
    "&sash_priority=default%2C-foreign%2C-true_story%2C-short_film%2C-physical%2C-streaming%2C-cinema%2C-production"
    "&badge_display_mode=0"
)

# Library names that start enabled, and what happens to their unmatched titles.
# "flag" lists them under Needs match; "leave" leaves them alone silently
# (Concerts: mostly YouTube recordings no database knows).
_DEFAULT_LIBRARIES = {"movies": "flag", "tv": "flag", "shorts": "flag", "concerts": "leave"}

RESOLUTIONS = (500, 780, 1000, 1500, 2000)

# Never part of a style: who the poster is for, or secrets.
_IDENTITY = {"tmdb_id", "imdb_id", "type", "stremio_id", "anilist_id", "kitsu_id", "quality",
             "access_key", "tmdb_key", "mdblist_key", "fanart_key", "tvdb_key", "season",
             "episode", "primary_client", "resolution", "nocache", "debug"}
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def get(key: str):
    defaults = {
        "jellyfin_url": "",
        "jellyfin_api_key": "",
        "stagemedia_key": "",
        "uploads_enabled": False,
        "seasons_enabled": False,
        "schedule_enabled": False,
        "schedule_time": "04:00",
        "resolution": 1000,
        "style_applied": DEFAULT_STYLE,
        "libraries": {},
    }
    return db.get_setting(key, defaults.get(key))


def set(key: str, value) -> None:  # noqa: A001 - module-level API
    db.set_setting(key, value)


def session_secret() -> str:
    value = db.get_setting("session_secret")
    if not value:
        value = secrets.token_hex(32)
        db.set_setting("session_secret", value)
    return value


def library_policy(library_id: str, library_name: str) -> dict:
    """{"enabled": bool, "unmatched": "flag"|"leave"} for a library, with the
    defaults above for libraries the user hasn't touched yet."""
    saved = (get("libraries") or {}).get(library_id)
    if saved:
        return {"enabled": bool(saved.get("enabled")),
                "unmatched": "leave" if saved.get("unmatched") == "leave" else "flag"}
    default = _DEFAULT_LIBRARIES.get(library_name.strip().lower())
    return {"enabled": default is not None, "unmatched": default or "flag"}


def clean_style(text: str) -> str:
    """A style as a query string: from a pasted poster URL or a bare query,
    identity and key parameters dropped.  ValueError when nothing is left."""
    text = (text or "").strip()
    query = urlsplit(text).query if ("?" in text or "://" in text) else text
    pairs = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True)
             if k not in _IDENTITY and not any(w in k for w in ("key", "token", "secret"))
             and "{" not in v]
    if not pairs:
        raise ValueError("That style has no settings in it")
    return urlencode(pairs)


def valid_time(value: str) -> bool:
    return bool(_TIME_RE.match(value or ""))
