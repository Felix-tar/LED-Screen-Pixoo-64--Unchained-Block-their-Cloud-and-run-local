"""A self-contained 3x5 pixel font.

Hand-authored here (public-domain, no third-party font shipped) so the project
never depends on a system font. Each glyph is 3 wide x 5 tall; a 1px gap is
inserted between glyphs. ``scale`` multiplies both glyph and gap uniformly.

Only uppercase letters, digits and the symbols we actually render are defined;
lowercase input is upper-cased, unknown glyphs render as blank.
"""
from __future__ import annotations

from PIL import Image, ImageDraw

GLYPH_W = 3
GLYPH_H = 5
GAP = 1

# fmt: off
_FONT: dict[str, list[str]] = {
    " ": ["...", "...", "...", "...", "..."],
    "0": ["###", "#.#", "#.#", "#.#", "###"],
    "1": [".#.", "##.", ".#.", ".#.", "###"],
    "2": ["###", "..#", "###", "#..", "###"],
    "3": ["###", "..#", ".##", "..#", "###"],
    "4": ["#.#", "#.#", "###", "..#", "..#"],
    "5": ["###", "#..", "###", "..#", "###"],
    "6": ["###", "#..", "###", "#.#", "###"],
    "7": ["###", "..#", "..#", "..#", "..#"],
    "8": ["###", "#.#", "###", "#.#", "###"],
    "9": ["###", "#.#", "###", "..#", "###"],
    "A": ["###", "#.#", "###", "#.#", "#.#"],
    "B": ["##.", "#.#", "##.", "#.#", "##."],
    "C": ["###", "#..", "#..", "#..", "###"],
    "D": ["##.", "#.#", "#.#", "#.#", "##."],
    "E": ["###", "#..", "##.", "#..", "###"],
    "F": ["###", "#..", "##.", "#..", "#.."],
    "G": ["###", "#..", "#.#", "#.#", ".##"],
    "H": ["#.#", "#.#", "###", "#.#", "#.#"],
    "I": ["###", ".#.", ".#.", ".#.", "###"],
    "J": ["..#", "..#", "..#", "#.#", "###"],
    "K": ["#.#", "#.#", "##.", "#.#", "#.#"],
    "L": ["#..", "#..", "#..", "#..", "###"],
    "M": ["#.#", "###", "###", "#.#", "#.#"],
    "N": ["#.#", "###", "#.#", "#.#", "#.#"],
    "O": ["###", "#.#", "#.#", "#.#", "###"],
    "P": ["###", "#.#", "###", "#..", "#.."],
    "Q": ["###", "#.#", "#.#", "###", "..#"],
    "R": ["###", "#.#", "##.", "#.#", "#.#"],
    "S": ["###", "#..", "###", "..#", "###"],
    "T": ["###", ".#.", ".#.", ".#.", ".#."],
    "U": ["#.#", "#.#", "#.#", "#.#", "###"],
    "V": ["#.#", "#.#", "#.#", "#.#", ".#."],
    "W": ["#.#", "#.#", "###", "###", "#.#"],
    "X": ["#.#", "#.#", ".#.", "#.#", "#.#"],
    "Y": ["#.#", "#.#", ".#.", ".#.", ".#."],
    "Z": ["###", "..#", ".#.", "#..", "###"],
    ".": ["...", "...", "...", "...", ".#."],
    ",": ["...", "...", "...", ".#.", "#.."],
    ":": ["...", ".#.", "...", ".#.", "..."],
    "-": ["...", "...", "###", "...", "..."],
    "+": ["...", ".#.", "###", ".#.", "..."],
    "/": ["..#", "..#", ".#.", "#..", "#.."],
    "%": ["#.#", "..#", ".#.", "#..", "#.#"],
    "!": [".#.", ".#.", ".#.", "...", ".#."],
    "'": [".#.", ".#.", "...", "...", "..."],
    "°": [".#.", "#.#", ".#.", "...", "..."],
    "(": [".#.", "#..", "#..", "#..", ".#."],
    ")": [".#.", "..#", "..#", "..#", ".#."],
    "#": ["#.#", "###", "#.#", "###", "#.#"],
}
# fmt: on


def _glyph(ch: str) -> list[str]:
    if ch in _FONT:
        return _FONT[ch]
    up = ch.upper()
    return _FONT.get(up, _FONT[" "])


def char_width(scale: int = 1) -> int:
    return GLYPH_W * scale


def text_width(text: str, scale: int = 1) -> int:
    if not text:
        return 0
    return len(text) * (GLYPH_W * scale) + (len(text) - 1) * (GAP * scale)


def text_height(scale: int = 1) -> int:
    return GLYPH_H * scale


def fit_text(text: str, max_width: int, scale: int = 1) -> str:
    """Truncate text (with a trailing '.') so it fits within max_width px."""
    if text_width(text, scale) <= max_width:
        return text
    out = text
    while out and text_width(out + ".", scale) > max_width:
        out = out[:-1]
    return (out + ".") if out else ""


def draw_text(
    img: Image.Image,
    x: int,
    y: int,
    text: str,
    fill=(255, 255, 255),
    bg=None,
    scale: int = 1,
) -> int:
    """Draw ``text`` at (x, y). Returns the x just past the text.

    If ``bg`` is given, a filled rectangle is drawn behind the text first (for
    contrast). Pixels are drawn as scale x scale blocks (no smoothing).
    """
    d = ImageDraw.Draw(img)
    if bg is not None and text:
        w = text_width(text, scale)
        d.rectangle([x - 1, y - 1, x + w, y + GLYPH_H * scale], fill=bg)
    cx = x
    for ch in text:
        rows = _glyph(ch)
        for ry, row in enumerate(rows):
            for rxi, cell in enumerate(row):
                if cell == "#":
                    px = cx + rxi * scale
                    py = y + ry * scale
                    d.rectangle([px, py, px + scale - 1, py + scale - 1], fill=fill)
        cx += (GLYPH_W + GAP) * scale
    return cx - GAP * scale
