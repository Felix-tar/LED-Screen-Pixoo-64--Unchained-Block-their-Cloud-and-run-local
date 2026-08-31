"""Builds the acceptance test image described in the task (section 13).

  * white outer frame
  * four quadrants: red / green / blue / yellow
  * black cross through the middle
  * white square in the centre
  * the text "LOCAL"
"""
from __future__ import annotations

from PIL import Image, ImageDraw

from dashboard.pixelfont import draw_text, text_width

WIDTH = 64
HEIGHT = 64

RED = (220, 30, 30)
GREEN = (30, 200, 60)
BLUE = (40, 90, 230)
YELLOW = (230, 200, 30)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)


def build_test_image() -> Image.Image:
    img = Image.new("RGB", (WIDTH, HEIGHT), BLACK)
    d = ImageDraw.Draw(img)

    half = WIDTH // 2
    # four quadrants
    d.rectangle([0, 0, half - 1, half - 1], fill=RED)
    d.rectangle([half, 0, WIDTH - 1, half - 1], fill=GREEN)
    d.rectangle([0, half, half - 1, HEIGHT - 1], fill=BLUE)
    d.rectangle([half, half, WIDTH - 1, HEIGHT - 1], fill=YELLOW)

    # white outer frame (1px)
    d.rectangle([0, 0, WIDTH - 1, HEIGHT - 1], outline=WHITE)

    # black cross through the middle (2px)
    d.rectangle([half - 1, 0, half, HEIGHT - 1], fill=BLACK)
    d.rectangle([0, half - 1, WIDTH - 1, half], fill=BLACK)

    # white square in the centre
    cs = 10
    d.rectangle(
        [half - cs // 2, half - cs // 2, half + cs // 2 - 1, half + cs // 2 - 1],
        fill=WHITE,
    )

    # "LOCAL" text, centred near the top over the frame
    label = "LOCAL"
    w = text_width(label, scale=1)
    draw_text(img, (WIDTH - w) // 2, 3, label, fill=WHITE, bg=BLACK, scale=1)
    return img
