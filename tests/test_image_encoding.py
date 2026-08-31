import base64

from PIL import Image

from controller.image import RAW_LEN, encode_frame, frame_hash, to_rgb_bytes
from controller.test_pattern import build_test_image


def test_raw_length_exact():
    img = Image.new("RGB", (64, 64), (1, 2, 3))
    assert len(to_rgb_bytes(img)) == RAW_LEN == 12288


def test_rgb_byte_order_and_positions():
    img = Image.new("RGB", (64, 64), (0, 0, 0))
    img.putpixel((0, 0), (10, 20, 30))   # first pixel
    img.putpixel((1, 0), (40, 50, 60))   # second pixel (same row, x=1)
    img.putpixel((0, 1), (70, 80, 90))   # first pixel of second row
    raw = to_rgb_bytes(img)
    assert raw[0:3] == bytes((10, 20, 30))
    assert raw[3:6] == bytes((40, 50, 60))
    # second row starts at offset 64*3
    assert raw[64 * 3: 64 * 3 + 3] == bytes((70, 80, 90))


def test_base64_roundtrip():
    img = Image.new("RGB", (64, 64), (5, 6, 7))
    b64 = encode_frame(img)
    assert len(base64.b64decode(b64)) == RAW_LEN


def test_resize_nonstandard_input():
    img = Image.new("RGB", (32, 48), (9, 9, 9))
    assert len(to_rgb_bytes(img)) == RAW_LEN


def test_rgba_alpha_flattened_onto_black():
    img = Image.new("RGBA", (64, 64), (200, 100, 50, 0))  # fully transparent
    raw = to_rgb_bytes(img)
    assert raw[0:3] == bytes((0, 0, 0))  # transparent -> black


def test_test_pattern_shape():
    img = build_test_image()
    assert img.size == (64, 64) and img.mode == "RGB"
    assert img.getpixel((32, 32)) == (255, 255, 255)  # centre white square


def test_frame_hash_changes():
    a = Image.new("RGB", (64, 64), (0, 0, 0))
    b = Image.new("RGB", (64, 64), (0, 0, 0))
    assert frame_hash(a) == frame_hash(b)
    b.putpixel((10, 10), (255, 0, 0))
    assert frame_hash(a) != frame_hash(b)
