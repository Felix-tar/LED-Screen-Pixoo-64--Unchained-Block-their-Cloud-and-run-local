"""64x64 dashboard renderer (Pillow).

Top half  : homeserver status (SERVER + health dot, CPU/RAM/DISK bars, temp).
Bottom half: market rows (label + coloured change with up/down arrow).

Readability beats density at 64x64, so we show a few high-contrast items. Each
data source is isolated: if sysinfo or market fails, the other half still draws.
"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw

from common.logutil import get_logger
from . import market, sysinfo
from .pixelfont import GLYPH_H, draw_text, fit_text, text_width

log = get_logger("dashboard.renderer")

W = H = 64
BLACK = (0, 0, 0)
WHITE = (245, 245, 245)
DIM = (150, 150, 150)
GREEN = (40, 205, 70)
ORANGE = (235, 150, 30)
RED = (225, 55, 45)
BLUE = (70, 120, 235)
GRAY = (110, 110, 110)


def _sev_color(pct: float) -> tuple:
    if pct < 70:
        return GREEN
    if pct < 90:
        return ORANGE
    return RED


def _draw_metric(img, d, y, label, pct):
    pct = max(0.0, min(100.0, float(pct)))
    draw_text(img, 1, y, label[:3], fill=DIM, scale=1)
    val = f"{int(round(pct))}%"
    vw = text_width(val, 1)
    vx = W - 1 - vw
    draw_text(img, vx, y, val, fill=WHITE, scale=1)
    bar_x0 = 1 + text_width("XXX", 1) + 2
    bar_x1 = vx - 3
    if bar_x1 > bar_x0:
        d.rectangle([bar_x0, y, bar_x1, y + GLYPH_H - 1], outline=GRAY)
        fill_w = int((bar_x1 - bar_x0 - 1) * pct / 100.0)
        if fill_w > 0:
            d.rectangle([bar_x0 + 1, y + 1, bar_x0 + fill_w, y + GLYPH_H - 2],
                        fill=_sev_color(pct))


def _draw_arrow(d, x, y, direction: str):
    if direction == "up":
        d.polygon([(x, y + 4), (x + 4, y + 4), (x + 2, y)], fill=GREEN)
    elif direction == "down":
        d.polygon([(x, y), (x + 4, y), (x + 2, y + 4)], fill=RED)
    else:
        d.rectangle([x, y + 1, x + 4, y + 3], fill=GRAY)


class DashboardRenderer:
    def __init__(self, cfg):
        self.cfg = cfg
        self.disk_path = cfg.get("dashboard", "disk_path", default="/")
        self.market_enabled = bool(cfg.get("dashboard", "market", "enabled", default=True))

    def render(self) -> Image.Image:
        img = Image.new("RGB", (W, H), BLACK)
        d = ImageDraw.Draw(img)
        try:
            self._render_server(img, d)
        except Exception as e:  # keep the other half alive
            log.error("server half failed: %s", e)
        try:
            if self.market_enabled:
                self._render_market(img, d)
        except Exception as e:
            log.error("market half failed: %s", e)
        return img

    # -- halves ----------------------------------------------------------
    def _render_server(self, img, d):
        info = sysinfo.collect(self.disk_path)
        draw_text(img, 1, 0, "SERVER", fill=WHITE, scale=1)
        # temperature (right side of header)
        if info["temp_c"] is not None:
            t = f"{int(round(info['temp_c']))}°"
            tw = text_width(t, 1)
            draw_text(img, W - 6 - tw, 0, t, fill=DIM, scale=1)
        # health dot top-right
        dot = GREEN if info["healthy"] else RED
        d.rectangle([W - 4, 0, W - 1, 3], fill=dot)

        _draw_metric(img, d, 8, "CPU", info["cpu"])
        _draw_metric(img, d, 15, "RAM", info["ram"])
        _draw_metric(img, d, 22, "DSK", info["disk"])

    def _render_market(self, img, d):
        d.line([(0, 30), (W - 1, 30)], fill=(40, 40, 40))
        quotes = market.get_quotes(self.cfg, max_items=4)
        y = 33
        for q in quotes:
            change = q.get("change")
            if change is None:
                direction, color = "flat", DIM
            elif change > 0:
                direction, color = "up", GREEN
            elif change < 0:
                direction, color = "down", RED
            else:
                direction, color = "flat", DIM
            _draw_arrow(d, 1, y, direction)
            label = fit_text(str(q.get("label", "")), 26, 1)
            draw_text(img, 8, y, label, fill=WHITE, scale=1)
            val = str(q.get("value", ""))
            vw = text_width(val, 1)
            draw_text(img, W - 1 - vw, y, val, fill=color, scale=1)
            y += 7

    # -- preview ---------------------------------------------------------
    def render_png(self, scale: int = 6) -> bytes:
        img = self.render()
        if scale > 1:
            img = img.resize((W * scale, H * scale), Image.NEAREST)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
