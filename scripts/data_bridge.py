#!/usr/bin/env python3
"""Host-side data bridge.

The Pixoo stack runs in a Docker macvlan container that, by macvlan design,
CANNOT reach its own parent host (10.10.20.50). So host-local APIs (e.g. the XMR
node monitor on :8420) are unreachable from the widgets. It also lets the whole
LED stack stay WAN-isolated: the Pixoo never talks to the internet — only THIS
host loop does, then it writes plain JSON to /opt/pixoo-local/data/<name>.json,
which is bind-mounted into the container. Widgets read those files (source:file).

Source types (config.yaml -> data_bridge.sources[].type):
  * "url"          (default) — GET a URL, save the body verbatim (e.g. xmr).
  * "market"       — crypto via CoinGecko + stocks via Yahoo, in EUR/USD/% .
  * "claude_usage" — Anthropic OAuth usage limits (5h / weekly), server-side.

Secrets (OAuth token) are read on the host and NEVER written to the output JSON
or the logs. Configured in config.yaml; run via pixoo-databridge.service.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, "/opt/pixoo-local")
import requests  # noqa: E402

from common.config import load as load_config  # noqa: E402
from common.logutil import get_logger  # noqa: E402

log = get_logger("databridge")
DATA_DIR = os.environ.get("PIXOO_DATA_DIR", "/opt/pixoo-local/data")

YAHOO_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
# values a source may emit for "no reading right now" — sticky sources keep the
# last good value instead of showing these.
_PLACEHOLDERS = {"", "-", "–", "—", "n/a", "na", "null", "none", "?", "..."}


def _missing(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip().lower() in _PLACEHOLDERS)


def _derive_monero(d: dict) -> dict:
    """Add easy-to-display connection stats computed from connections.detail
    (per-type/direction counts the kv widget can't aggregate itself)."""
    conns = d.get("connections") or {}
    detail = conns.get("detail") or []
    n_in = sum(1 for c in detail if c.get("direction") == "in")
    n_out = sum(1 for c in detail if c.get("direction") == "out")
    if not detail:  # fall back to the summary fields when detail is absent
        n_in = conns.get("connections_in") or 0
        n_out = conns.get("connections_out") or 0
    types = {}
    for c in detail:
        t = str(c.get("type", "?")).lower()
        types[t] = types.get(t, 0) + 1
    d["nodes_in"] = n_in
    d["nodes_out"] = n_out
    d["nodes_total"] = n_in + n_out
    d["nodes_inout"] = f"{n_in}/{n_out}"
    d["nodes_tor"] = types.get("tor", 0)
    d["nodes_ipv4"] = types.get("ipv4", 0)
    d["nodes_ipv6"] = types.get("ipv6", 0)
    # compact, font-safe (uppercase) uptime, e.g. "51D 7H" (the 3x5 font has no
    # lowercase, so the raw '51d 7h 28m 6s' would lose its letters)
    up = str(d.get("monerod_uptime") or "").upper().split()
    if up:
        d["uptime"] = " ".join(up[:2])
    return d


_DERIVERS = {"monero": _derive_monero}
_fx_cache: dict[str, tuple[float, float]] = {}   # "USDEUR" -> (ts, rate)


# --------------------------------------------------------------------------
def _write_json(name: str, obj) -> None:
    tmp = os.path.join(DATA_DIR, f".{name}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(tmp, os.path.join(DATA_DIR, f"{name}.json"))


def _read_json(name: str):
    try:
        with open(os.path.join(DATA_DIR, f"{name}.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _now() -> float:
    return time.time()


def _human_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


# ---- source: url (verbatim passthrough) ----------------------------------
def fetch_url(s) -> bool:
    name, url = s.get("name"), s.get("url")
    if not name or not url:
        return False
    r = requests.get(url, timeout=6)
    text = r.text
    if s.get("derive") or s.get("sticky"):
        try:
            obj = r.json()
        except Exception:
            obj = None
        if isinstance(obj, dict):
            deriver = _DERIVERS.get(s.get("derive"))
            if deriver:
                try:
                    obj = deriver(obj)
                except Exception as e:
                    log.debug("derive %s failed: %s", s.get("derive"), e)
            if s.get("sticky"):
                # keep the last good value for any field the API momentarily drops
                # or returns null/"" for (e.g. hashrate while the reading is stale)
                merged = dict(_read_json(name) or {})
                for k, v in obj.items():
                    if _missing(v):
                        merged.setdefault(k, v)
                    else:
                        merged[k] = v
                obj = merged
            text = json.dumps(obj)
    tmp = os.path.join(DATA_DIR, f".{name}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, os.path.join(DATA_DIR, f"{name}.json"))
    return True


# ---- source: market (crypto + stocks, EUR/USD/%) -------------------------
def _yahoo_chart(symbol: str, ua: str) -> dict | None:
    """Return {price, prev, currency} for a Yahoo symbol via the crumb-free
    chart endpoint (works with just a browser User-Agent)."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1d&interval=1d"
    r = requests.get(url, timeout=8, headers={"User-Agent": ua, "Accept": "application/json"})
    if r.status_code != 200:
        raise RuntimeError(f"yahoo {symbol} http {r.status_code}")
    meta = (((r.json() or {}).get("chart") or {}).get("result") or [{}])[0].get("meta") or {}
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    cur = (meta.get("currency") or "USD").upper()
    if price is None:
        raise RuntimeError(f"yahoo {symbol}: no price")
    return {"price": float(price), "prev": float(prev) if prev else None, "currency": cur}


def _fx(cur: str, target: str, ua: str) -> float:
    """Rate to convert 1 unit of `cur` into `target` (e.g. USD->EUR)."""
    cur, target = cur.upper(), target.upper()
    if cur == target:
        return 1.0
    key = f"{cur}{target}"
    hit = _fx_cache.get(key)
    if hit and (_now() - hit[0]) < 3600:
        return hit[1]
    rate = _yahoo_chart(f"{cur}{target}=X", ua)["price"]
    _fx_cache[key] = (_now(), rate)
    return rate


def quote_crypto(coin: dict) -> dict:
    """Live one-coin quote (CoinGecko). Raises on failure. Used by the test button."""
    r = requests.get("https://api.coingecko.com/api/v3/simple/price",
                     params={"ids": coin["id"], "vs_currencies": "eur,usd",
                             "include_24hr_change": "true"},
                     timeout=8, headers={"Accept": "application/json"})
    r.raise_for_status()
    d = (r.json() or {}).get(coin["id"])
    if not d:
        raise RuntimeError(f"unknown coin id '{coin['id']}'")
    sym = str(coin.get("symbol", coin["id"])).upper()
    return {"symbol": sym, "name": coin.get("name", sym), "type": "crypto",
            "currency": "USD", "price_usd": d.get("usd"), "price_eur": d.get("eur"),
            "price_native": d.get("usd"), "change_pct": d.get("usd_24h_change")}


def quote_stock(st: dict, ua: str) -> dict:
    """Live one-stock quote (Yahoo + FX). Raises on failure."""
    sym = str(st["symbol"]).strip()
    q = _yahoo_chart(sym, ua)
    cur, price = q["currency"], q["price"]
    change = (price - q["prev"]) / q["prev"] * 100.0 if q["prev"] else None
    return {"symbol": sym, "name": st.get("name", sym), "type": "stock",
            "currency": cur, "price_native": round(price, 4),
            "price_usd": round(price * _fx(cur, "USD", ua), 4),
            "price_eur": round(price * _fx(cur, "EUR", ua), 4), "change_pct": change}


def quote_entry(cfg, entry: dict) -> dict:
    """Fetch a single resolved entry ({type:crypto,id,…} or {type:stock,symbol,…})."""
    ua = (cfg.get("data_bridge", "market", default={}) or {}).get("yahoo_user_agent", YAHOO_UA)
    return quote_crypto(entry) if entry.get("type") == "crypto" else quote_stock(entry, ua)


def fetch_market(s, cfg) -> bool:
    mcfg = cfg.get("data_bridge", "market", default={}) or {}
    ua = mcfg.get("yahoo_user_agent", YAHOO_UA)
    wl_path = mcfg.get("watchlist_file", "/opt/pixoo-local/config/market.json")
    try:
        with open(wl_path, encoding="utf-8") as f:
            wl = json.load(f)
    except Exception as e:
        log.warning("market: watchlist %s unreadable: %s", wl_path, e)
        wl = {}

    # start from last-good so a transient API failure never blanks a row
    prev = {it["symbol"]: it for it in (_read_json(s.get("name", "market")) or {}).get("items", [])}
    items: dict[str, dict] = dict(prev)
    ok_any = False

    # --- crypto via CoinGecko (gives eur, usd and 24h change directly) -----
    coins = [c for c in wl.get("crypto", []) if c.get("id")]
    if coins:
        try:
            ids = ",".join(c["id"] for c in coins)
            r = requests.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": ids, "vs_currencies": "eur,usd", "include_24hr_change": "true"},
                timeout=8, headers={"Accept": "application/json"})
            r.raise_for_status()
            data = r.json()
            for c in coins:
                d = data.get(c["id"])
                if not d:
                    continue
                sym = c.get("symbol", c["id"]).upper()
                items[sym] = {
                    "symbol": sym, "name": c.get("name", sym), "type": "crypto",
                    "currency": "USD",
                    "price_usd": d.get("usd"), "price_eur": d.get("eur"),
                    "price_native": d.get("usd"),
                    "change_pct": d.get("usd_24h_change"),
                }
                ok_any = True
        except Exception as e:
            log.warning("market: coingecko failed: %s (keeping last)", e)

    # --- stocks via Yahoo (native price + FX to eur/usd) -------------------
    for st in wl.get("stocks", []):
        sym = str(st.get("symbol", "")).strip()
        if not sym:
            continue
        try:
            q = _yahoo_chart(sym, ua)
            cur = q["currency"]
            price = q["price"]
            change = None
            if q["prev"]:
                change = (price - q["prev"]) / q["prev"] * 100.0
            items[sym] = {
                "symbol": sym, "name": st.get("name", sym), "type": "stock",
                "currency": cur, "price_native": round(price, 4),
                "price_usd": round(price * _fx(cur, "USD", ua), 4),
                "price_eur": round(price * _fx(cur, "EUR", ua), 4),
                "change_pct": change,
            }
            ok_any = True
        except Exception as e:
            log.warning("market: %s failed: %s (keeping last)", sym, e)

    if not items:
        return False
    # preserve watchlist order (crypto first, then stocks)
    order = [c.get("symbol", "").upper() for c in coins] + \
            [str(st.get("symbol", "")) for st in wl.get("stocks", [])]
    ordered = [items[k] for k in order if k in items] + \
              [v for k, v in items.items() if k not in order]
    _write_json(s.get("name", "market"),
                {"updated": _now(), "updated_human": _human_ts(_now()), "items": ordered})
    return ok_any


# ---- source: claude_usage (Anthropic OAuth limits) -----------------------
def _token_from_credentials(path):
    """Read Claude Code's own OAuth accessToken (has the user:profile scope) from
    its credentials file. Returns (token, expired). READ-ONLY — never refreshed
    or written back, so Claude Code's login is never disturbed."""
    try:
        with open(path, encoding="utf-8") as f:
            o = (json.load(f) or {}).get("claudeAiOauth", {}) or {}
        tok = o.get("accessToken")
        if not tok:
            return None, False
        exp = o.get("expiresAt")  # epoch millis
        expired = bool(exp) and (time.time() * 1000.0 > float(exp) - 60000.0)
        return tok, expired
    except Exception:
        return None, False


def _read_token(cfg, s):
    """Return (token, expired, source). Prefer Claude Code's credentials (right
    scope); fall back to the secret file, then the editor-written file."""
    ccfg = cfg.get("data_bridge", "claude", default={}) or {}
    cred = s.get("credentials_file") or ccfg.get("credentials_file")
    if cred:
        tok, expired = _token_from_credentials(cred)
        if tok:
            return tok, expired, "claude_code"
    secret_name = s.get("token_secret") or ccfg.get("token_secret", "claude-oauth-token")
    try:
        from common.secrets import read_secret
        tok = read_secret(cfg.secret_path(secret_name), required=False)
        if tok:
            return tok, False, "secret"
    except Exception:
        pass
    fb = s.get("token_file_fallback") or ccfg.get(
        "token_file_fallback", os.path.join(DATA_DIR, ".claude-oauth-token"))
    try:
        with open(fb, encoding="utf-8") as f:
            return f.read().strip(), False, "file"
    except Exception:
        return "", False, None


def _map_limit(entry: dict) -> dict | None:
    kind = entry.get("kind")
    label = {"session": "5H", "weekly_all": "WEEK"}.get(kind)
    if kind == "weekly_scoped":
        model = ((entry.get("scope") or {}).get("model") or {}).get("display_name") or "MODEL"
        label = str(model).upper()[:8]
    if not label:
        return None
    return {
        "key": kind if kind != "weekly_scoped" else f"weekly_{label.lower()}",
        "label": label,
        "percent": entry.get("percent"),
        "resets_at": entry.get("resets_at"),
        "is_active": entry.get("is_active", True),
        "severity": entry.get("severity"),
    }


def _claude_soft_fail(name: str, error: str, retry_at: float | None = None) -> bool:
    """On a transient error keep the last good limits (so the screen doesn't blank),
    just flag them stale. Preserve/record a Retry-After deadline so we don't call
    again until it passes (calling while limited only extends the ban)."""
    out = _read_json(name) or {}
    if out.get("limits"):
        out["stale"] = True                       # keep showing last-good limits
    else:
        out.setdefault("ok", False)
        out["configured"] = True
        out.setdefault("updated", _now())
    out["error"] = error
    out["checked"] = _now()
    if retry_at is not None:
        out["retry_after_ts"] = retry_at
    _write_json(name, out)
    return False


def fetch_claude(s, cfg) -> bool:
    name = s.get("name", "claude")
    ccfg = cfg.get("data_bridge", "claude", default={}) or {}
    url = s.get("url") or ccfg.get("url", "https://api.anthropic.com/api/oauth/usage")
    # Honor a prior Retry-After: the oauth/usage endpoint uses long windows and
    # every call while limited RESETS the window, so make NO call until it passes.
    prev = _read_json(name) or {}
    ra = prev.get("retry_after_ts")
    if ra:
        try:
            if _now() < float(ra):
                return True   # still within the server's Retry-After window; no call
        except (TypeError, ValueError):
            pass
    token, expired, source = _read_token(cfg, s)
    if not token:
        _write_json(name, {"ok": False, "configured": False,
                           "error": "no token — log in with Claude Code or set claude-oauth-token",
                           "updated": _now()})
        return False
    if expired:  # Claude Code refreshes its token on next use; wait, keep last-good
        return _claude_soft_fail(name, "token expired (waiting for Claude Code refresh)")
    headers = {
        "Authorization": f"Bearer {token}",
        "anthropic-beta": "oauth-2025-04-20",
        "anthropic-version": "2023-06-01",
        "Accept": "application/json",
    }
    try:
        r = requests.get(url, headers=headers, timeout=8)
    except Exception as e:
        log.warning("claude: request failed: %s", type(e).__name__)  # never log token/url
        return _claude_soft_fail(name, "request failed")
    if r.status_code == 429:
        try:
            wait = int(r.headers.get("Retry-After", "0") or 0)
        except ValueError:
            wait = 0
        wait = max(wait, 300)
        log.warning("claude: 429, honoring Retry-After=%ss (no calls until then)", wait)
        return _claude_soft_fail(name, "rate limited", retry_at=_now() + wait)
    if r.status_code != 200:
        log.warning("claude: http %s", r.status_code)
        return _claude_soft_fail(name, f"http {r.status_code}")
    body = r.json() or {}
    limits: list[dict] = []
    for entry in (body.get("limits") or []):
        m = _map_limit(entry)
        if m:
            limits.append(m)
    if not limits:  # fallback to the older flat schema
        fh, sd = body.get("five_hour") or {}, body.get("seven_day") or {}
        if fh:
            limits.append({"key": "session", "label": "5H",
                           "percent": _pct(fh.get("utilization")),
                           "resets_at": fh.get("resets_at"), "is_active": True})
        if sd:
            limits.append({"key": "weekly_all", "label": "WEEK",
                           "percent": _pct(sd.get("utilization")),
                           "resets_at": sd.get("resets_at"), "is_active": True})
    _write_json(name, {"ok": True, "configured": True, "updated": _now(),
                       "updated_human": _human_ts(_now()), "limits": limits})
    return True


def _pct(util):
    """Old schema uses 0..1 utilization; normalise to 0..100."""
    try:
        v = float(util)
    except (TypeError, ValueError):
        return None
    return round(v * 100.0, 1) if v <= 1.0 else round(v, 1)


# ---- source: time (authoritative internet time; drift/NTP safety net) -----
def fetch_time(s, cfg) -> bool:
    """Fetch true UTC from the internet (host side) and record the offset vs the
    local clock. The Pi is normally NTP-synced so offset~=0; this proves it and
    lets the clock self-heal if NTP ever fails. DST itself is handled by the
    timezone database, not by this."""
    name = s.get("name", "time")
    tcfg = cfg.get("data_bridge", "time", default={}) or {}
    urls = s.get("urls") or tcfg.get("urls", [
        "https://worldtimeapi.org/api/timezone/Etc/UTC",
        "https://timeapi.io/api/Time/current/zone?timeZone=UTC",
    ])
    server_unix = None
    used = None
    for url in urls:
        try:
            t0 = time.time()
            r = requests.get(url, timeout=6, headers={"Accept": "application/json"})
            t1 = time.time()
            if r.status_code != 200:
                continue
            j = r.json()
            if "unixtime" in j:                       # worldtimeapi
                server_unix = float(j["unixtime"]) + (t1 - t0) / 2
            elif "dateTime" in j:                     # timeapi.io (UTC ISO)
                iso = j["dateTime"].split(".")[0] + "+00:00"
                server_unix = datetime.fromisoformat(iso).timestamp() + (t1 - t0) / 2
            if server_unix:
                used = url
                break
        except Exception as e:
            log.debug("time: %s failed: %s", url, e)
    if server_unix is None:
        # last resort: the HTTP Date header from a reliable host (second precision)
        try:
            t0 = time.time()
            r = requests.head("https://www.google.com/", timeout=6)
            t1 = time.time()
            from email.utils import parsedate_to_datetime
            server_unix = parsedate_to_datetime(r.headers["Date"]).timestamp() + (t1 - t0) / 2
            used = "http-date"
        except Exception as e:
            log.warning("time: all sources failed: %s", e)
            return False
    offset = server_unix - time.time()
    _write_json(name, {"ok": True, "updated": _now(), "source": used,
                       "utc": datetime.fromtimestamp(server_unix, timezone.utc).isoformat(),
                       "offset_seconds": round(offset, 2)})
    if abs(offset) > 5:
        log.warning("time: local clock off by %.1fs vs %s (NTP problem?)", offset, used)
    return True


# --------------------------------------------------------------------------
_DISPATCH = {"url": fetch_url, "market": fetch_market,
             "claude_usage": fetch_claude, "time": fetch_time}


def _call(fn, s, cfg) -> bool:
    return fn(s, cfg) if fn is not fetch_url else fn(s)


# ---- custom, user-defined sources (added in the browser editor) ----------
CUSTOM_FILE = os.path.join(DATA_DIR, "custom_sources.json")
_mqtt_clients: dict = {}   # name -> (client, signature)


def _load_custom() -> list:
    try:
        with open(CUSTOM_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else (data.get("sources", []) if isinstance(data, dict) else [])
    except Exception:
        return []


def _mqtt_on_message(name):
    def cb(client, userdata, msg):
        payload = msg.payload.decode("utf-8", "ignore").strip()
        try:
            obj = json.loads(payload)
        except Exception:
            obj = {"value": payload}
        if isinstance(obj, dict):
            obj.setdefault("topic", msg.topic)
            obj["updated"] = _now()
        _write_json(name, obj)
    return cb


def _reconcile_mqtt(mqtt_sources):
    """Keep a live MQTT subscriber per custom mqtt source; each writes its topic's
    latest payload to data/<name>.json for widgets to read."""
    try:
        import paho.mqtt.client as mqtt
    except Exception:
        if mqtt_sources:
            log.warning("custom mqtt sources need paho-mqtt (run data-bridge with the host venv)")
        return
    wanted = {s["name"]: (s.get("mqtt_host", "10.10.20.50"), int(s.get("mqtt_port", 1883)), s["topic"])
              for s in mqtt_sources if s.get("name") and s.get("topic")}
    for name in list(_mqtt_clients):
        if _mqtt_clients[name][1] != wanted.get(name):
            try:
                _mqtt_clients[name][0].loop_stop(); _mqtt_clients[name][0].disconnect()
            except Exception:
                pass
            del _mqtt_clients[name]
    for name, sig in wanted.items():
        if name in _mqtt_clients:
            continue
        host, port, topic = sig
        try:
            try:
                c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
            except Exception:
                c = mqtt.Client()
            c.on_message = _mqtt_on_message(name)
            c.on_connect = lambda cl, u, f, rc, t=topic: cl.subscribe(t, qos=0)
            c.connect(host, port, 30)
            c.loop_start()
            _mqtt_clients[name] = (c, sig)
            log.info("custom mqtt '%s' -> %s:%s %s", name, host, port, topic)
        except Exception as e:
            log.warning("custom mqtt '%s' failed: %s", name, e)


def main():
    cfg = load_config()
    os.makedirs(DATA_DIR, exist_ok=True)
    base_sources = cfg.get("data_bridge", "sources", default=[]) or []
    interval = int(cfg.get("data_bridge", "interval_seconds", default=15))
    log.info("data bridge started: %d source(s) -> %s", len(base_sources), DATA_DIR)
    last: dict[str, float] = {}
    backoff: dict[str, int] = {}
    while True:
        now = _now()
        custom = _load_custom()
        _reconcile_mqtt([c for c in custom if c.get("type") == "mqtt"])
        url_customs = [{"name": c.get("name"), "type": "url", "url": c.get("url"),
                        "every": int(c.get("every", interval)), "sticky": bool(c.get("sticky", False))}
                       for c in custom if c.get("type") == "url" and c.get("name") and c.get("url")]
        for s in base_sources + url_customs:
            name = s.get("name")
            stype = s.get("type", "url")
            if not name:
                continue
            base = int(s.get("every", interval))
            cap = int(s.get("max_every", base))
            adaptive = cap > base
            eff = min(base * backoff.get(name, 1), cap) if adaptive else base
            if (now - last.get(name, 0)) < eff:
                continue
            fn = _DISPATCH.get(stype)
            if not fn:
                log.warning("unknown source type %r for %s", stype, name)
                last[name] = now
                continue
            ok = False
            try:
                ok = bool(_call(fn, s, cfg))
            except Exception as e:
                log.warning("bridge %s (%s) failed: %s", name, stype, e)
            last[name] = now
            if adaptive:  # fast when it works, back off on repeated failure, then recover
                backoff[name] = 1 if ok else min(backoff.get(name, 1) * 2, max(1, cap // base))
        time.sleep(5)


if __name__ == "__main__":
    main()
