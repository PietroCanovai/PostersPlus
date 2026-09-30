"""Logos made from text: a title set in one of the bundled fonts, as a
transparent PNG.  Saved into the title's own logos, so it works wherever a
logo does (posters, generated thumbs, Jellyfin's Logo image).
"""
from __future__ import annotations

import io
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import fonts

# Display names for the bundled fonts (fonts/*.ttf); anything else is skipped.
FONTS = {
    "bebas": ("BebasNeue-Bold.ttf", "Bebas Neue"),
    "oswald": ("Oswald-Bold.ttf", "Oswald"),
    "barlow": ("BarlowCondensed-Bold.ttf", "Barlow Condensed"),
    "roboto_condensed": ("RobotoCondensed-Bold.ttf", "Roboto Condensed"),
    "montserrat": ("Montserrat-Bold.ttf", "Montserrat"),
    "inter": ("Inter-Bold.ttf", "Inter"),
    "jakarta": ("PlusJakartaSans-Bold.ttf", "Plus Jakarta Sans"),
    "manrope": ("Manrope-Bold.ttf", "Manrope"),
    "space": ("SpaceGrotesk-Bold.ttf", "Space Grotesk"),
    "rubik": ("Rubik-Bold.ttf", "Rubik"),
    "exo": ("Exo2-Bold.ttf", "Exo 2"),
    "ubuntu": ("Ubuntu-Bold.ttf", "Ubuntu"),
    "fira": ("FiraSans-Bold.ttf", "Fira Sans"),
    "opensans": ("OpenSans-Bold.ttf", "Open Sans"),
    "playfair": ("PlayfairDisplay-Bold.ttf", "Playfair Display"),
    "serif": ("NotoSerif-Bold.ttf", "Noto Serif"),
    "pacifico": ("Pacifico-Regular.ttf", "Pacifico"),
    "creepster": ("Creepster-Regular.ttf", "Creepster"),
}
DEFAULT_FONT = "bebas"
MAX_TEXT = 80
SIZE = 220            # font size the logo is set at; logos are scaled by the renderer anyway
MAX_W = 1800          # wider than this and it wraps (or shrinks)


def available() -> list[dict]:
    return [{"id": k, "name": name} for k, (f, name) in FONTS.items() if os.path.exists(os.path.join(fonts.FONTS_DIR, f))]


def _hex(v: str | None, default: tuple) -> tuple:
    v = (v or "").strip().lstrip("#")
    if len(v) == 6:
        try:
            return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            pass
    return default


def _lines(text: str, font: ImageFont.FreeTypeFont) -> list[str]:
    """One line, or two balanced ones when one would be too wide."""
    if font.getlength(text) <= MAX_W or " " not in text:
        return [text]
    words = text.split()
    best = None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        w = max(font.getlength(a), font.getlength(b))
        if best is None or w < best[0]:
            best = (w, [a, b])
    return best[1]


def render(text: str, *, font: str = DEFAULT_FONT, color: str = "ffffff", upper: bool = False,
           outline: bool = False, shadow: bool = True, spacing: float = 0.0) -> bytes:
    """The text as a tight transparent PNG."""
    text = " ".join((text or "").split())[:MAX_TEXT]
    if not text:
        raise ValueError("Type the logo's text")
    if upper:
        text = text.upper()
    file = FONTS.get(font, FONTS[DEFAULT_FONT])[0]
    path = fonts.font_for_text(os.path.join(fonts.FONTS_DIR, file), text)   # non-Latin titles get a font that has them
    size = SIZE
    f = ImageFont.truetype(path, size)
    lines = _lines(text, f)
    while size > 60 and max(f.getlength(l) for l in lines) > MAX_W:
        size -= 10
        f = ImageFont.truetype(path, size)
    fill = _hex(color, (255, 255, 255))
    stroke = max(2, size // 22) if outline else 0
    track = spacing * size      # extra letter spacing, in font sizes

    def width(line: str) -> float:
        return f.getlength(line) + track * max(0, len(line) - 1)

    line_h = int(size * 1.08)
    pad = size // 2
    w = int(max(width(l) for l in lines)) + pad * 2 + stroke * 2
    h = line_h * len(lines) + pad * 2 + stroke * 2
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    y = pad
    for line in lines:
        x = (w - width(line)) / 2
        if track:
            for ch in line:
                d.text((x, y), ch, font=f, fill=fill + (255,), stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
                x += f.getlength(ch) + track
        else:
            d.text((x, y), line, font=f, fill=fill + (255,), stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
        y += line_h
    if shadow:
        sh = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        sh.putalpha(layer.getchannel("A").point(lambda a: a * 0.55))
        sh = sh.filter(ImageFilter.GaussianBlur(size / 18))
        base = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        base.alpha_composite(sh, (0, max(2, size // 40)))
        base.alpha_composite(layer)
        layer = base
    box = layer.getchannel("A").getbbox()
    if box:
        m = size // 12
        layer = layer.crop((max(0, box[0] - m), max(0, box[1] - m), min(w, box[2] + m), min(h, box[3] + m)))
    out = io.BytesIO()
    layer.save(out, format="PNG", optimize=True)
    return out.getvalue()


def options(body: dict) -> dict:
    """render() keyword arguments from a request (query or JSON)."""
    def flag(k, default):
        v = body.get(k)
        return default if v is None else str(v).lower() in ("1", "true", "yes", "on")
    try:
        spacing = max(0.0, min(0.5, float(body.get("spacing") or 0)))
    except ValueError:
        spacing = 0.0
    return {"font": str(body.get("font") or DEFAULT_FONT), "color": str(body.get("color") or "ffffff"),
            "upper": flag("upper", False), "outline": flag("outline", False), "shadow": flag("shadow", True),
            "spacing": spacing}
