"""The renderer's side of Studio: per-request art and colour parameters.

main.py calls these from a few marked hooks, so the upstream renderer learns
a handful of new /poster parameters without Studio code living in main.py:

  art_poster        a poster or backdrop to draw on (TMDB path, fanart.tv/TVDB
                    url, or a stored custom:… image — the Artwork tab's allow-list)
  art_crop          x,y,zoom — a 2:3 window on art_poster (for a backdrop)
  art_original      art_poster already carries the title: no logo drawn over it
  art_logo          a logo (same allow-list), or "text" for the title as text
  art_exclude       comma list of posters/backdrops the automatic pick must skip
  art_logo_exclude  comma list of logos the automatic pick must skip
  tint_color        RRGGBB — the colour every frosted element tints from
  fade_color        RRGGBB — the colour of a tinted vignette
  logo_color        RRGGBB, logo_color_mode solid|tint — recolour the logo

Nothing here fetches anything: paths are only validated, and the renderer
fetches them exactly as it fetches the Artwork tab's picks.  An invalid value
is ignored rather than refused, like the rest of the URL parser.
"""
from __future__ import annotations

import logging

import art_overrides

logger = logging.getLogger("studio")

MAX_EXCLUDE = 60


def _valid_path(path: str) -> bool:
    try:
        art_overrides.provider_of(path)
        return True
    except ValueError:
        return False


def _paths(raw: str | None) -> tuple:
    if not raw:
        return ()
    out = [p.strip() for p in raw.split(",") if p.strip()]
    return tuple(sorted({p for p in out[:MAX_EXCLUDE] if _valid_path(p)}))


def apply_params(cfg, params: dict, parse_hex) -> None:
    poster = (params.get("art_poster") or "").strip()
    if poster and _valid_path(poster):
        cfg.art_poster = poster
        crop = (params.get("art_crop") or "").strip()
        if crop:
            try:
                parsed = art_overrides.parse_crop(crop)
                cfg.art_crop = parsed.token() if parsed else ""
            except ValueError:
                cfg.art_crop = ""
        cfg.art_original = (params.get("art_original") or "").strip().lower() in ("1", "true", "yes")
    logo = (params.get("art_logo") or "").strip()
    if logo == "text" or (logo and _valid_path(logo)):
        cfg.art_logo = logo
    cfg.art_exclude = _paths(params.get("art_exclude"))
    cfg.art_logo_exclude = _paths(params.get("art_logo_exclude"))
    cfg.tint_color = parse_hex(params.get("tint_color"))
    cfg.fade_color = parse_hex(params.get("fade_color"))
    cfg.logo_color = parse_hex(params.get("logo_color"))
    cfg.notch_text_color = parse_hex(params.get("notch_text_color"))
    off = {s.strip() for s in (params.get("sash_off") or "").split(",") if s.strip()}
    if off:
        # This title's own "never show" labels, taken out of whatever order applies.
        cfg.sash_priority = [s for s in cfg.sash_priority if s not in off]
    label = " ".join((params.get("notch_label") or "").split())
    if label and len(label) <= 40 and label.isprintable():
        cfg.notch_label = label
    if cfg.logo_color is not None and (params.get("logo_color_mode") or "").strip().lower() == "tint":
        cfg.logo_color_mode = "tint"


def poster_override(cfg):
    """The art a Studio look names, shaped like an Artwork-tab override so the
    renderer's existing path (crop, custom images, no text scan) handles it."""
    if not cfg.art_poster:
        return None
    crop = art_overrides.parse_crop(cfg.art_crop) if cfg.art_crop else None
    return art_overrides.Override(
        slot="original" if cfg.art_original else "textless", language="", path=cfg.art_poster,
        provider=art_overrides.provider_of(cfg.art_poster), sources=frozenset(art_overrides.SOURCES),
        title="", updated_at=0.0, crop=crop,
    )


def skip_excluded(excluded: tuple, poster_path, is_textless: bool, backdrop_path, tmdb_data: dict):
    """(poster_path, is_textless, backdrop_path) with the automatic pick moved
    off anything excluded: the next of TMDB's ranked textless posters, else
    the backdrop fallback, else the title's standard poster."""
    banned = set(excluded)
    if backdrop_path in banned:
        backdrop_path = None
    if poster_path is not None and poster_path in banned:
        pool = (tmdb_data.get("poster_pools") or {}).get("textless") or []
        nxt = next((p for p in pool if p not in banned), None) if is_textless else None
        if nxt is not None:
            poster_path = nxt
        else:
            original = tmdb_data.get("original_poster_path")
            poster_path = original if original and original not in banned else None
            is_textless = False
    return poster_path, is_textless, backdrop_path


def recolor_logo(logo, color: tuple, mode: str = "solid"):
    """The logo in *color*: "solid" paints every visible pixel that colour (its
    shape kept by the alpha channel); "tint" keeps its shading, mapping dark to
    a deep shade of the colour and light to the colour itself."""
    from PIL import Image, ImageOps
    rgba = logo.convert("RGBA")
    alpha = rgba.getchannel("A")
    if mode == "tint":
        gray = ImageOps.autocontrast(rgba.convert("L"))
        dark = tuple(int(c * 0.35) for c in color)
        out = ImageOps.colorize(gray, black=dark, white=tuple(color)).convert("RGBA")
    else:
        out = Image.new("RGBA", rgba.size, (*color, 255))
    out.putalpha(alpha)
    return out


def sash_candidates(meta) -> list[dict]:
    """Every notch label a title qualifies for, whether or not it's switched on:
    [{"slot", "label"}] in the full default order (for the editor's "could show")."""
    import discovery
    out = []
    for slot in discovery.ALL_PRIORITY_SLOTS:
        try:
            label = discovery._evaluate_slot(slot, meta)
        except Exception:
            label = None
        if label:
            out.append({"slot": slot, "label": label})
    return out


def without_logos(logos: list, excluded: tuple) -> list:
    banned = set(excluded)
    return [lg for lg in (logos or []) if lg.get("file_path") not in banned]
