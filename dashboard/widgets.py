"""Widget framework for composable 64x64 screens.

A *screen* is a background plus a list of *widgets*; each widget draws into its
own rectangle (x, y, w, h) of the frame. Widgets are configured as plain dicts
(from config/screens.json, editable in the browser editor), so no code change is
needed to build new layouts.

Add a widget type by writing a render function and decorating it with
@widget("mytype"); it immediately becomes available in the editor.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from PIL import Image, ImageDraw

from common.logutil import get_logger
from . import market, sysinfo
from .pixelfont import GLYPH_H, draw_text, fit_text, text_width

log = get_logger("dashboard.widgets")

WHITE = (240, 240, 240)
DIM = (150, 150, 150)
GREEN = (40, 205, 70)
RED = (225, 55, 45)
ORANGE = (235, 150, 30)
GRAY = (110, 110, 110)

REGISTRY: dict[str, Callable] = {}


def widget(type_name: str):
    def deco(fn):
        REGISTRY[type_name] = fn
        fn.widget_type = type_name
        return fn
    return deco


def hexcolor(value, default=WHITE) -> tuple:
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return tuple(int(c) for c in value)
    if isinstance(value, str) and value.startswith("#") and len(value) == 7:
        try:
            return (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16))
        except ValueError:
            pass
    return default


def _sev(pct: float) -> tuple:
    """Green below 70; from 70 orange, darkening smoothly to red by 90; red above."""
    try:
        pct = float(pct)
    except (TypeError, ValueError):
        return GREEN
    if pct < 70:
        return GREEN
    if pct >= 90:
        return RED
    t = (pct - 70.0) / 20.0  # 0..1 across 70..90
    return tuple(int(round(ORANGE[i] + (RED[i] - ORANGE[i]) * t)) for i in range(3))


class RenderContext:
    """Shared, briefly-cached data sources for all widgets on a frame."""

    def __init__(self, cfg):
        self.cfg = cfg
        self._sys = None
        self._sys_ts = 0.0
        self._http: dict[str, tuple[float, object]] = {}
        # seconds since the current screen became active (set by the playlist so
        # slideshow widgets step 0,1,2,… in order); None in editor previews.
        self.slide_time = None

    def now(self, tz: str | None = None) -> datetime:
        # A clock without an explicit tz defaults to the configured home zone
        # (Europe/Berlin), NOT the container's UTC clock. zoneinfo/tzdata does
        # the winter<->summer switch automatically for every country, offline.
        zone = tz or self.cfg.get("display", "timezone", default="Europe/Berlin")
        try:
            base = datetime.now(ZoneInfo(zone))
        except (ZoneInfoNotFoundError, Exception):
            base = datetime.now().astimezone()
        off = self._time_offset()
        return base + timedelta(seconds=off) if off else base

    def _time_offset(self) -> float:
        """Safety net: if the host clock has drifted grossly (NTP failure), apply
        the internet-time offset the data-bridge measured. Normally ~0 (NTP), so
        no correction is applied below the configured threshold."""
        thr = float(self.cfg.get("display", "time_correction_threshold_seconds", default=0) or 0)
        if thr <= 0:
            return 0.0
        data = _load_data("time")
        try:
            off = float(data.get("offset_seconds"))
        except (AttributeError, TypeError, ValueError):
            return 0.0
        return off if abs(off) >= thr else 0.0

    def sysinfo(self) -> dict:
        if not self._sys or (time.time() - self._sys_ts) > 2:
            self._sys = sysinfo.collect(self.cfg.get("dashboard", "disk_path", default="/"))
            self._sys_ts = time.time()
        return self._sys

    def market(self) -> list:
        try:
            return market.get_quotes(self.cfg, max_items=8)
        except Exception:
            return []

    def http_json(self, url: str, cache_seconds: int = 30):
        if not url:
            return None
        hit = self._http.get(url)
        if hit and (time.time() - hit[0]) < cache_seconds:
            return hit[1]
        try:
            import requests
            data = requests.get(url, timeout=4).json()
        except Exception as e:
            log.debug("http_json %s failed: %s", url, e)
            data = hit[1] if hit else None
        self._http[url] = (time.time(), data)
        return data


DATA_DIR = os.environ.get("PIXOO_DATA_DIR", "/opt/pixoo-local/data")


def _load_data(ref: str):
    """Load a data-bridge JSON by bare name ('market') or explicit path."""
    if not ref:
        return None
    path = ref if ("/" in ref or ref.endswith(".json")) else os.path.join(DATA_DIR, f"{ref}.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _money(v) -> str:
    """Compact money using uppercase magnitudes (font has no lowercase/$/€)."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "-"
    a = abs(v)
    if a >= 1e9:
        return f"{v/1e9:.2f}B"
    if a >= 1e6:
        return f"{v/1e6:.2f}M"
    if a >= 1e3:
        return f"{v/1e3:.1f}K"
    if a >= 100:
        return f"{v:.0f}"
    if a >= 1:
        return f"{v:.2f}"
    return f"{v:.4f}"


def _money_full(v) -> str:
    """Fully-written price, German style: '.' thousands, ',' decimals.
    79654 -> '79.654'   231.4 -> '231,40'   0.4523 -> '0,4523'."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "-"
    a = abs(v)
    if a >= 1000:
        return f"{v:,.0f}".replace(",", ".")
    if a >= 1:
        s = f"{v:,.2f}"                     # e.g. 231.40 (no thousands sep < 1000)
        intp, dec = s.split(".")
        return intp.replace(",", ".") + "," + dec
    return f"{v:.4f}".replace(".", ",")


def _chg(c):
    try:
        c = float(c)
    except (TypeError, ValueError):
        return None, DIM
    return f"{'+' if c >= 0 else ''}{c:.1f}%", (GREEN if c > 0 else RED if c < 0 else DIM)


def _fit_ticker(sym, maxw, scale) -> str:
    """Always show the whole ticker. If it doesn't fit, drop the dot(s)
    (SAP.DE -> SAPDE) before ever truncating."""
    sym = str(sym).upper()
    if text_width(sym, scale) <= maxw:
        return sym
    nod = sym.replace(".", "")
    if text_width(nod, scale) <= maxw:
        return nod
    return fit_text(nod, maxw, scale)


def _slide_base(ctx, rot):
    """Slide index base: screen-relative time if the playlist set it, else wall
    clock. Divided by rot by the caller."""
    t = getattr(ctx, "slide_time", None)
    return (t if t is not None else time.time())


def _as_list(v) -> list:
    if isinstance(v, list):
        return [str(s).strip() for s in v if str(s).strip()]
    return [s.strip() for s in str(v or "").split(",") if s.strip()]


def _countdown(resets_at) -> str:
    """ISO timestamp -> compact 'in' string like 3H12M / 2D4H / NOW."""
    if not resets_at:
        return ""
    try:
        s = str(resets_at).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        secs = (dt - datetime.now(timezone.utc)).total_seconds()
    except Exception:
        return ""
    if secs <= 0:
        return "NOW"
    days, rem = divmod(int(secs), 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    if days >= 1:
        return f"{days}D{hours}H"
    if hours >= 1:
        return f"{hours}H{mins:02d}M"
    return f"{mins}M"


def _dig(obj, path: str):
    """Extract a nested value by a dotted path with [index] support."""
    cur = obj
    for part in str(path).replace("]", "").replace("[", ".").split("."):
        if part == "":
            continue
        try:
            if isinstance(cur, list):
                cur = cur[int(part)]
            elif isinstance(cur, dict):
                cur = cur.get(part)
            else:
                return None
        except (ValueError, IndexError, KeyError, TypeError):
            return None
    return cur


# --------------------------------------------------------------------------
# widgets
# --------------------------------------------------------------------------
@widget("text")
def w_text(img, d, r, p, ctx):
    x, y, w, h = r
    scale = int(p.get("scale", 1))
    color = hexcolor(p.get("color"), WHITE)
    txt = str(p.get("text", ""))
    txt = fit_text(txt, w, scale)
    tw = text_width(txt, scale)
    align = p.get("align", "left")
    tx = x + (w - tw if align == "right" else (w - tw) // 2 if align == "center" else 0)
    draw_text(img, tx, y, txt, fill=color, scale=scale)


@widget("clock")
def w_clock(img, d, r, p, ctx):
    x, y, w, h = r
    scale = int(p.get("scale", 2))
    color = hexcolor(p.get("color"), WHITE)
    fmt = {"HH:MM": "%H:%M", "HH:MM:SS": "%H:%M:%S", "H:MM": "%-H:%M"}.get(
        p.get("format", "HH:MM"), "%H:%M")
    txt = ctx.now(p.get("tz")).strftime(fmt)
    label = p.get("label")
    tw = text_width(txt, scale)
    tx = x + max(0, (w - tw) // 2)
    ty = y + (max(0, (h - GLYPH_H * scale) // 2) if not label else 0)
    if label:
        draw_text(img, x + max(0, (w - text_width(label, 1)) // 2), y, str(label), fill=DIM, scale=1)
        ty = y + GLYPH_H + 2
    draw_text(img, tx, ty, txt, fill=color, scale=scale)


@widget("date")
def w_date(img, d, r, p, ctx):
    x, y, w, h = r
    scale = int(p.get("scale", 1))
    color = hexcolor(p.get("color"), DIM)
    fmt = {"DD.MM": "%d.%m", "DD.MM.YY": "%d.%m.%y", "WD DD.MM": "%a %d.%m"}.get(
        p.get("format", "DD.MM.YY"), "%d.%m.%y")
    txt = ctx.now(p.get("tz")).strftime(fmt).upper()
    draw_text(img, x + max(0, (w - text_width(txt, scale)) // 2), y, txt, fill=color, scale=scale)


@widget("sysbars")
def w_sysbars(img, d, r, p, ctx):
    x, y, w, h = r
    info = ctx.sysinfo()
    metrics = p.get("metrics", ["cpu", "ram", "disk"])
    labels = {"cpu": "CPU", "ram": "RAM", "disk": "DSK", "temp": "TMP"}
    row = 7
    for i, m in enumerate(metrics):
        yy = y + i * row
        if yy + GLYPH_H > y + h:
            break
        val = float(info.get(m if m != "temp" else "temp_c") or 0)
        draw_text(img, x, yy, labels.get(m, m.upper())[:3], fill=DIM, scale=1)
        unit = "°" if m == "temp" else "%"
        vs = f"{int(round(val))}{unit}"
        vw = text_width(vs, 1)
        draw_text(img, x + w - vw, yy, vs, fill=WHITE, scale=1)
        bx0 = x + text_width("XXX", 1) + 2
        bx1 = x + w - vw - 3
        if m != "temp" and bx1 > bx0:
            d.rectangle([bx0, yy, bx1, yy + GLYPH_H - 1], outline=GRAY)
            fw = int((bx1 - bx0 - 1) * max(0.0, min(100.0, val)) / 100.0)
            if fw > 0:
                d.rectangle([bx0 + 1, yy + 1, bx0 + fw, yy + GLYPH_H - 2], fill=_sev(val))


@widget("metric")
def w_metric(img, d, r, p, ctx):
    x, y, w, h = r
    info = ctx.sysinfo()
    src = p.get("source", "cpu")
    label = p.get("label", src.upper())[:6]
    if src == "temp":
        val = float(info.get("temp_c") or 0)
        vs = f"{int(round(val))}°"
    else:
        val = float(info.get(src) or 0)
        vs = f"{int(round(val))}%"
    draw_text(img, x, y, label, fill=DIM, scale=1)
    draw_text(img, x + w - text_width(vs, 1), y, vs, fill=hexcolor(p.get("color"), WHITE), scale=1)


@widget("list")
def w_list(img, d, r, p, ctx):
    x, y, w, h = r
    title = p.get("title")
    yy = y
    if title:
        draw_text(img, x, yy, fit_text(str(title), w, 1), fill=hexcolor(p.get("titlecolor"), DIM), scale=1)
        yy += 7
    src = p.get("source", "static")
    rows = []
    if src == "crypto" or src == "market":
        rows = [{"label": q.get("label"), "value": q.get("value"), "change": q.get("change")}
                for q in ctx.market()]
    elif src == "http":
        data = ctx.http_json(p.get("url", ""), int(p.get("cache_seconds", 30)))
        for f in p.get("fields", []):
            rows.append({"label": f.get("label", ""), "value": _dig(data, f.get("path", ""))})
    else:
        rows = p.get("items", [])
    maxrows = max(0, (y + h - yy) // 7)
    for row in rows[:maxrows]:
        lab = fit_text(str(row.get("label", "")), w // 2, 1)
        draw_text(img, x, yy, lab, fill=WHITE, scale=1)
        val = str(row.get("value", ""))
        ch = row.get("change")
        col = WHITE
        if ch is not None:
            try:
                col = GREEN if float(ch) > 0 else RED if float(ch) < 0 else DIM
            except (ValueError, TypeError):
                col = WHITE
        vw = text_width(val, 1)
        draw_text(img, x + w - vw, yy, fit_text(val, w // 2, 1), fill=col, scale=1)
        yy += 7


@widget("kv")
def w_kv(img, d, r, p, ctx):
    """Fetch a JSON doc and show label/value pairs (great for the XMR server or a
    file another process writes, e.g. Claude usage)."""
    x, y, w, h = r
    src = p.get("source", "http")
    data = ctx.http_json(p.get("url", ""), int(p.get("cache_seconds", 30))) if src == "http" else None
    if src == "file":
        try:
            import json
            data = json.loads(open(p.get("path", "")).read())
        except Exception:
            data = None
    yy = y
    if p.get("title"):
        draw_text(img, x, yy, fit_text(str(p["title"]), w, 1), fill=DIM, scale=1)
        yy += 7
    for f in p.get("fields", []):
        if yy + GLYPH_H > y + h:
            break
        lab = fit_text(str(f.get("label", "")), w, 1)
        draw_text(img, x, yy, lab, fill=DIM, scale=1)
        val = _dig(data, f.get("path", "")) if data is not None else None
        if val is None:
            val = f.get("default", "-")
        suf = f.get("suffix", "")
        vs = f"{val}{suf}"
        draw_text(img, x + w - text_width(vs, 1), yy, fit_text(vs, w, 1), fill=hexcolor(f.get("color"), WHITE), scale=1)
        yy += 7


@widget("bar")
def w_bar(img, d, r, p, ctx):
    """A horizontal progress bar (0..100), e.g. a usage/limit percentage."""
    x, y, w, h = r
    src = p.get("source", "value")
    if src == "http":
        pct = _dig(ctx.http_json(p.get("url", ""), int(p.get("cache_seconds", 30))), p.get("path", ""))
    elif src in ("cpu", "ram", "disk"):
        pct = ctx.sysinfo().get(src)
    else:
        pct = p.get("value", 0)
    try:
        pct = max(0.0, min(100.0, float(pct)))
    except (ValueError, TypeError):
        pct = 0.0
    label = p.get("label")
    yy = y
    if label:
        draw_text(img, x, yy, fit_text(f"{label} {int(round(pct))}%", w, 1), fill=WHITE, scale=1)
        yy += 7
    d.rectangle([x, yy, x + w - 1, min(y + h - 1, yy + 4)], outline=GRAY)
    fw = int((w - 2) * pct / 100.0)
    if fw > 0:
        d.rectangle([x + 1, yy + 1, x + fw, min(y + h - 2, yy + 3)], fill=hexcolor(p.get("color"), _sev(pct)))


@widget("rect")
def w_rect(img, d, r, p, ctx):
    x, y, w, h = r
    fill = hexcolor(p.get("fill"), None) if p.get("fill") else None
    outline = hexcolor(p.get("outline"), None) if p.get("outline") else None
    d.rectangle([x, y, x + w - 1, y + h - 1], fill=fill, outline=outline)


@widget("line")
def w_line(img, d, r, p, ctx):
    x, y, w, h = r
    d.line([(x, y), (x + w - 1, y)], fill=hexcolor(p.get("color"), (40, 40, 40)))


@widget("market")
def w_market(img, d, r, p, ctx):
    """Crypto + stock quotes from data/market.json (host data-bridge), shown in
    EUR/USD with % change. `view: card` cycles one symbol at a time within the
    same screen; `view: rows` is a compact paginated list."""
    x, y, w, h = r
    data = _load_data(p.get("path") or p.get("source") or "market")
    items = (data or {}).get("items", []) if isinstance(data, dict) else []
    want = [s.upper() for s in _as_list(p.get("symbols"))]
    if want:
        order = {s: i for i, s in enumerate(want)}
        items = sorted([it for it in items if str(it.get("symbol", "")).upper() in order],
                       key=lambda it: order.get(str(it.get("symbol", "")).upper(), 999))
    if not items:
        draw_text(img, x, y, "NO MARKET DATA", fill=DIM, scale=1)
        return
    rot = max(1, int(p.get("rotate_seconds", 4)))
    if p.get("view", "rows") == "card":
        idx = int(_slide_base(ctx, rot) // rot) % len(items)
        _market_card(img, d, r, items[idx], len(items), idx, p)
    else:
        _market_rows(img, d, r, items, p, rot, ctx)


def _market_card(img, d, r, it, total, idx, p):
    x, y, w, h = r
    chg, col = _chg(it.get("change_pct"))
    cw = text_width(chg, 1) if chg else 0
    if chg:
        draw_text(img, x + w - cw, y + 2, chg, fill=col, scale=1)
    sym = _fit_ticker(it.get("symbol", ""), w - cw - 2, 2)
    draw_text(img, x, y, sym, fill=WHITE, scale=2)
    draw_text(img, x, y + 12, fit_text(str(it.get("name", "")).upper(), w, 1), fill=DIM, scale=1)
    cur = p.get("currency", "usd")
    prim_key, sec_key = ("price_eur", "price_usd") if cur == "eur" else ("price_usd", "price_eur")
    prim_lbl, sec_lbl = ("EUR", "USD") if cur == "eur" else ("USD", "EUR")
    # big, fully-written primary price (reserve room for the currency tag)
    lblw = text_width(prim_lbl, 1)
    bp = fit_text(_money_full(it.get(prim_key)), w - lblw - 3, 2)
    draw_text(img, x, y + 21, bp, fill=WHITE, scale=2)
    draw_text(img, x + w - lblw, y + 25, prim_lbl, fill=DIM, scale=1)
    # secondary currency, smaller
    draw_text(img, x, y + 37, fit_text(f"{sec_lbl} {_money_full(it.get(sec_key))}", w, 1),
              fill=DIM, scale=1)
    if total > 1:  # page dots
        dotw = min(total, w // 4)
        for i in range(dotw):
            cx = x + (w - dotw * 4) // 2 + i * 4
            d.rectangle([cx, y + h - 2, cx + 1, y + h - 1],
                        fill=WHITE if i == idx % dotw else GRAY)


def _market_rows(img, d, r, items, p, rot, ctx=None):
    x, y, w, h = r
    yy = y
    if p.get("title"):
        draw_text(img, x, yy, fit_text(str(p["title"]), w, 1), fill=DIM, scale=1)
        yy += 7
    cur = p.get("currency", "usd")
    key = "price_eur" if cur == "eur" else "price_usd"
    maxrows = max(1, (y + h - yy) // 7)
    pages = (len(items) + maxrows - 1) // maxrows
    page = (int(_slide_base(ctx, rot) // rot) % pages) if pages > 1 else 0
    for it in items[page * maxrows:(page + 1) * maxrows]:
        chg, col = _chg(it.get("change_pct"))
        cw = text_width(chg, 1) if chg else 0
        if chg:
            draw_text(img, x + w - cw, yy, chg, fill=col, scale=1)
        # ticker always fully shown (dot dropped if needed); value takes the rest,
        # full number when it fits else a compact one (222 / 78.8K) — never a cut-off
        sym = _fit_ticker(it.get("symbol", ""), w - cw - 6, 1)
        sw = text_width(sym, 1)
        draw_text(img, x, yy, sym, fill=WHITE, scale=1)
        vmax = w - cw - sw - 6
        vs = _money_full(it.get(key))
        if text_width(vs, 1) > vmax:
            vs = _money(it.get(key))
        vs = fit_text(vs, vmax, 1)
        draw_text(img, x + w - cw - 3 - text_width(vs, 1), yy, vs, fill=WHITE, scale=1)
        yy += 7


@widget("claude")
def w_claude(img, d, r, p, ctx):
    """Claude Code usage limits from data/claude.json (host data-bridge queries
    the Anthropic OAuth usage endpoint server-side). Shows % used + reset
    countdown per limit (5h session, weekly, per-model)."""
    x, y, w, h = r
    data = _load_data(p.get("path") or p.get("source") or "claude")
    yy = y
    title = p.get("title", "CLAUDE")
    if title:
        draw_text(img, x, yy, fit_text(str(title), w, 1), fill=hexcolor(p.get("titlecolor"), ORANGE), scale=1)
        yy += 8
    if not isinstance(data, dict) or not data.get("ok"):
        msg = "NO TOKEN" if (isinstance(data, dict) and not data.get("configured")) else \
              (str((data or {}).get("error", "NO DATA")).upper() if isinstance(data, dict) else "NO DATA")
        draw_text(img, x, yy, fit_text(msg, w, 1), fill=DIM, scale=1)
        return
    limits = data.get("limits", [])
    show = [s.lower() for s in _as_list(p.get("show"))]
    if show:
        limits = [l for l in limits
                  if l.get("key", "").lower() in show or l.get("label", "").lower() in show]
    # one limit (the 5h session by default) is rendered large; the rest compact
    fkey = str(p.get("feature", "session")).lower()
    feat = next((l for l in limits
                 if l.get("key", "").lower() == fkey or l.get("label", "").lower() == fkey), None)
    if feat is not None:
        yy = _claude_feature(img, d, x, yy, w, feat)
    for l in limits:
        if l is feat:
            continue
        if yy + 11 > y + h:
            break
        yy = _claude_compact(img, d, x, yy, w, l)


def _claude_pct(l) -> float:
    try:
        return max(0.0, min(100.0, float(l.get("percent") or 0)))
    except (TypeError, ValueError):
        return 0.0


def _claude_feature(img, d, x, yy, w, l) -> int:
    """Big, prominent block for the featured limit: large %, tall bar, reset."""
    pct = _claude_pct(l)
    col = _sev(pct)
    rs = _countdown(l.get("resets_at"))
    draw_text(img, x, yy, str(l.get("label", "?")), fill=WHITE if l.get("is_active", True) else DIM, scale=1)
    if rs:
        draw_text(img, x + w - text_width(rs, 1), yy, rs, fill=DIM, scale=1)
    yy += 8
    pcs = f"{int(round(pct))}%"
    draw_text(img, x, yy, pcs, fill=col, scale=2)          # big percentage
    pw = text_width(pcs, 2)
    bx0, bx1 = x + pw + 4, x + w
    if bx1 > bx0 + 2:
        d.rectangle([bx0, yy + 1, bx1 - 1, yy + 8], outline=GRAY)   # taller bar
        fw = int((bx1 - bx0 - 2) * pct / 100.0)
        if fw > 0:
            d.rectangle([bx0 + 1, yy + 2, bx0 + fw, yy + 7], fill=col)
    return yy + 13


def _claude_compact(img, d, x, yy, w, l) -> int:
    pct = _claude_pct(l)
    pcs = f"{int(round(pct))}%"
    draw_text(img, x, yy, fit_text(str(l.get("label", "?")), w - text_width(pcs, 1) - 2, 1),
              fill=WHITE if l.get("is_active", True) else DIM, scale=1)
    draw_text(img, x + w - text_width(pcs, 1), yy, pcs, fill=_sev(pct), scale=1)
    by = yy + 7
    rs = _countdown(l.get("resets_at"))
    rw = text_width(rs, 1) if rs else 0
    if rs:
        draw_text(img, x + w - rw, by, rs, fill=DIM, scale=1)
    bx1 = x + w - (rw + 3 if rs else 0)
    if bx1 > x:
        d.rectangle([x, by, bx1 - 1, by + 3], outline=GRAY)
        fw = int((bx1 - x - 2) * pct / 100.0)
        if fw > 0:
            d.rectangle([x + 1, by + 1, x + fw, by + 2], fill=_sev(pct))
    return yy + 12


def slide_seconds(wdef: dict, ctx) -> float:
    """How many seconds a widget needs to show ALL its slides once (0 if it isn't
    a slideshow). The playlist uses this to hold a screen until its slides finish."""
    if wdef.get("type") != "market":
        return 0.0
    data = _load_data(wdef.get("path") or wdef.get("source") or "market")
    items = (data or {}).get("items", []) if isinstance(data, dict) else []
    want = [s.upper() for s in _as_list(wdef.get("symbols"))]
    if want:
        items = [it for it in items if str(it.get("symbol", "")).upper() in want]
    n = len(items)
    if n <= 1:
        return 0.0
    rot = max(1, int(wdef.get("rotate_seconds", 4)))
    if wdef.get("view", "rows") == "card":
        return n * rot
    # rows: paginate by available height
    h = int(wdef.get("h", 32)) - (7 if wdef.get("title") else 0)
    maxrows = max(1, h // 7)
    pages = (n + maxrows - 1) // maxrows
    return pages * rot if pages > 1 else 0.0


def render_widget(img, d, wdef: dict, ctx: RenderContext):
    fn = REGISTRY.get(wdef.get("type"))
    if not fn:
        return
    region = (int(wdef.get("x", 0)), int(wdef.get("y", 0)),
              int(wdef.get("w", 64)), int(wdef.get("h", 32)))
    try:
        fn(img, d, region, wdef, ctx)
    except Exception as e:  # one bad widget must never break the screen
        log.error("widget %s failed: %s", wdef.get("type"), e)


# metadata for the editor (types + their editable props)
WIDGET_SCHEMA = {
    "text":    {"props": ["text", "color", "scale", "align"]},
    "clock":   {"props": ["tz", "format", "scale", "color", "label"]},
    "date":    {"props": ["tz", "format", "scale", "color"]},
    "sysbars": {"props": ["metrics"]},
    "metric":  {"props": ["source", "label", "color"]},
    "list":    {"props": ["title", "source", "url", "fields", "items"]},
    "market":  {"props": ["title", "view", "currency", "symbols", "rotate_seconds"]},
    "claude":  {"props": ["title", "feature", "view", "show"]},
    "kv":      {"props": ["title", "source", "url", "path?", "fields"]},
    "bar":     {"props": ["label", "source", "value", "url", "path", "color"]},
    "rect":    {"props": ["fill", "outline"]},
    "line":    {"props": ["color"]},
}
