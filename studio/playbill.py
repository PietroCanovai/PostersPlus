"""A Playbill cover: the yellow PLAYBILL header with the theatre under it, the
show's art below, a thin black frame.

The header is laid out from real covers (measured on the 2:3 covers of
Aladdin and & Juliet, the lettering on Cabaret's, 2026-10-04).  Every size is a
share of the cover's width:

  header      0.3174 high, yellow #FEE300
  PLAYBILL    letters 0.1165 high, their tops 0.0547 down, each letter in the
              box it has on the real cover (LETTERS)
  theatre     capitals 0.0247 high, their tops 0.2280 down, centred

The wordmark is hand lettering in a heavy "Latin" face with wedge serifs.
Goblin One is the closest open-licensed typeface (scored letter by letter
against the cover, 0.80 overlap; the next best had 0.74), and each of its
letters is fitted to the real letter's box, so the spacing is the cover's.
The theatre line is Pragati Narrow: a narrow gothic like the covers', with
their wide gaps between words.

Pure PIL, no network: the caller hands over the decoded art.
"""
from __future__ import annotations

import os
import re

from PIL import Image, ImageDraw, ImageFont

FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
WORD_FONT, VENUE_FONT = "GoblinOne.ttf", "PragatiNarrow-Regular.ttf"
YELLOW = (254, 227, 0)
INK = (0, 0, 0)
SIZE = (1000, 1500)       # what the generator makes: Jellyfin's poster shape, at Studio's usual size
HEADER = 0.3174
FRAME = 0.003
WORD_TOP, WORD_H = 0.0547, 0.1165
# (letter, left edge, width), as on the cover.
LETTERS = (("P", 0.0478, 0.1017), ("L", 0.1649, 0.0955), ("A", 0.2743, 0.1109), ("Y", 0.3883, 0.1156),
           ("B", 0.5116, 0.1032), ("I", 0.6410, 0.0616), ("L", 0.7304, 0.0971), ("L", 0.8475, 0.0963))
VENUE_TOP, VENUE_CAP = 0.2280, 0.0247
VENUE_TRACK, VENUE_SPACE, VENUE_SPACE_MIN, VENUE_MAX_W = 0.02, 0.62, 0.20, 0.75   # in em, and of the width


def header_height(width: int) -> int:
    return round(width * HEADER)


def art_aspect(size: tuple[int, int] = SIZE) -> float:
    """Width over height of the space under the header: what an image is framed to."""
    return size[0] / (size[1] - header_height(size[0]))


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(os.path.join(FONTS_DIR, name), size)


def _glyph(ch: str, font: ImageFont.FreeTypeFont, width: int, height: int) -> Image.Image:
    """One letter's ink as a mask, fitted to exactly width x height."""
    left, top, right, bottom = font.getbbox(ch)
    layer = Image.new("L", (right - left, bottom - top), 0)
    ImageDraw.Draw(layer).text((-left, -top), ch, font=font, fill=255)
    if ch == "I":
        layer = _both_sides(layer)
    return layer.resize((width, height), Image.Resampling.LANCZOS)


def _both_sides(glyph: Image.Image) -> Image.Image:
    """The cover's I has serifs on both sides, top and bottom; the font's has
    them on one side each.  Mirror the letter about its stem and keep both."""
    from PIL import ImageChops, ImageOps
    w, h = glyph.size
    mid = glyph.crop((0, h * 2 // 5, w, h * 3 // 5)).resize((w, 1), Image.Resampling.BOX)
    cols = [x for x in range(w) if mid.getpixel((x, 0)) > 127]
    if not cols:
        return glyph
    axis = (cols[0] + cols[-1] + 1) / 2                 # the stem's centre line
    half = round(max(axis, w - axis))
    wide = Image.new("L", (half * 2, h), 0)
    wide.paste(glyph, (round(half - axis), 0))
    both = ImageChops.lighter(wide, ImageOps.mirror(wide))
    return both.crop(both.getbbox())


def venue_from_name(item_name: str) -> str:
    """"Hadestown - Broadway, 09-02-2024 - x" → "BROADWAY"; "... - West End Concert (Palladium Theatre), ..." → "PALLADIUM THEATRE"."""
    parts = re.split(r"\s+-\s+", item_name or "")
    if len(parts) < 2:
        return ""
    where = parts[1].split(",")[0].strip()
    paren = re.search(r"\(([^)]+)\)", where)
    return (paren.group(1) if paren else where).upper()


def pick_venue(recordings: list[dict]) -> str:
    """The theatre a cover should name, from a show's recordings
    ([{production, venue, date}]): its Broadway house, else its West End one,
    else wherever it played.  The original run comes before revivals and
    transfers ("Broadway" before "Broadway Revival"), then the earliest date.
    Off-Broadway and Off-West End count as "wherever"."""
    def rank(r: dict) -> tuple:
        p = (r.get("production") or "").strip().lower()
        main = 0 if "broadway" in p and "off" not in p else 1 if "west end" in p and "off" not in p else 2
        return main, p not in ("broadway", "west end"), r.get("date") or "9999"
    known = [r for r in recordings if (r.get("venue") or "").strip()]
    return min(known, key=rank)["venue"].strip() if known else ""


def venues(recordings: list[dict]) -> list[str]:
    """Every theatre the show's recordings name, the cover's pick first."""
    first = pick_venue(recordings)
    rest = sorted({(r.get("venue") or "").strip() for r in recordings} - {"", first})
    return ([first] if first else []) + rest


def _venue_line(draw: ImageDraw.ImageDraw, width: int, text: str) -> None:
    """The theatre, in capitals: wide gaps between words as on the covers,
    narrower when the name is long, and smaller only when even that won't fit."""
    words = text.upper().split()
    if not words:
        return
    probe = _font(VENUE_FONT, 1000)
    cap_em = (probe.getbbox("H")[3] - probe.getbbox("H")[1]) / 1000
    size = width * VENUE_CAP / cap_em
    while True:
        font = _font(VENUE_FONT, max(8, round(size)))
        em = font.size
        track = VENUE_TRACK * em
        word_w = [sum(font.getlength(ch) for ch in w) + track * (len(w) - 1) for w in words]
        gaps = len(words) - 1
        room = width * VENUE_MAX_W - sum(word_w)
        space = min(VENUE_SPACE * em, room / gaps) if gaps else 0
        if not gaps and room >= 0 or gaps and space >= VENUE_SPACE_MIN * em or em <= 8:
            break
        size *= 0.96
    space = max(space, VENUE_SPACE_MIN * em) if gaps else 0
    total = sum(word_w) + space * gaps
    x = (width - total) / 2
    y = round(width * VENUE_TOP) - font.getbbox("H")[1]
    for w in words:
        for ch in w:
            draw.text((x, y), ch, font=font, fill=INK)
            x += font.getlength(ch) + track
        x += space - track


def header(width: int, venue: str = "") -> Image.Image:
    """The yellow header, *width* wide."""
    out = Image.new("RGB", (width, header_height(width)), YELLOW)
    draw = ImageDraw.Draw(out)
    top, h = round(width * WORD_TOP), round(width * WORD_H)
    font = _font(WORD_FONT, h * 3)        # drawn large, then fitted: clean edges at any size
    black = Image.new("RGB", (width, h), INK)
    for ch, left, w in LETTERS:
        mask = _glyph(ch, font, round(width * w), h)
        out.paste(black.crop((0, 0, mask.width, h)), (round(width * left), top), mask)
    reg = _font(VENUE_FONT, max(8, round(width * 0.017)))
    draw.text((round(width * 0.9465), top - round(width * 0.003)), "®", font=reg, fill=INK)
    _venue_line(draw, width, venue)
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


def compose(art: Image.Image, *, size: tuple[int, int] = SIZE, venue: str = "", logo: Image.Image | None = None,
            crop: tuple[float, float, float] = (0.5, 0.5, 1.0)) -> Image.Image:
    """The cover: *art* framed (x, y, zoom) into the space under the header."""
    W, H = size
    head = header(W, venue)
    canvas = Image.new("RGB", (W, H), INK)
    canvas.paste(_cover(art.convert("RGB"), W, H - head.height, *crop), (0, head.height))
    canvas.paste(head, (0, 0))
    # A logo (the show's own title art), low on the poster: the run-time design only.
    if logo is not None:
        lg = logo.convert("RGBA")
        lg.thumbnail((round(W * 0.78), round(H * 0.2)), Image.Resampling.LANCZOS)
        canvas.paste(lg, ((W - lg.width) // 2, H - lg.height - round(H * 0.06)), lg)
    ImageDraw.Draw(canvas).rectangle((0, 0, W - 1, H - 1), outline=INK, width=max(2, round(W * FRAME)))
    return canvas
