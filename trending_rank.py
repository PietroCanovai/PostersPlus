# trending_rank.py
#
# The trending rank drawn as its own mark rather than as a sash label, chosen
# with trending_style:
#
#   number — a large silver-to-white numeral in the top corner, the way Apple TV
#            numbers its Top Shows row
#   ribbon — a dark bookmark ribbon hanging from the top edge, the rank on it
#
# Either one frees the sash for the next label in the user's priority list, so
# a title can read "#3" and "New Season" at once.  Both are laid out as ratios
# of the canvas width, so every poster width draws the same mark.
import os
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

_FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "Inter-Bold.ttf")

STYLES = ("sash", "number", "ribbon")
# What the ribbon's label says for each kind of title, as sashLabels keys.
KIND_LABELS = {"movie": "Film", "series": "Series", "anime": "Anime"}

# Drawn at SS× on a layer just big enough for the mark, then box-reduced:
# anti-aliased edges for the ribbon's point and the numeral's gradient.
_SS = 4

# Number: the digits' ink height, and its inset from the top and side, as
# fractions of the poster width.
_NUM_H      = 0.19
_NUM_INSET  = 0.065
# Silver at the foot of the numeral, white at its head.
_NUM_TOP    = (255, 255, 255)
_NUM_BOTTOM = (168, 172, 180)

# Ribbon: its width for one or two digits, its inset from the side (clear of
# the rounded corner most apps clip posters with), and the body's height and
# the depth of the notch cut into its foot, as fractions of its width.
_RIB_W      = 0.13
_RIB_INSET  = 0.06
_RIB_BODY   = 1.55
_RIB_NOTCH  = 0.26
_RIB_DIGIT  = 0.46   # digit ink height, of the ribbon width
# With a label under the rank: the extra body it takes, and its capitals'
# ink height, both of the ribbon width.
_RIB_LABEL_BAND = 0.36
_RIB_LABEL_CAP  = 0.13


@lru_cache(maxsize=16)
def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_FONT_PATH, size)


def _digit_font(ink_h: float) -> tuple[ImageFont.FreeTypeFont, int]:
    """Inter at the size whose digits are *ink_h* tall, and that size."""
    probe = _font(200)
    t = probe.getbbox("0123456789", anchor="ls")
    size = max(8, round(200 * ink_h / (t[3] - t[1])))
    return _font(size), size


def _shadow(layer: Image.Image, blur: float, alpha: int) -> Image.Image:
    """A black copy of *layer*'s alpha, blurred, for a soft drop shadow."""
    a = layer.getchannel("A").point(lambda v: v * alpha // 255)
    shadow = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    shadow.putalpha(a)
    return shadow.filter(ImageFilter.GaussianBlur(blur))


def number_box(width: int, scale: float = 1.0) -> tuple[int, int]:
    """(inset, bottom) of the numeral's band, in pixels of a *width*-wide
    poster: where to look for room beside it."""
    return round(_NUM_INSET * width), round((_NUM_INSET + _NUM_H * scale) * width)


def draw_rank_number(image: Image.Image, rank: int, right: bool = False,
                     max_w: float | None = None, scale: float = 1.0) -> Image.Image:
    """The rank as a large silver numeral in the top-left (or top-right) corner.

    *scale* sizes it against its default.  *max_w* caps its width in pixels,
    for when a notch sits beside it: the digits shrink to fit, down to 60 % of
    their size and no further.
    """
    w = image.width
    text = str(rank)
    ink_h = _NUM_H * scale * w
    if max_w is not None:
        font, _ = _digit_font(ink_h)
        natural = font.getlength(text) * 0.97
        if natural > max_w:
            ink_h *= max(0.6, max_w / natural)
    font, _ = _digit_font(ink_h * _SS)
    # Tighten the digits a touch: display numerals at this size read loose.
    track = -round(font.size * 0.03)
    glyphs = [(ch, font.getbbox(ch, anchor="ls")) for ch in text]
    adv = [font.getlength(ch) for ch in text]
    x0 = min(0, glyphs[0][1][0])
    ink_w = sum(adv[:-1]) + track * (len(text) - 1) + glyphs[-1][1][2] - x0
    top = min(b[1] for _, b in glyphs)
    ink_h = -top
    pad = round(0.04 * w * _SS)          # room for the shadow's blur

    lw, lh = round(ink_w) + 2 * pad, round(ink_h) + 2 * pad
    mask = Image.new("L", (lw, lh), 0)
    md = ImageDraw.Draw(mask)
    x = pad - x0
    for i, ch in enumerate(text):
        md.text((x, pad + ink_h), ch, font=font, fill=255, anchor="ls")
        x += adv[i] + track

    # Vertical silver-to-white gradient over the ink, brightest at the head.
    grad = Image.new("RGBA", (1, lh))
    for y in range(lh):
        t = min(1.0, max(0.0, (y - pad) / max(1, ink_h)))
        t = t ** 1.4
        grad.putpixel((0, y), tuple(round(_NUM_TOP[i] * (1 - t) + _NUM_BOTTOM[i] * t) for i in range(3)) + (255,))
    numeral = grad.resize((lw, lh))
    numeral.putalpha(mask)
    numeral = numeral.reduce(_SS)

    shadow = _shadow(numeral, 0.012 * w, 150)
    inset = round(_NUM_INSET * w)
    pad1 = pad / _SS
    nx = round(w - inset - numeral.width + pad1) if right else round(inset - pad1)
    ny = round(inset - pad1)
    off = max(1, round(0.004 * w))

    result = image.convert("RGBA") if image.mode != "RGBA" else image.copy()
    _paste(result, shadow, nx, ny + off)
    _paste(result, numeral, nx, ny)
    return result.convert(image.mode) if image.mode != "RGBA" else result


def _spaced(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str,
            font: ImageFont.FreeTypeFont, track: float, fill) -> None:
    """*text* from its left baseline at *xy*, *track* px between letters."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill, anchor="ls")
        x += font.getlength(ch) + track


def _spaced_width(text: str, font: ImageFont.FreeTypeFont, track: float) -> float:
    return sum(font.getlength(ch) for ch in text) + track * max(0, len(text) - 1)


def draw_rank_ribbon(image: Image.Image, rank: int, right: bool = False,
                     label: str | None = None, scale: float = 1.0,
                     corner: bool = False) -> Image.Image:
    """The rank on a dark ribbon hanging from the top edge, its foot cut into
    a notch: just in from the top-left (or top-right) corner, or with
    *corner*, nested right into it.

    *label* ("FILM", "SERIES", ...) goes in small letter-spaced capitals under
    the rank, the ribbon growing to hold it.  *scale* sizes the whole ribbon
    against its default.
    """
    w = image.width
    text = str(rank)
    widen = 1 + 0.28 * max(0, len(text) - 2)
    rib_w = _RIB_W * scale * w * widen
    body_h = rib_w * (_RIB_BODY + (_RIB_LABEL_BAND if label else 0))
    notch = rib_w * _RIB_NOTCH
    pad = round(0.03 * w)                # room for the shadow's blur

    S = _SS
    lw, lh = round(rib_w + 2 * pad), round(body_h + pad)
    layer = Image.new("RGBA", (lw * S, lh * S), (0, 0, 0, 0))
    # Top edge flush with the poster's (layer row 0), so it hangs from it.
    x0, x1 = pad * S, (pad + rib_w) * S
    yb, yn = body_h * S, (body_h - notch) * S
    outline = [(x0, 0), (x1, 0), (x1, yb), ((x0 + x1) / 2, yn), (x0, yb)]

    mask = Image.new("L", layer.size, 0)
    ImageDraw.Draw(mask).polygon(outline, fill=255)
    # Charcoal body, a shade lighter at the top, like the notch's dark styles.
    grad = Image.new("RGBA", (1, layer.height))
    for y in range(layer.height):
        t = min(1.0, y / max(1, yb))
        c = round(46 * (1 - t) + 20 * t)
        grad.putpixel((0, y), (c, c, c + 2, 235))
    body = grad.resize(layer.size)
    body.putalpha(Image.eval(mask, lambda v: v * 235 // 255))
    layer.alpha_composite(body)

    # A hairline rim down the sides and round the notch; none on top, where it
    # meets the poster edge, nor down a side nested against the poster's.
    rim_w = max(S, round(0.0035 * w * S))
    rim = outline[1:] + outline[:1]
    if corner:
        rim = outline[2:] + outline[:1] if right else outline[1:]
    ImageDraw.Draw(layer).line(rim, fill=(255, 255, 255, 46), width=rim_w, joint="curve")
    # The rim is centred on the outline; keep only its inner half.
    layer.putalpha(ImageChops.darker(layer.getchannel("A"), mask))

    draw = ImageDraw.Draw(layer)
    cx_mid = (x0 + x1) / 2
    num_bottom = yn
    if label:
        # Capitals whose ink is _RIB_LABEL_CAP of the width, narrowed to fit
        # a long word ("PELÍCULA", "MFULULIZO") inside the ribbon's sides.
        cap_h = _RIB_LABEL_CAP * rib_w * S / widen ** 0.5
        probe = _font(200)
        hb = probe.getbbox("H", anchor="ls")
        lfont = _font(max(6, round(200 * cap_h / (hb[3] - hb[1]))))
        track = lfont.size * 0.08
        fit = 0.80 * rib_w * S
        span = _spaced_width(label, lfont, track)
        if span > fit:
            lfont = _font(max(6, round(lfont.size * fit / span)))
            track = lfont.size * 0.08
            span = _spaced_width(label, lfont, track)
        lcap = -lfont.getbbox("H", anchor="ls")[1]
        band = _RIB_LABEL_BAND * rib_w * S
        base = yn - (band - lcap) * 0.55
        _spaced(draw, (cx_mid - span / 2, base), label, lfont, track, (255, 255, 255, 200))
        num_bottom = yn - band

    font, _ = _digit_font(_RIB_DIGIT * rib_w * S / widen ** 0.5)
    ink = font.getbbox(text, anchor="ls")
    fit = 0.74 * rib_w * S
    if ink[2] - ink[0] > fit:
        font = _font(max(6, round(font.size * fit / (ink[2] - ink[0]))))
        ink = font.getbbox(text, anchor="ls")
    cx = cx_mid - (ink[0] + ink[2]) / 2
    cy = num_bottom / 2 - (ink[1] + ink[3]) / 2 + (0.06 * rib_w * S if label else 0)
    draw.text((cx, cy), text, font=font, fill=(255, 255, 255, 255), anchor="ls")

    ribbon = layer.reduce(S)
    shadow = _shadow(ribbon, 0.012 * w, 140)
    inset = 0 if corner else _RIB_INSET * w
    rx = round(w - inset - rib_w - pad) if right else round(inset - pad)

    result = image.convert("RGBA") if image.mode != "RGBA" else image.copy()
    _paste(result, shadow, rx + max(1, round(0.004 * w)), max(1, round(0.004 * w)))
    _paste(result, ribbon, rx, 0)
    return result.convert(image.mode) if image.mode != "RGBA" else result


def _paste(dst: Image.Image, src: Image.Image, x: int, y: int) -> None:
    """alpha_composite *src* at (x, y), clipped to *dst* (which it rejects when
    the offset is negative or the layer runs off the edge)."""
    sx0, sy0 = max(0, -x), max(0, -y)
    sx1, sy1 = min(src.width, dst.width - x), min(src.height, dst.height - y)
    if sx1 > sx0 and sy1 > sy0:
        dst.alpha_composite(src, (x + sx0, y + sy0), (sx0, sy0, sx1, sy1))
