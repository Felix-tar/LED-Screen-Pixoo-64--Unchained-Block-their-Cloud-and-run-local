"""Bootstrap HTTP server: answers Device/InitV2 for the Pixoo on port 80.

Uses the stdlib http.server (no framework) so it stays dependency-light and can
read a request body on any method — the Pixoo sends a GET with a JSON body.
Run as:  python -m bootstrap.app
"""
from __future__ import annotations

import ipaddress
import json
import os
import threading
import time
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from common import status
from common.config import load as load_config
from common.logutil import get_logger, redact
from common.secrets import read_secret
from .core import BootstrapSettings, build_response, extract_mac, is_host_allowed, parse_request_json

log = get_logger("bootstrap")

INIT_PATH = "/Device/InitV2"
HEALTH_PATH = "/health"
# Connectivity probe the firmware polls; a 404 here makes it think it is offline
# and re-run InitV2 in a loop. Note Divoom's field typo "CustonIP" (kept as-is).
TESTIP_PATH = "/Test/GetIP"


def _iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class BootstrapHandler(BaseHTTPRequestHandler):
    server_version = "pixoo-bootstrap/1.0"
    protocol_version = "HTTP/1.1"

    # settings + capture injected as class attrs by make_server()
    settings: BootstrapSettings
    capture = None  # optional callable(raw_body, headers) -> dict|None

    def log_message(self, fmt, *args):  # silence default stderr logging
        pass

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            return b""
        # cap body to a sane size to avoid abuse
        length = min(length, 65536)
        return self.rfile.read(length)

    def _send_json(self, obj: dict, status: int = 200):
        body = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=UTF-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _send_404(self):
        self.send_response(404)
        self.send_header("Content-Type", "application/json; charset=UTF-8")
        self.send_header("Content-Length", "2")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(b"{}")

    # -- routing ---------------------------------------------------------
    def _handle_init(self):
        raw = self._read_body()
        host = self.headers.get("Host", "")
        src = self.client_address[0]
        if not is_host_allowed(host, self.settings):
            log.warning("InitV2 REJECTED bad host=%r src=%s -> 404 (no secrets)", host, src)
            self._send_404()
            return

        req = parse_request_json(raw)
        mac = extract_mac(req)

        # optional one-time capture/proxy fallback
        if self.capture is not None:
            try:
                proxied = self.capture(raw, dict(self.headers))
            except Exception as e:  # never let capture break the boot path
                log.error("capture proxy failed, serving local response: %s", e)
                proxied = None
            if proxied is not None:
                log.info("InitV2 (CAPTURE-PROXY) src=%s mac=%s -> upstream reply served", src, mac)
                self._send_json(proxied)
                return

        now = int(time.time())
        resp = build_response(req, remote_ip=src, settings=self.settings, now_ts=now)
        known = bool(mac and mac == self.settings.configured_mac)

        # publish liveness for the supervisor (BOOTSTRAP_SEEN detection); no secrets
        try:
            st = status.read("bootstrap")
            st.update(
                {
                    "service": "bootstrap",
                    "last_request_ts": now,
                    "last_request_iso": _iso_now(),
                    "last_src": src,
                    "last_mac": mac,
                    "last_known": known,
                    "count": int(st.get("count", 0)) + 1,
                }
            )
            status.write("bootstrap", st)
        except Exception:  # status must never break the boot path
            pass

        log.info(
            "InitV2 src=%s host=%s mac=%s known=%s packetflag=%s -> IP=%s UTCTime=%s DeviceId=%s (token %s)",
            src, host, mac, known, resp["PacketFlag"], resp["IP"], resp["UTCTime"],
            resp["DeviceId"], redact(resp["DeviceToken"]),
        )
        self._send_json(resp)

    def _handle_health(self):
        self._send_json(
            {
                "status": "ok",
                "service": "pixoo-bootstrap",
                "time": _iso_now(),
                "mqtt_host": self.settings.advertise_ip,
            }
        )

    def _handle_test_getip(self):
        # firmware connectivity probe — must return 200 JSON with a PUBLIC IP
        # ("CustonIP" is Divoom's field-name typo). A private IP here makes the
        # firmware believe it is offline and re-run InitV2 in a loop.
        self._read_body()  # drain any body
        ip = self.settings.public_ip or self.client_address[0]
        self._send_json({"ReturnCode": 0, "ReturnMessage": "", "CustonIP": ip})

    def _route(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == INIT_PATH.rstrip("/"):
            self._handle_init()
        elif path == TESTIP_PATH:
            self._handle_test_getip()
        elif path == HEALTH_PATH:
            self._handle_health()
        else:
            # log unknown paths the firmware hits so we can implement them
            log.info("HTTP OTHER %s %s host=%s src=%s clen=%s", self.command, self.path,
                     self.headers.get("Host", ""), self.client_address[0],
                     self.headers.get("Content-Length", "0"))
            self._send_404()

    def do_GET(self):
        self._route()

    def do_POST(self):
        self._route()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()


def build_settings(cfg) -> BootstrapSettings:
    token = read_secret(cfg.secret_path(cfg.get("mqtt", "device_token_file")))
    return BootstrapSettings(
        advertise_ip=cfg.advertise_ip,
        device_id=int(cfg.get("bootstrap", "device_id")),
        timezone_code=cfg.get("bootstrap", "timezone_code"),
        summer_zone=int(cfg.get("bootstrap", "summer_zone", default=0)),
        last_clock_id=int(cfg.get("bootstrap", "last_clock_id", default=1)),
        configured_mac=cfg.pixoo_mac,
        device_token=token,
        allowed_hosts=list(cfg.get("bootstrap", "allowed_hosts") or []),
        allow_direct_ip_host=bool(cfg.get("bootstrap", "allow_direct_ip_host", default=True)),
        user_id=int(cfg.get("bootstrap", "user_id", default=0)),
        backup_ip=cfg.get("bootstrap", "backup_ip", default="") or cfg.advertise_ip,
        screen_on_off=int(cfg.get("bootstrap", "screen_on_off", default=1)),
        custom_type=int(cfg.get("bootstrap", "custom_type", default=0)),
        lot=float(cfg.get("bootstrap", "lot", default=0.0)),
        lat=float(cfg.get("bootstrap", "lat", default=0.0)),
        public_ip=cfg.get("bootstrap", "public_ip", default="") or "",
    )


def _fetch_wan_ip(url: str, timeout: float = 6.0):
    """The CONTAINER's own public IP (same ISP path the Pixoo uses). Only accepts
    a globally-routable address, so a transient failure never yields a private IP."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            ip = r.read().decode("utf-8", "ignore").strip()
        if ipaddress.ip_address(ip).is_global:
            return ip
    except Exception as e:
        log.debug("WAN IP fetch failed: %s", e)
    return None


def _start_public_ip_refresher(settings, cfg):
    """Keep settings.public_ip current with the container's WAN IP so nightly ISP
    re-connects don't leave a stale public IP in the InitV2 / Test/GetIP replies."""
    url = cfg.get("bootstrap", "public_ip_url", default="https://api.ipify.org")
    interval = int(cfg.get("bootstrap", "public_ip_refresh_seconds", default=300))
    ip = _fetch_wan_ip(url)                    # one synchronous fetch up front
    if ip and ip != settings.public_ip:
        log.info("public_ip set from WAN: %s (was %s)", ip, settings.public_ip)
        settings.public_ip = ip

    def loop():
        while True:
            time.sleep(interval)
            new = _fetch_wan_ip(url)
            if new and new != settings.public_ip:
                log.info("public_ip updated %s -> %s (WAN changed)", settings.public_ip, new)
                settings.public_ip = new
    threading.Thread(target=loop, name="public-ip-refresh", daemon=True).start()


def make_server(cfg) -> ThreadingHTTPServer:
    settings = build_settings(cfg)
    handler = BootstrapHandler
    handler.settings = settings
    if bool(cfg.get("bootstrap", "public_ip_auto", default=False)):
        _start_public_ip_refresher(settings, cfg)

    if bool(cfg.get("capture_proxy", "enabled", default=False)) or \
            os.environ.get("CAPTURE_PROXY_ENABLED", "false").lower() == "true":
        from .capture import make_capture
        handler.capture = make_capture(cfg)
        log.warning("CAPTURE PROXY ENABLED — requests are forwarded to the REAL Divoom server")
    else:
        handler.capture = None

    host = cfg.get("bootstrap", "listen_host", default="0.0.0.0")
    port = int(cfg.get("bootstrap", "listen_port", default=80))
    srv = ThreadingHTTPServer((host, port), handler)
    log.info("bootstrap listening on %s:%s (advertise IP=%s, device_id=%s, hosts=%s)",
             host, port, settings.advertise_ip, settings.device_id, settings.allowed_hosts)
    return srv


def main():
    cfg = load_config()
    srv = make_server(cfg)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
