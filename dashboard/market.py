"""Market / stock data for the lower dashboard half.

No hard dependency on any specific external API. Providers:
  * "demo"   — returns the static symbols from config.yaml (default).
  * "plugin" — imports dashboard.market_plugins.<name>:get_quotes() -> list.

Each quote is {"label": str, "value": str, "change": float|None}. ``change`` (if
present) decides the arrow + colour; otherwise the sign in ``value`` is parsed.
A failing provider returns the last good data (or demo data) so it never blocks
the dashboard.
"""
from __future__ import annotations

import importlib
import time

from common.logutil import get_logger

log = get_logger("dashboard.market")

_cache = {"ts": 0.0, "data": None}


def _parse_change(quote: dict) -> float | None:
    if quote.get("change") is not None:
        try:
            return float(quote["change"])
        except (TypeError, ValueError):
            pass
    val = str(quote.get("value", "")).strip()
    try:
        cleaned = val.replace("%", "").replace("+", "").replace(",", ".")
        return float(cleaned)
    except ValueError:
        return None


def _demo(cfg) -> list[dict]:
    symbols = cfg.get("dashboard", "market", "symbols") or []
    out = []
    for s in symbols:
        q = {"label": str(s.get("label", "?")), "value": str(s.get("value", "")).strip()}
        q["change"] = _parse_change(q)
        out.append(q)
    return out


def _plugin(cfg) -> list[dict]:
    name = cfg.get("dashboard", "market", "plugin", default="")
    if not name:
        raise ValueError("market provider is 'plugin' but no plugin name configured")
    mod = importlib.import_module(f"dashboard.market_plugins.{name}")
    quotes = mod.get_quotes(cfg)  # type: ignore[attr-defined]
    for q in quotes:
        q.setdefault("change", _parse_change(q))
    return list(quotes)


def get_quotes(cfg, *, max_items: int = 4) -> list[dict]:
    provider = cfg.get("dashboard", "market", "provider", default="demo")
    interval = int(cfg.get("dashboard", "market", "update_seconds", default=300))
    now = time.time()
    if _cache["data"] is not None and (now - _cache["ts"]) < interval:
        return _cache["data"][:max_items]
    try:
        data = _plugin(cfg) if provider == "plugin" else _demo(cfg)
        _cache["data"] = data
        _cache["ts"] = now
    except Exception as e:
        log.error("market provider %r failed: %s (keeping previous data)", provider, e)
        if _cache["data"] is None:
            _cache["data"] = _demo(cfg)
    return _cache["data"][:max_items]
