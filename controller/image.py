"""Pixoo 64 frame encoding.

The Pixoo expects, per static frame, exactly 64*64*3 = 12288 raw RGB bytes in
row-major order (left->right, top->bottom), then base64-encoded ascii.
No alpha, no BGR, no PNG/JPEG. See NOTES from the reverse-engineered API.
"""
from __future__ import annotations

import base64
import hashlib

from PIL import Image

WIDTH = 64
HEIGHT = 64
RAW_LEN = WIDTH * HEIGHT * 3  # 12288


def to_rgb_bytes(image: Image.Image) -> bytes:
    """Convert a PIL image to exactly 12288 RGB bytes for the Pixoo.

    The image is force-resized to 64x64 (NEAREST, to preserve pixel art) and
    converted to RGB (alpha flattened onto black) before extraction.
    """
    if image.size != (WIDTH, HEIGHT):
        # NEAREST: no smoothing, keeps pixel art crisp when up/down-scaling.
        image = image.resize((WIDTH, HEIGHT), Image.NEAREST)
    if image.mode != "RGB":
        if image.mode in ("RGBA", "LA", "P"):
            image = image.convert("RGBA")
            bg = Image.new("RGB", image.size, (0, 0, 0))
            bg.paste(image, mask=image.split()[-1])
            image = bg
        else:
            image = image.convert("RGB")

    raw = bytearray(RAW_LEN)
    px = image.load()
    i = 0
    for y in range(HEIGHT):
        for x in range(WIDTH):
            r, g, b = px[x, y]
            raw[i] = r
            raw[i + 1] = g
            raw[i + 2] = b
            i += 3
    assert len(raw) == RAW_LEN, f"expected {RAW_LEN} bytes, got {len(raw)}"
    return bytes(raw)


def encode_frame(image: Image.Image) -> str:
    """Return base64 ascii PicData for a single 64x64 frame."""
    raw = to_rgb_bytes(image)
    return base64.b64encode(raw).decode("ascii")


def frame_hash(image: Image.Image) -> str:
    """Stable hash of the raw pixel content (used to skip unchanged frames)."""
    return hashlib.sha1(to_rgb_bytes(image)).hexdigest()
