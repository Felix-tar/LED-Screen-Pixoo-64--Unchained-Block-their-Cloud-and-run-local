"""Flask web UI + REST API. Also hosts the supervisor thread + shared transport.

LAN-only by design (bind host from config; never expose to WAN). Optional HTTP
basic auth guards every route. Secrets are never sent to the browser.

Run as:  python -m web.app
"""
from __future__ import annotations

import functools
import hmac
import json
import os
import re

from flask import Flask, Response, jsonify, render_template, request
from werkzeug.serving import make_server

from common import status
from common.config import load as load_config
from common.logutil import get_logger
from common.secrets import read_secret

log = get_logger("web")


def _want_auth(cfg):
    return bool(cfg.get("web", "authentication_enabled", default=True))


def _check_basic_auth(cfg) -> bool:
    if not _want_auth(cfg):
        return True
    auth = request.authorization
    if not auth:
        return False
    user = cfg.get("web", "auth_username", default="pixoo")
    try:
        pw = read_secret(cfg.secret_path(cfg.get("web", "auth_password_file")), required=False)
    except Exception:
        pw = ""
    if not pw:
        # no password provisioned -> deny (fail closed) when auth is enabled
        return False
    return hmac.compare_digest(auth.username or "", user) and hmac.compare_digest(
        auth.password or "", pw
    )


def create_app(cfg, supervisor, renderer) -> Flask:
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    app.config["JSON_SORT_KEYS"] = False

    def require_auth(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            if not _check_basic_auth(cfg):
                return Response(
                    "authentication required",
                    401,
                    {"WWW-Authenticate": 'Basic realm="pixoo-local"'},
                )
            return fn(*a, **kw)

        return wrapper

    def _post_int(field, lo, hi):
        data = request.get_json(silent=True) or request.form
        raw = data.get(field)
        if raw is None:
            raise ValueError(f"missing field {field!r}")
        value = int(raw)
        if not (lo <= value <= hi):
            raise ValueError(f"{field} out of range {lo}..{hi}")
        return value

    # -- pages -----------------------------------------------------------
    @app.get("/")
    @require_auth
    def index():
        return render_template(
            "index.html",
            web_port=cfg.get("web", "listen_port", default=8090),
            advertise_ip=cfg.advertise_ip,
            pixoo_ip=cfg.pixoo_ip,
        )

    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "service": "pixoo-web"})

    # -- status / preview ------------------------------------------------
    @app.get("/api/status")
    @require_auth
    def api_status():
        agg = status.read_all()
        sup = agg.get("supervisor", {})
        hb = agg.get("heartbeat", {})
        boot = agg.get("bootstrap", {})
        out = {
            "pixoo_ip": cfg.pixoo_ip,
            "advertise_ip": cfg.advertise_ip,
            "state": sup.get("state", "UNKNOWN"),
            "ping": sup.get("ping"),
            "http_api": sup.get("http_api"),
            "mqtt_connected": bool(hb.get("mqtt_connected")),
            "heartbeats_answered": hb.get("heartbeats_answered"),
            "last_heartbeat": hb.get("last_heartbeat_iso"),
            "last_bootstrap_request": boot.get("last_request_iso"),
            "bootstrap_count": boot.get("count"),
            "last_frame_sent": sup.get("last_frame_sent"),
            "transport": sup.get("transport"),
            "brightness": sup.get("brightness"),
        }
        return jsonify(out)

    @app.get("/api/config")
    @require_auth
    def api_config():
        # sanitized: never expose secret VALUES, only non-sensitive settings
        return jsonify(
            {
                "network": {
                    "pixoo_ip": cfg.pixoo_ip,
                    "pixoo_mac": cfg.pixoo_mac,
                    "advertise_ip": cfg.advertise_ip,
                    "dns_ip": cfg.get("network", "dns_ip"),
                },
                "bootstrap": {
                    "device_id": cfg.get("bootstrap", "device_id"),
                    "allowed_hosts": cfg.get("bootstrap", "allowed_hosts"),
                    "timezone_code": cfg.get("bootstrap", "timezone_code"),
                },
                "mqtt": {
                    "device_username": cfg.get("mqtt", "device_username"),
                    "server_username": cfg.get("mqtt", "server_username"),
                    "topic_prefix": cfg.topic_prefix,
                    "port": cfg.get("mqtt", "port"),
                },
                "display": {
                    "transport": cfg.get("display", "transport"),
                    "default_brightness": cfg.get("display", "default_brightness"),
                },
            }
        )

    @app.get("/api/preview.png")
    @require_auth
    def api_preview():
        try:
            png = renderer.render_png(scale=int(request.args.get("scale", 6)))
        except Exception as e:
            log.error("preview render failed: %s", e)
            return Response(b"", 500)
        return Response(png, mimetype="image/png", headers={"Cache-Control": "no-store"})

    # -- actions ---------------------------------------------------------
    @app.post("/api/brightness")
    @require_auth
    def api_brightness():
        try:
            value = _post_int("value", 0, 100)
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        try:
            supervisor.set_brightness(value)
            return jsonify({"ok": True, "brightness": value})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 502

    @app.post("/api/screen/on")
    @require_auth
    def api_screen_on():
        try:
            supervisor.screen_on()
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 502

    @app.post("/api/screen/off")
    @require_auth
    def api_screen_off():
        try:
            supervisor.screen_off()
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 502

    # -- screen on/off schedule (data/schedule.json, config.yaml = default) --
    _sched_path = os.path.join(os.environ.get("PIXOO_DATA_DIR", "/opt/pixoo-local/data"),
                               "schedule.json")
    _WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    _TIME_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")

    @app.get("/api/schedule")
    @require_auth
    def get_schedule():
        try:
            with open(_sched_path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {"enabled": bool(cfg.get("schedule", "enabled", default=False)),
                    "timezone": cfg.get("schedule", "timezone", default="Europe/Berlin"),
                    "days": cfg.get("schedule", "days", default={}) or {}}
        return jsonify(data)

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
            supervisor.reload_schedule()
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
        return jsonify({"ok": True, **out})

    @app.get("/api/smarthome")
    @require_auth
    def get_smarthome():
        hc = cfg.get("homeassistant", default={}) or {}
        node = hc.get("node_id", "pixoo64")
        port = cfg.get("web", "listen_port", default=8090)
        return jsonify({
            "control_url": f"http://{cfg.advertise_ip}:{port}/",
            "ha_enabled": bool(hc.get("enabled", False)),
            "entity_name": hc.get("device_name", "Pixoo Screen"),
            "entity_id": "switch.pixoo_screen",
            "mqtt": {"host": hc.get("mqtt_host", "10.10.20.50"), "port": hc.get("mqtt_port", 1883)},
            "topics": {"command": f"pixoo/{node}/screen/set",
                       "state": f"pixoo/{node}/screen/state"},
        })

    @app.post("/api/test-pattern")
    @require_auth
    def api_test_pattern():
        ok = supervisor.send_test_pattern()
        return jsonify({"ok": ok}), (200 if ok else 502)

    @app.post("/api/dashboard/push")
    @require_auth
    def api_dashboard_push():
        ok = supervisor.push_dashboard(force=True)
        return jsonify({"ok": ok}), (200 if ok else 502)

    @app.post("/api/reconnect")
    @require_auth
    def api_reconnect():
        supervisor.reconnect()
        return jsonify({"ok": True})

    # -- smart-home hooks (section 18) -----------------------------------
    @app.post("/api/hooks/power-on")
    @require_auth
    def api_power_on():
        supervisor.power_on()
        return jsonify({"ok": True})

    @app.post("/api/hooks/power-off")
    @require_auth
    def api_power_off():
        supervisor.power_off()
        return jsonify({"ok": True})

    return app


def main():
    cfg = load_config()
    from controller.transport import PixooTransport
    from controller.supervisor import Supervisor

    # "screens" = the widget playlist (config/screens.json, editable in the editor);
    # "legacy" = the fixed two-half DashboardRenderer.
    if cfg.get("dashboard", "mode", default="screens") == "legacy":
        from dashboard.renderer import DashboardRenderer
        renderer = DashboardRenderer(cfg)
    else:
        from dashboard.screens import PlaylistRenderer
        renderer = PlaylistRenderer(cfg)
    transport = PixooTransport(cfg)
    frame_provider = renderer.render if bool(cfg.get("dashboard", "enabled", default=True)) else None
    supervisor = Supervisor(cfg, transport, frame_provider=frame_provider)
    supervisor.start()

    app = create_app(cfg, supervisor, renderer)
    host = cfg.get("web", "listen_host", default="0.0.0.0")
    port = int(cfg.get("web", "listen_port", default=8090))
    log.info("web UI on http://%s:%s  (reach it at http://%s:%s)", host, port, cfg.advertise_ip, port)
    srv = make_server(host, port, app, threaded=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        supervisor.stop()
        transport.close()


if __name__ == "__main__":
    main()
