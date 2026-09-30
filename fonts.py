# fonts.py
"""The font poster labels are drawn in, and fallback by script.

Labels — the genre / rating line, the sash and notch, the Bar, the landscape
info line and badge, the trending ribbon's caption — are drawn in the *label
font*.  Inter is the default; Rubik is the one that has Hebrew; the rest are
Google Fonts families with Inter's ★ added (tools/build_label_fonts.py), and an
operator can upload more (custom_fonts, keys "custom-…").  A choice that has
no glyphs for the poster's language falls back to the first shipped label font
that does, so a Hebrew poster is always drawn in Rubik and a Greek or
Vietnamese one in Inter, whichever the user picked.

The label font is set around a render by build_poster / build_landscape
(``label_font_scope``), a ContextVar for the same reason pxscale's scale is
one: renders run on thread-pool threads, and each sees only its own.  Outside a
render it is Inter.

Numerals and codes that are never translated (quality and age badges, the
trending numeral, rating badge logos) stay in Inter.
"""
import os
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache

from PIL import ImageFont

import custom_fonts
import i18n

FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")

# label_font choice → file, as shipped.  Order is the fallback order when the
# chosen font can't draw a language; an operator's own fonts are never a
# fallback.
LABEL_FONTS: dict[str, str] = {
    "inter": "Inter-Bold.ttf",
    "rubik": "Rubik-Bold.ttf",
    "jakarta": "PlusJakartaSans-Bold.ttf",
    "manrope": "Manrope-Bold.ttf",
    "montserrat": "Montserrat-Bold.ttf",
    "robotocondensed": "RobotoCondensed-Bold.ttf",
    "barlowcondensed": "BarlowCondensed-Bold.ttf",
    "oswald": "OswaldLabel-Bold.ttf",
    "spacegrotesk": "SpaceGrotesk-Bold.ttf",
    "exo2": "Exo2-Bold.ttf",
    "fira": "FiraSans-Bold.ttf",
    "opensans": "OpenSans-Bold.ttf",
}
DEFAULT_LABEL_FONT = "inter"

_LABEL_PATH: ContextVar[str] = ContextVar(
    "label_font_path", default=os.path.join(FONTS_DIR, LABEL_FONTS[DEFAULT_LABEL_FONT]))


def _path(choice: str) -> str:
    return os.path.join(FONTS_DIR, LABEL_FONTS[choice])


@lru_cache(maxsize=8)
def _probe_font(path: str) -> tuple[ImageFont.FreeTypeFont, bytes]:
    font = ImageFont.truetype(path, 40)
    return font, bytes(font.getmask("\U0010FFFD"))   # .notdef, a missing glyph


@lru_cache(maxsize=4096)
def _has_glyph(path: str, ch: str) -> bool:
    font, notdef = _probe_font(path)
    return bytes(font.getmask(ch)) != notdef


def covers(path: str, text: str) -> bool:
    """Whether the font at *path* has a glyph for every character of *text*
    (ASCII is taken as read: every font here has it)."""
    return all(_has_glyph(path, ch) for ch in set(text) if ord(ch) > 127)


def is_label_font(choice: str) -> bool:
    """Whether *choice* names a label font: a shipped one or one the
    operator uploaded."""
    return choice in LABEL_FONTS or (choice.startswith(custom_fonts.KEY_PREFIX)
                                      and choice in custom_fonts.paths())


def resolve_label_font(choice: str | None, lang: str | None) -> str:
    """Path of the label font for *choice* on a poster in *lang*: the chosen
    font if it has the language's labels, else the first label font that
    does, else the chosen one anyway."""
    path = None
    if choice in LABEL_FONTS:
        path = _path(choice)
    elif choice and choice.startswith(custom_fonts.KEY_PREFIX):
        path = custom_fonts.paths().get(choice)
        # Deleted or replaced on another worker a moment ago: its file is
        # gone before this worker has read the new index.
        if path and not os.path.isfile(path):
            path = None
    # Keyed by the labels themselves, not the code, so nothing resolved
    # before the language files load (or since they changed) sticks.
    return _resolve(path or _path(DEFAULT_LABEL_FONT), i18n.language_text(lang))


@lru_cache(maxsize=64)
def _resolve(path: str, text: str) -> str:
    if covers(path, text):
        return path
    for key in LABEL_FONTS:
        if covers(_path(key), text):
            return _path(key)
    return path


def font_for_text(path: str, text: str) -> str:
    """*path* if its font can draw *text*, else the first label font that can
    — for text from outside the language files, such as a fallback title in
    the poster's language."""
    if covers(path, text):
        return path
    for name in LABEL_FONTS.values():
        alt = os.path.join(FONTS_DIR, name)
        if covers(alt, text):
            return alt
    return path


@contextmanager
def label_font_scope(choice: str | None, lang: str | None):
    """Draw labels in *choice* (a LABEL_FONTS key) for the duration, or the
    font that can draw *lang* if *choice* can't."""
    token = _LABEL_PATH.set(resolve_label_font(choice, lang))
    try:
        yield
    finally:
        _LABEL_PATH.reset(token)


def label_path() -> str:
    """The current render's label font file."""
    return _LABEL_PATH.get()


@lru_cache(maxsize=256)
def truetype(path: str, size: float) -> ImageFont.FreeTypeFont:
    """ImageFont.truetype, kept: a font is immutable once built."""
    return ImageFont.truetype(path, size)


def label_font(size: float) -> ImageFont.FreeTypeFont:
    """The current render's label font at *size*."""
    return truetype(label_path(), size)
