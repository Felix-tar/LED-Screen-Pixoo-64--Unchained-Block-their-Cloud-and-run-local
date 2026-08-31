"""Browser-based screen editor (own port, default 8091).

Build/rearrange the widget screens and their rotation live in the browser; saves
go to config/screens.json, which the running renderer reloads automatically, so
the Pixoo updates without a restart. LAN-only, optional basic auth (same
credentials as the main web UI).

Run as:  python -m web.editor
"""
from __future__ import annotations

import functools
import json
import os
import re

import requests
from flask import Flask, Response, jsonify, render_template, request
from werkzeug.serving import make_server

from common.config import load as load_config
from common.logutil import get_logger
from common.secrets import secret_present
from controller.pixoo_http import PixooHttp
from dashboard.screens import PlaylistRenderer, validate_playlist
from dashboard.widgets import WIDGET_SCHEMA
from web.app import _check_basic_auth  # reuse the same auth

log = get_logger("editor")

# Common crypto tickers -> CoinGecko id, so a plain symbol line resolves without
# a lookup. Anything not here (and not a dotted stock ticker) is resolved via the
# CoinGecko search API; if still not a coin, it is treated as a stock ticker.
CRYPTO_MAP = {
    "BTC": ("bitcoin", "Bitcoin"), "ETH": ("ethereum", "Ethereum"),
    "XMR": ("monero", "Monero"), "SOL": ("solana", "Solana"),
    "USDT": ("tether", "Tether"), "USDC": ("usd-coin", "USD Coin"),
    "BNB": ("binancecoin", "BNB"), "XRP": ("ripple", "XRP"),
    "ADA": ("cardano", "Cardano"), "DOGE": ("dogecoin", "Dogecoin"),
    "TRX": ("tron", "TRON"), "DOT": ("polkadot", "Polkadot"),
    "MATIC": ("matic-network", "Polygon"), "LTC": ("litecoin", "Litecoin"),
    "SHIB": ("shiba-inu", "Shiba Inu"), "AVAX": ("avalanche-2", "Avalanche"),
    "LINK": ("chainlink", "Chainlink"), "ATOM": ("cosmos", "Cosmos"),
    "XLM": ("stellar", "Stellar"), "UNI": ("uniswap", "Uniswap"),
    "ETC": ("ethereum-classic", "Ethereum Classic"), "ALGO": ("algorand", "Algorand"),
    "BCH": ("bitcoin-cash", "Bitcoin Cash"), "NEAR": ("near", "NEAR"),
    "APT": ("aptos", "Aptos"), "FIL": ("filecoin", "Filecoin"),
    "ICP": ("internet-computer", "Internet Computer"), "HBAR": ("hedera-hashgraph", "Hedera"),
    "VET": ("vechain", "VeChain"), "ARB": ("arbitrum", "Arbitrum"),
    "OP": ("optimism", "Optimism"), "AAVE": ("aave", "Aave"),
    "SUI": ("sui", "Sui"), "TON": ("the-open-network", "Toncoin"),
    "KAS": ("kaspa", "Kaspa"), "INJ": ("injective-protocol", "Injective"),
    "TIA": ("celestia", "Celestia"), "PEPE": ("pepe", "Pepe"),
    "FTM": ("fantom", "Fantom"), "XTZ": ("tezos", "Tezos"),
    "RNDR": ("render-token", "Render"), "GRT": ("the-graph", "The Graph"),
    "SAND": ("the-sandbox", "The Sandbox"), "MANA": ("decentraland", "Decentraland"),
    "EGLD": ("elrond-erd-2", "MultiversX"), "THETA": ("theta-token", "Theta"),
    "EOS": ("eos", "EOS"), "FLOW": ("flow", "Flow"), "CHZ": ("chiliz", "Chiliz"),
    "CRV": ("curve-dao-token", "Curve"), "LDO": ("lido-dao", "Lido DAO"),
    "MKR": ("maker", "Maker"), "SNX": ("havven", "Synthetix"),
    "GALA": ("gala", "Gala"), "AXS": ("axie-infinity", "Axie Infinity"),
    "STX": ("blockstack", "Stacks"), "ZEC": ("zcash", "Zcash"),
    "DASH": ("dash", "Dash"), "WIF": ("dogwifcoin", "dogwifhat"),
    "BONK": ("bonk", "Bonk"), "JUP": ("jupiter-exchange-solana", "Jupiter"),
}


def _resolve_watchlist_lines(text: str):
    """Turn a plain 'one instrument per line' text into {crypto,stocks} + a
    per-line classification. Crypto via CRYPTO_MAP / CoinGecko search; a dotted
    ticker (SAP.DE) or anything unresolved is a stock. Prefix c:/s: forces it."""
    crypto, stocks, resolved = [], [], []
    for raw in (text or "").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        forced = None
        if s[:2].lower() in ("c:", "s:"):
            forced, s = s[0].lower(), s[2:].strip()
        up = s.upper()
        if forced == "s":
            stocks.append({"symbol": s, "name": s}); resolved.append({"line": s, "type": "stock"}); continue
        if forced == "c" or up in CRYPTO_MAP:
            cid, name = CRYPTO_MAP.get(up, (s.lower(), s))
            crypto.append({"id": cid, "symbol": up if up in CRYPTO_MAP else s.upper(), "name": name})
            resolved.append({"line": s, "type": "crypto", "id": cid}); continue
        if "." in s:  # exchange-suffixed ticker like SAP.DE / ASML.AS
            stocks.append({"symbol": s, "name": s}); resolved.append({"line": s, "type": "stock"}); continue
        # a bare UPPERCASE short ticker not in the crypto map is a stock (avoids
        # memecoins that squat on equity tickers, e.g. TSLA). Crypto beyond the
        # map is added by lowercase name/id (bitcoin, cardano) or the c: prefix.
        if s.isalpha() and s.isupper() and len(s) <= 5:
            stocks.append({"symbol": s, "name": s}); resolved.append({"line": s, "type": "stock"}); continue
        # ambiguous -> ask CoinGecko, but exclude tokenized-stock tokens (their
        # symbols collide with real equities) and require a real market-cap rank,
        # so a bare ticker like AAPL/NVDA correctly defaults to a stock.
        hit = None
        try:
            r = requests.get("https://api.coingecko.com/api/v3/search",
                             params={"query": s}, timeout=6)
            coins = (r.json() or {}).get("coins", []) if r.status_code == 200 else []

            def _real(c):
                blob = f"{c.get('id','')} {c.get('name','')}".lower()
                bad = ("tokenized", "robinhood", "xstock", "-stock", "wrapped ")
                return c.get("market_cap_rank") and not any(b in blob for b in bad)
            real = [c for c in coins if _real(c)]
            exact = sorted([c for c in real if str(c.get("symbol", "")).upper() == up],
                           key=lambda c: c["market_cap_rank"])
            named = sorted([c for c in real if str(c.get("name", "")).upper() == up],
                           key=lambda c: c["market_cap_rank"])
            hit = (exact or named or [None])[0]
        except Exception as e:
            log.debug("coingecko search %s failed: %s", s, e)
        if hit:
            crypto.append({"id": hit["id"], "symbol": str(hit.get("symbol", up)).upper(),
                           "name": hit.get("name", s)})
            resolved.append({"line": s, "type": "crypto", "id": hit["id"]})
        else:
            stocks.append({"symbol": s, "name": s})
            resolved.append({"line": s, "type": "stock"})
    return {"crypto": crypto, "stocks": stocks}, resolved

# City-labelled timezones for the editor dropdown (label -> IANA zone). Picking a
# city auto-fills the clock's label. Add freely — any IANA zone works.
TIMEZONES = [
    {"label": "UTC", "tz": "UTC"},
    {"label": "London", "tz": "Europe/London"},
    {"label": "Berlin", "tz": "Europe/Berlin"},
    {"label": "Paris", "tz": "Europe/Paris"},
    {"label": "Madrid", "tz": "Europe/Madrid"},
    {"label": "Rome", "tz": "Europe/Rome"},
    {"label": "Amsterdam", "tz": "Europe/Amsterdam"},
    {"label": "Zurich", "tz": "Europe/Zurich"},
    {"label": "Istanbul", "tz": "Europe/Istanbul"},
    {"label": "Moscow", "tz": "Europe/Moscow"},
    {"label": "Dubai", "tz": "Asia/Dubai"},
    {"label": "Mumbai", "tz": "Asia/Kolkata"},
    {"label": "Bangkok", "tz": "Asia/Bangkok"},
    {"label": "Singapore", "tz": "Asia/Singapore"},
    {"label": "Hong Kong", "tz": "Asia/Hong_Kong"},
    {"label": "Shanghai", "tz": "Asia/Shanghai"},
    {"label": "Tokyo", "tz": "Asia/Tokyo"},
    {"label": "Seoul", "tz": "Asia/Seoul"},
    {"label": "Sydney", "tz": "Australia/Sydney"},
    {"label": "Auckland", "tz": "Pacific/Auckland"},
    {"label": "Anchorage", "tz": "America/Anchorage"},
    {"label": "Honolulu", "tz": "Pacific/Honolulu"},
    {"label": "Los Angeles", "tz": "America/Los_Angeles"},
    {"label": "Las Vegas", "tz": "America/Los_Angeles"},
    {"label": "Seattle", "tz": "America/Los_Angeles"},
    {"label": "Phoenix", "tz": "America/Phoenix"},
    {"label": "Boise", "tz": "America/Boise"},
    {"label": "Denver", "tz": "America/Denver"},
    {"label": "Chicago", "tz": "America/Chicago"},
    {"label": "New York", "tz": "America/New_York"},
    {"label": "Toronto", "tz": "America/Toronto"},
    {"label": "Mexico City", "tz": "America/Mexico_City"},
    {"label": "Sao Paulo", "tz": "America/Sao_Paulo"},
]
SOURCES = {
    "list": ["crypto", "http", "static"],
    "kv": ["file", "http"],
    "bar": ["value", "cpu", "ram", "disk", "http"],
    "metric": ["cpu", "ram", "disk", "temp"],
}
# option lists for select-style widget props
PROP_OPTIONS = {
    "view": {"market": ["rows", "card"], "claude": ["bars"]},
    "currency": {"market": ["usd", "eur"]},
}


def create_editor(cfg, renderer: PlaylistRenderer) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["JSON_SORT_KEYS"] = False

    def require_auth(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            if not _check_basic_auth(cfg):
                return Response("auth required", 401,
                                {"WWW-Authenticate": 'Basic realm="pixoo-editor"'})
            return fn(*a, **kw)
        return wrapper

    @app.get("/")
    @require_auth
    def index():
        return render_template("editor.html", advertise_ip=cfg.advertise_ip)

    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "service": "pixoo-editor"})

    # data-bridge / secret paths (from config, with sane defaults)
    data_dir = os.environ.get("PIXOO_DATA_DIR", "/opt/pixoo-local/data")
    watchlist_file = cfg.get("data_bridge", "market", "watchlist_file",
                             default="/opt/pixoo-local/config/market.json")
    claude_secret = cfg.secret_path(
        cfg.get("data_bridge", "claude", "token_secret", default="claude-oauth-token"))
    claude_fallback = cfg.get("data_bridge", "claude", "token_file_fallback",
                              default=os.path.join(data_dir, ".claude-oauth-token"))
    claude_creds = cfg.get("data_bridge", "claude", "credentials_file", default=None)

    @app.get("/api/schema")
    @require_auth
    def schema():
        return jsonify({
            "widgets": WIDGET_SCHEMA,
            "timezones": TIMEZONES,
            "sources": SOURCES,
            "prop_options": PROP_OPTIONS,
            "clock_formats": ["HH:MM", "HH:MM:SS", "H:MM"],
            "date_formats": ["DD.MM", "DD.MM.YY", "WD DD.MM"],
            "sys_metrics": ["cpu", "ram", "disk", "temp"],
        })

    # ---- market watchlist (read/write config/market.json) ----------------
    @app.get("/api/market")
    @require_auth
    def get_market():
        try:
            with open(watchlist_file, encoding="utf-8") as f:
                wl = json.load(f)
        except Exception:
            wl = {"crypto": [], "stocks": []}
        return jsonify({"crypto": wl.get("crypto", []), "stocks": wl.get("stocks", [])})

    @app.put("/api/market")
    @require_auth
    def put_market():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"ok": False, "error": "invalid JSON"}), 400
        wl = {"crypto": [], "stocks": []}
        for c in data.get("crypto", []) or []:
            if isinstance(c, dict) and c.get("id"):
                wl["crypto"].append({"id": str(c["id"]).strip().lower(),
                                     "symbol": str(c.get("symbol", c["id"])).strip().upper(),
                                     "name": str(c.get("name", c["id"])).strip()})
        for s in data.get("stocks", []) or []:
            if isinstance(s, dict) and s.get("symbol"):
                wl["stocks"].append({"symbol": str(s["symbol"]).strip(),
                                     "name": str(s.get("name", s["symbol"])).strip()})
        try:
            tmp = watchlist_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(wl, f, indent=2)
            os.replace(tmp, watchlist_file)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
        return jsonify({"ok": True, **wl})

    # ---- market watchlist as a plain line editor (one per line) ----------
    @app.get("/api/market/lines")
    @require_auth
    def get_market_lines():
        try:
            with open(watchlist_file, encoding="utf-8") as f:
                wl = json.load(f)
        except Exception:
            wl = {}
        lines = [c.get("symbol") or c.get("id") for c in wl.get("crypto", []) if isinstance(c, dict)]
        lines += [s.get("symbol") for s in wl.get("stocks", []) if isinstance(s, dict)]
        return jsonify({"lines": "\n".join(x for x in lines if x)})

    @app.post("/api/market/lines")
    @require_auth
    def put_market_lines():
        data = request.get_json(silent=True) or {}
        wl, resolved = _resolve_watchlist_lines(str(data.get("lines", "")))
        try:
            tmp = watchlist_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(wl, f, indent=2)
            os.replace(tmp, watchlist_file)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
        return jsonify({"ok": True, "resolved": resolved,
                        "crypto": len(wl["crypto"]), "stocks": len(wl["stocks"])})

    # ---- resolve a symbol to its type/id (for the watchlist manager) -----
    @app.post("/api/market/resolve")
    @require_auth
    def market_resolve():
        sym = str((request.get_json(silent=True) or {}).get("symbol", "")).strip()
        if not sym:
            return jsonify({"ok": False, "error": "symbol required"}), 400
        wl, _ = _resolve_watchlist_lines(sym)
        if wl["crypto"]:
            return jsonify({"ok": True, "type": "crypto", "entry": wl["crypto"][0]})
        if wl["stocks"]:
            return jsonify({"ok": True, "type": "stock", "entry": wl["stocks"][0]})
        return jsonify({"ok": False, "error": "could not resolve"})

    # ---- available JSON paths for a kv/list widget (field picker) --------
    @app.post("/api/kv/paths")
    @require_auth
    def kv_paths():
        body = request.get_json(silent=True) or {}
        src, path, url = body.get("source", "file"), body.get("path", ""), body.get("url", "")
        data = None
        try:
            if src == "file" and path:
                ap = os.path.abspath(path)
                if ap.startswith("/opt/pixoo-local/"):     # never read outside the project
                    with open(ap, encoding="utf-8") as f:
                        data = json.load(f)
            elif src == "http" and url:
                data = requests.get(url, timeout=4).json()
        except Exception:
            data = None
        out = []
        if isinstance(data, dict):
            def walk(o, pre="", depth=0):
                for k, v in o.items():
                    p = f"{pre}{k}"
                    if isinstance(v, dict) and depth < 2:
                        walk(v, p + ".", depth + 1)
                    elif isinstance(v, (str, int, float, bool)) or v is None:
                        out.append({"path": p, "sample": v})
            walk(data)
        return jsonify({"paths": out[:120]})

    # ---- test a single symbol live (does it return a value?) -------------
    @app.post("/api/market/test")
    @require_auth
    def market_test():
        sym = str((request.get_json(silent=True) or {}).get("symbol", "")).strip()
        if not sym:
            return jsonify({"ok": False, "error": "symbol required"}), 400
        wl, _ = _resolve_watchlist_lines(sym)
        if wl["crypto"]:
            entry = {**wl["crypto"][0], "type": "crypto"}
        elif wl["stocks"]:
            entry = {**wl["stocks"][0], "type": "stock"}
        else:
            return jsonify({"ok": False, "error": "could not resolve symbol"})
        try:
            import scripts.data_bridge as db
            item = db.quote_entry(cfg, entry)
            return jsonify({"ok": True, "type": entry["type"], "item": item})
        except Exception as e:
            return jsonify({"ok": False, "type": entry["type"], "error": str(e)})

    # ---- brightness (live SetBrightness + persist default) ---------------
    @app.get("/api/brightness")
    @require_auth
    def get_brightness():
        return jsonify({"value": int(cfg.get("display", "default_brightness", default=30))})

    @app.post("/api/brightness")
    @require_auth
    def set_brightness():
        data = request.get_json(silent=True) or {}
        try:
            value = max(1, min(100, int(data.get("value"))))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "value 1..100 required"}), 400
        sent, warn = False, None
        try:
            PixooHttp(cfg.http_base_url).set_brightness(value)
            sent = True
        except Exception as e:
            warn = f"device unreachable ({type(e).__name__})"
            log.warning("brightness: %s", warn)
        try:  # persist so it survives reconnects (supervisor reads it on connect)
            with open(cfg.path, encoding="utf-8") as f:
                txt = f.read()
            new = re.sub(r"(?m)^(\s*default_brightness:\s*)\d+", rf"\g<1>{value}", txt)
            if new != txt:
                with open(cfg.path, "w", encoding="utf-8") as f:
                    f.write(new)
        except Exception as e:
            log.warning("brightness persist failed: %s", e)
        return jsonify({"ok": True, "value": value, "sent": sent, "warn": warn})

    # ---- Claude usage token (write-only) + status ------------------------
    @app.get("/api/claude/status")
    @require_auth
    def claude_status():
        # reflect the ACTUAL live result the bridge produced (most useful)
        try:
            d = json.load(open(os.path.join(data_dir, "claude.json")))
            if d.get("ok"):
                return jsonify({"configured": True, "active": True, "source": "claude_code",
                                "limits": len(d.get("limits", [])),
                                "updated": d.get("updated_human")})
            if d.get("configured"):
                return jsonify({"configured": True, "active": False, "source": "token",
                                "error": d.get("error")})
        except Exception:
            pass
        # else fall back to token-presence
        src = None
        try:
            if claude_creds and os.path.exists(claude_creds):
                o = (json.load(open(claude_creds)) or {}).get("claudeAiOauth", {}) or {}
                if o.get("accessToken"):
                    src = "claude_code"
            if src is None and secret_present(claude_secret):
                src = "secret"
            elif src is None and os.path.exists(claude_fallback) and os.path.getsize(claude_fallback) > 0:
                src = "file"
        except Exception:
            pass
        return jsonify({"configured": src is not None, "active": False, "source": src})

    @app.post("/api/claude/token")
    @require_auth
    def claude_set_token():
        data = request.get_json(silent=True) or {}
        token = str(data.get("token", "")).strip()
        try:
            if not token:  # clear the editor-written fallback
                if os.path.exists(claude_fallback):
                    os.remove(claude_fallback)
                return jsonify({"ok": True, "configured": secret_present(claude_secret)})
            os.makedirs(os.path.dirname(claude_fallback), exist_ok=True)
            fd = os.open(claude_fallback, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(token)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
        # never echo the token back
        return jsonify({"ok": True, "configured": True})

    # ---- live sanitized data view (market / xmr / claude) ----------------
    @app.get("/api/data/<name>")
    @require_auth
    def get_data(name):
        if not re.fullmatch(r"[a-z0-9_]{1,32}", name or ""):
            return jsonify({"error": "bad name"}), 400
        try:
            with open(os.path.join(data_dir, f"{name}.json"), encoding="utf-8") as f:
                return Response(f.read(), mimetype="application/json")
        except Exception:
            return jsonify({"error": "no data yet"}), 404

    # ---- screen on/off schedule (data/schedule.json) ---------------------
    _sched_path = os.path.join(data_dir, "schedule.json")
    _WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    _TIME_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")

    @app.get("/api/schedule")
    @require_auth
    def get_schedule():
        try:
            with open(_sched_path, encoding="utf-8") as f:
                return Response(f.read(), mimetype="application/json")
        except Exception:
            return jsonify({"enabled": bool(cfg.get("schedule", "enabled", default=False)),
                            "timezone": cfg.get("schedule", "timezone", default="Europe/Berlin"),
                            "days": cfg.get("schedule", "days", default={}) or {}})

    @app.put("/api/schedule")
    @require_auth
    def put_schedule():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"ok": False, "error": "invalid JSON"}), 400
        out = {"enabled": bool(body.get("enabled", False)),
               "timezone": str(body.get("timezone", "Europe/Berlin")), "days": {}}
        for d in _WEEKDAYS:
            dc = (body.get("days") or {}).get(d) or {}
            day = {}
            for k in ("on", "off"):
                v = str(dc.get(k, "")).strip()
                if v:
                    if not _TIME_RE.match(v):
                        return jsonify({"ok": False, "error": f"{d}.{k} must be HH:MM"}), 400
                    day[k] = v
            if day:
                out["days"][d] = day
        try:
            tmp = _sched_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(out, f, indent=2)
            os.replace(tmp, _sched_path)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
        return jsonify({"ok": True, **out})

    @app.get("/api/smarthome")
    @require_auth
    def get_smarthome():
        hc = cfg.get("homeassistant", default={}) or {}
        node = hc.get("node_id", "pixoo64")
        return jsonify({
            "control_url": f"http://{cfg.advertise_ip}:{cfg.get('web', 'listen_port', default=8090)}/",
            "ha_enabled": bool(hc.get("enabled", False)),
            "entity_name": hc.get("device_name", "Pixoo Screen"),
            "entity_id": "switch.pixoo_screen",
            "mqtt": {"host": hc.get("mqtt_host", "10.10.20.50"), "port": hc.get("mqtt_port", 1883)},
            "topics": {"command": f"pixoo/{node}/screen/set", "state": f"pixoo/{node}/screen/state"},
        })

    # ---- custom data sources (own MQTT topics / JSON URLs) ---------------
    _custom_path = os.path.join(data_dir, "custom_sources.json")

    @app.get("/api/custom_sources")
    @require_auth
    def get_custom():
        try:
            with open(_custom_path, encoding="utf-8") as f:
                return Response(f.read(), mimetype="application/json")
        except Exception:
            return jsonify([])

    @app.put("/api/custom_sources")
    @require_auth
    def put_custom():
        body = request.get_json(silent=True)
        if not isinstance(body, list):
            return jsonify({"ok": False, "error": "expected a JSON array"}), 400
        out = []
        for c in body:
            if not isinstance(c, dict):
                continue
            name = str(c.get("name", "")).strip()
            if not re.fullmatch(r"[a-z0-9_]{1,32}", name):
                return jsonify({"ok": False, "error": f"bad name {name!r} — use a-z 0-9 _"}), 400
            t = c.get("type")
            if t == "url" and c.get("url"):
                out.append({"name": name, "type": "url", "url": str(c["url"]).strip(),
                            "every": int(c.get("every", 300)), "sticky": bool(c.get("sticky", False))})
            elif t == "mqtt" and c.get("topic"):
                out.append({"name": name, "type": "mqtt",
                            "mqtt_host": str(c.get("mqtt_host", "10.10.20.50")).strip(),
                            "mqtt_port": int(c.get("mqtt_port", 1883)),
                            "topic": str(c["topic"]).strip()})
            else:
                return jsonify({"ok": False, "error": f"{name}: url required (type url) or topic (type mqtt)"}), 400
        try:
            tmp = _custom_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(out, f, indent=2)
            os.replace(tmp, _custom_path)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
        return jsonify({"ok": True, "count": len(out), "sources": out})

    # ---- Homebridge: status + one-click install/activate -----------------
    _HB_CFG = "/var/lib/homebridge/config.json"
    _HB_PLUGIN_DIR = "/var/lib/homebridge/node_modules/homebridge-mqttthing"

    @app.get("/api/homebridge/status")
    @require_auth
    def hb_status():
        import subprocess
        name = cfg.get("homeassistant", "device_name", default="Pixoo Screen")
        available = os.path.exists(_HB_CFG)
        plugin = os.path.isdir(_HB_PLUGIN_DIR)
        configured = False
        try:
            with open(_HB_CFG, encoding="utf-8") as f:
                d = json.load(f)
            configured = any(a.get("name") == name for a in d.get("accessories", []))
        except Exception:
            pass
        running = None
        try:
            running = subprocess.run(["systemctl", "is-active", "homebridge"],
                                     capture_output=True, text=True, timeout=4).stdout.strip() == "active"
        except Exception:
            pass
        return jsonify({"available": available, "plugin": plugin,
                        "configured": configured, "running": running})

    @app.post("/api/homebridge/install")
    @require_auth
    def hb_install():
        import subprocess
        if not os.path.exists(_HB_CFG):
            return jsonify({"ok": False, "error": "Homebridge not found on this host"}), 400
        try:
            r = subprocess.run(["bash", "/opt/pixoo-local/scripts/setup_homebridge.sh"],
                               capture_output=True, text=True, timeout=300)
            return jsonify({"ok": r.returncode == 0, "log": (r.stdout + r.stderr)[-2500:]})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.get("/api/config")
    @require_auth
    def get_config():
        return jsonify(renderer.data())

    @app.put("/api/config")
    @require_auth
    def put_config():
        data = request.get_json(silent=True)
        if data is None:
            return jsonify({"ok": False, "error": "invalid JSON"}), 400
        try:
            validate_playlist(data)
            renderer.save(data)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify({"ok": True})

    @app.route("/api/preview.png", methods=["GET", "POST"])
    @require_auth
    def preview():
        scale = int(request.args.get("scale", 6))
        try:
            if request.method == "POST":
                screen = request.get_json(silent=True) or {}
                png = renderer.render_png(scale=scale, screen=screen)
            else:
                idx = request.args.get("index")
                png = renderer.render_png(scale=scale, index=int(idx) if idx is not None else None)
        except Exception as e:
            log.error("preview failed: %s", e)
            return Response(b"", 500)
        return Response(png, mimetype="image/png", headers={"Cache-Control": "no-store"})

    return app


def main():
    cfg = load_config()
    renderer = PlaylistRenderer(cfg)
    app = create_editor(cfg, renderer)
    host = cfg.get("editor", "listen_host", default="0.0.0.0")
    port = int(cfg.get("editor", "listen_port", default=8091))
    log.info("editor on http://%s:%s (reach at http://%s:%s)", host, port, cfg.advertise_ip, port)
    srv = make_server(host, port, app, threaded=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
