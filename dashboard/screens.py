"""Screens + playlist: compose widgets into 64x64 frames and rotate them.

The layout lives in config/screens.json (edited in the browser editor). This
renderer reloads that file whenever it changes, so edits take effect live
without a restart. Exposes .render() (current screen in the rotation) as a drop-in
frame_provider for the supervisor, plus .render_index() for the editor preview.
"""
from __future__ import annotations

import io
import json
import time
from pathlib import Path

from PIL import Image, ImageDraw

from common.logutil import get_logger
from .widgets import RenderContext, hexcolor, render_widget, slide_seconds

log = get_logger("dashboard.screens")

W = H = 64


def default_screens() -> dict:
    """A sensible starter playlist (used if config/screens.json is missing)."""
    return {
        "rotation": True,
        "screens": [
            {
                "name": "Server", "duration": 10, "background": "#000000",
                "widgets": [
                    {"type": "text", "x": 1, "y": 0, "w": 62, "h": 6, "text": "SERVER", "color": "#f0f0f0"},
                    {"type": "sysbars", "x": 1, "y": 8, "w": 62, "h": 22, "metrics": ["cpu", "ram", "disk"]},
                    {"type": "line", "x": 0, "y": 31, "w": 64, "h": 1},
                    {"type": "clock", "x": 0, "y": 34, "w": 64, "h": 20, "scale": 3, "format": "HH:MM"},
                    {"type": "date", "x": 0, "y": 56, "w": 64, "h": 6, "format": "WD DD.MM"},
                ],
            },
            {
                "name": "Market", "duration": 8, "background": "#000000",
                "widgets": [
                    {"type": "list", "x": 1, "y": 0, "w": 62, "h": 64, "title": "MARKET", "source": "crypto"},
                ],
            },
        ],
    }


class PlaylistRenderer:
    def __init__(self, cfg):
        self.cfg = cfg
        self.path = Path(cfg.path).parent / "screens.json"
        self.ctx = RenderContext(cfg)
        self._data = None
        self._mtime = 0.0
        self._start = time.time()
        self._load()

    # -- config i/o ------------------------------------------------------
    def _load(self):
        try:
            if self.path.exists():
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
                self._mtime = self.path.stat().st_mtime
                return
        except Exception as e:
            log.error("failed to load screens.json: %s", e)
        self._data = default_screens()

    def _maybe_reload(self):
        try:
            if self.path.exists() and self.path.stat().st_mtime != self._mtime:
                log.info("screens.json changed — reloading")
                self._load()
        except OSError:
            pass

    def data(self) -> dict:
        self._maybe_reload()
        return self._data or default_screens()

    def save(self, data: dict):
        validate_playlist(data)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        self._data = data
        self._mtime = self.path.stat().st_mtime

    # -- rendering -------------------------------------------------------
    def screens(self) -> list:
        return self.data().get("screens", []) or []

    def _effective_duration(self, screen: dict) -> float:
        """Configured duration, but never shorter than the time a widget on it
        needs to cycle through all its slides — so a screen isn't cut off mid-cycle."""
        base = max(1, int(screen.get("duration", 10)))
        need = 0.0
        for w in screen.get("widgets", []) or []:
            try:
                need = max(need, slide_seconds(w, self.ctx))
            except Exception:
                pass
        return max(float(base), need)

    def _current(self) -> tuple[int, float]:
        """Return (screen index, seconds elapsed within that screen)."""
        screens = self.screens()
        if not screens:
            return -1, 0.0
        if not self.data().get("rotation", True) or len(screens) == 1:
            return 0, time.time() - self._start
        durs = [self._effective_duration(s) for s in screens]
        total = sum(durs) or 1
        t = (time.time() - self._start) % total
        acc = 0.0
        for i, dsec in enumerate(durs):
            if t < acc + dsec:
                return i, t - acc
            acc += dsec
        return 0, 0.0

    def _current_index(self) -> int:
        return self._current()[0]

    def render_screen(self, screen: dict) -> Image.Image:
        img = Image.new("RGB", (W, H), hexcolor(screen.get("background"), (0, 0, 0)))
        d = ImageDraw.Draw(img)
        for wdef in screen.get("widgets", []):
            render_widget(img, d, wdef, self.ctx)
        return img

    def render_index(self, i: int) -> Image.Image:
        screens = self.screens()
        if 0 <= i < len(screens):
            return self.render_screen(screens[i])
        return Image.new("RGB", (W, H), (0, 0, 0))

    def render(self) -> Image.Image:
        """Current screen in the rotation — drop-in frame_provider."""
        i, elapsed = self._current()
        if i < 0:
            return Image.new("RGB", (W, H), (0, 0, 0))
        self.ctx.slide_time = elapsed          # slides step in order within the screen
        try:
            return self.render_index(i)
        finally:
            self.ctx.slide_time = None

    def render_png(self, scale: int = 6, index: int | None = None,
                   screen: dict | None = None) -> bytes:
        if screen is not None:
            img = self.render_screen(screen)
        elif index is not None:
            img = self.render_index(index)
        else:
            img = self.render()
        if scale > 1:
            img = img.resize((W * scale, H * scale), Image.NEAREST)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


def validate_playlist(data: dict):
    if not isinstance(data, dict) or not isinstance(data.get("screens"), list):
        raise ValueError("playlist must be an object with a 'screens' array")
    from .widgets import REGISTRY
    for s in data["screens"]:
        if not isinstance(s, dict):
            raise ValueError("each screen must be an object")
        for wdef in s.get("widgets", []) or []:
            if wdef.get("type") not in REGISTRY:
                raise ValueError(f"unknown widget type: {wdef.get('type')}")
            for k in ("x", "y", "w", "h"):
                if k in wdef and not (0 <= int(wdef[k]) <= 64):
                    raise ValueError(f"widget {k} out of range 0..64")
