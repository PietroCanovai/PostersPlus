"""The Playbill design for theatre titles: a yellow PLAYBILL header with the
venue under it, the show's art below, a thin black frame.

Pure PIL, no network: the caller hands over the decoded art and an optional
logo.  Fonts come from PostersPlus's own fonts/ folder.
"""
from __future__ import annotations

import os
import re

from PIL import Image, ImageDraw, ImageFont

FONTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts")
YELLOW = (255, 229, 0)
INK = (24, 22, 22)
HEADER = 0.225      # share of the height
FRAME = 0.004       # share of the width


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(os.path.join(FONTS_DIR, name), size)
    except OSError:
        return ImageFont.load_default()


def venue_from_name(item_name: str) -> str:
    """"Hadestown - Broadway, 09-02-2024 - x" → "BROADWAY"; "... - West End Concert (Palladium Theatre), ..." → "PALLADIUM THEATRE"."""
    parts = re.split(r"\s+-\s+", item_name or "")
    if len(parts) < 2:
        return ""
    where = parts[1].split(",")[0].strip()
    paren = re.search(r"\(([^)]+)\)", where)
    return (paren.group(1) if paren else where).upper()


def _stretched_text(text: str, font: ImageFont.FreeTypeFont, box_w: int, box_h: int) -> Image.Image:
    """*text* drawn black on transparent, then scaled to fill box_w x box_h
    (the Playbill wordmark is a wide, stretched slab)."""
    left, top, right, bottom = font.getbbox(text)
    w, h = right - left, bottom - top
    layer = Image.new("L", (w + 4, h + 4), 0)
    ImageDraw.Draw(layer).text((2 - left, 2 - top), text, font=font, fill=255)
    layer = layer.resize((box_w, box_h), Image.Resampling.LANCZOS)
    out = Image.new("RGBA", (box_w, box_h), (*INK, 0))
    out.putalpha(layer)
    return out


def _cover(art: Image.Image, width: int, height: int, x: float, y: float, zoom: float) -> Image.Image:
    aspect = width / height
    base_w = min(art.width, art.height * aspect)
    cw = base_w / max(1.0, zoom)
    ch = cw / aspect
    left = (art.width - cw) * x
    top = (art.height - ch) * y
    return art.crop((round(left), round(top), round(left + cw), round(top + ch))).resize(
        (width, height), Image.Resampling.LANCZOS)


def compose(art: Image.Image, *, size: tuple[int, int], venue: str = "", logo: Image.Image | None = None,
            crop: tuple[float, float, float] = (0.5, 0.5, 1.0)) -> Image.Image:
    W, H = size
    header_h = round(H * HEADER)
    frame = max(2, round(W * FRAME))
    canvas = Image.new("RGB", (W, H), INK)
    # The show's art fills everything under the header.
    canvas.paste(_cover(art.convert("RGB"), W, H - header_h, *crop), (0, header_h))
    # Header.
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, W, header_h), fill=YELLOW)
    word = _stretched_text("PLAYBILL", _font("NotoSerif-Bold.ttf", 200), round(W * 0.84), round(H * 0.085))
    wx, wy = (W - word.width) // 2, round(H * 0.052)
    canvas.paste(word, (wx, wy), word)
    reg = _font("Inter-Bold.ttf", max(10, round(H * 0.012)))
    draw.text((wx + word.width + round(W * 0.006), wy), "®", font=reg, fill=INK)
    if venue:
        size_v = round(H * 0.022)
        vfont = _font("Montserrat-Bold.ttf", size_v)
        spaced = " ".join(venue.upper())   # letter-spaced, as Playbill sets it
        spaced = spaced.replace("   ", "  ")
        while vfont.getlength(spaced) > W * 0.86 and size_v > 10:
            size_v -= 1
            vfont = _font("Montserrat-Bold.ttf", size_v)
        vw = vfont.getlength(spaced)
        draw.text(((W - vw) / 2, round(H * 0.165)), spaced, font=vfont, fill=INK)
    # A logo (the show's own title art), low on the poster.
    if logo is not None:
        lg = logo.convert("RGBA")
        lg.thumbnail((round(W * 0.78), round(H * 0.2)), Image.Resampling.LANCZOS)
        canvas.paste(lg, ((W - lg.width) // 2, H - lg.height - round(H * 0.06)), lg)
    # Thin black frame.
    draw.rectangle((0, 0, W - 1, H - 1), outline=INK, width=frame)
    return canvas
