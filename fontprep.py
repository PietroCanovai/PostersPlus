"""Make a font ready to draw labels in, with fontTools: a CFF (.otf) font's
outlines turned into TrueType ones, heavy hinting taken out, and the ★ the
labels draw added from Inter Bold when the font has none.

Used by tools/build_label_fonts.py for the shipped label fonts and by
custom_fonts for the ones an operator uploads.

The labels draw "★ 87" in the same font as the words, and almost no font has
U+2605 (of ~85 Google Fonts families checked, only Inter, Plus Jakarta Sans
and M PLUS 1p do).  Inter's is scaled so it stands as tall against the font's
capitals as it does against Inter's, and given Inter's advance in proportion.
"""
from __future__ import annotations

import os

from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont, newTable

STAR = 0x2605
INTER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "Inter-Bold.ttf")

# Tables that only hinting reads.
_HINTING_TABLES = ("fpgm", "prep", "cvt ", "hdmx", "VDMX", "LTSH", "gasp")


def cff_to_glyf(font: TTFont) -> None:
    """Turn a CFF-flavoured OpenType font's cubic outlines into TrueType's
    quadratic ones, in place, so it is built the same way as every other
    label font (and add_star has one outline format to write)."""
    order = font.getGlyphOrder()
    glyph_set = font.getGlyphSet()
    glyphs = {}
    for name in order:
        pen = TTGlyphPen(glyph_set)
        # CFF contours run the other way round from TrueType's.
        glyph_set[name].draw(Cu2QuPen(pen, 1.0, reverse_direction=True))
        glyphs[name] = pen.glyph()
    font["loca"] = newTable("loca")
    glyf = font["glyf"] = newTable("glyf")
    glyf.glyphOrder = order
    glyf.glyphs = glyphs
    del font["CFF "]
    if "VORG" in font:
        del font["VORG"]
    glyf.compile(font)
    hmtx = font["hmtx"]
    for name, glyph in glyphs.items():
        if hasattr(glyph, "xMin"):
            hmtx[name] = (hmtx[name][0], glyph.xMin)
    maxp = font["maxp"] = newTable("maxp")
    maxp.tableVersion = 0x00010000
    maxp.maxZones = 1
    for field in ("maxTwilightPoints", "maxStorage", "maxFunctionDefs", "maxInstructionDefs",
                  "maxStackElements", "maxSizeOfInstructions", "maxComponentElements"):
        setattr(maxp, field, 0)
    maxp.compile(font)
    post = font["post"]
    post.formatType = 2.0
    post.extraNames = []
    post.mapping = {}
    post.glyphOrder = order
    try:
        post.compile(font)
    except OverflowError:
        post.formatType = 3.0   # glyph names that don't fit are only names
    font.sfntVersion = "\x00\x01\x00\x00"


def strip_heavy_hinting(font: TTFont) -> bool:
    """Take the hinting out of a font whose glyphs carry their own hinting
    programs; True when it did.  FreeType runs those programs for every glyph
    drawn, which made Fira Sans and Barlow Condensed ~9x slower to draw than
    the other label fonts, for no visible gain at poster sizes.  A font with
    only a light prep/gasp setup is left alone: taking that out made Inter
    and Oswald slower, not faster."""
    if "glyf" not in font or "fpgm" not in font:
        return False
    glyf = font["glyf"]
    if not any(getattr(glyf[name], "program", None) for name in glyf.keys()):
        return False
    for tag in _HINTING_TABLES:
        if tag in font:
            del font[tag]
    for name in glyf.keys():
        glyf[name].removeHinting()
    return True


def add_star(font: TTFont, inter: TTFont | None = None) -> float | None:
    """Copy Inter Bold's ★ into *font* (TrueType outlines), in place; the
    scale used, or None when the font has a ★ of its own."""
    if STAR in font.getBestCmap():
        return None
    inter = inter or TTFont(INTER_PATH)
    src_name = inter.getBestCmap()[STAR]
    k = _cap_height(font) / _cap_height(inter)
    pen = TTGlyphPen(None)
    inter.getGlyphSet()[src_name].draw(TransformPen(pen, (k, 0, 0, k, 0, 0)))
    glyph = pen.glyph()

    name = "uni2605"
    while name in font.getGlyphOrder():
        name += ".star"
    glyf = font["glyf"]
    glyf[name] = glyph   # appends to the glyph order; maxp recounts on save
    glyph.recalcBounds(glyf)
    adv, _lsb = inter["hmtx"][src_name]
    font["hmtx"][name] = (round(adv * k), glyph.xMin)
    # Per-glyph tables add_star doesn't fill in fail to save without the new
    # glyph: vertical metrics get a plain full-height box; the device-metric
    # caches are optional and simply dropped.
    if "vmtx" in font:
        font["vmtx"][name] = (font["head"].unitsPerEm, 0)
    for tag in ("hdmx", "LTSH", "VDMX"):
        if tag in font:
            del font[tag]
    for table in font["cmap"].tables:
        # Format 14 is variation sequences, not a character map.
        if table.isUnicode() and table.format in (4, 6, 10, 12, 13):
            table.cmap[STAR] = name
    return k


def _cap_height(font: TTFont) -> float:
    """The height of a capital, in font units: OS/2's figure when the font
    has one (version 2 on), else the height of its H."""
    cap = getattr(font["OS/2"], "sCapHeight", 0) if "OS/2" in font else 0
    if cap > 0:
        return cap
    h = font.getBestCmap().get(ord("H"))
    if h and "glyf" in font:
        glyph = font["glyf"][h]
        glyph.recalcBounds(font["glyf"])
        if getattr(glyph, "yMax", 0) > 0:
            return glyph.yMax
    return 0.7 * font["head"].unitsPerEm
