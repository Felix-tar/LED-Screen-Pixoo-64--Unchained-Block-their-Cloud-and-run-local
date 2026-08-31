"""Unified Pixoo transport with HTTP-preferred / MQTT-fallback auto mode.

Presents one interface (get_config, set_brightness, screen_on, screen_off,
send_frame, health) and picks the transport per configured mode:
  * "http" — always HTTP
  * "mqtt" — always MQTT
  * "auto" — prefer HTTP when the local API is ready, else MQTT if connected.

The auto decision is cached briefly so we don't probe the device on every call,
and is re-evaluated when the active transport raises an unreachable error.
"""
from __future__ import annotations

import time

from PIL import Image

from common.logutil import get_logger
from .pixoo_http import PixooHttp, PixooUnreachable, PixooBadResponse
from .pixoo_mqtt import PixooMqtt, PixooMqttUnavailable

log = get_logger("controller.transport")


class PixooTransport:
    def __init__(self, cfg, *, http: PixooHttp | None = None, mqtt_client: PixooMqtt | None = None):
        self.cfg = cfg
        self.mode = cfg.get("display", "transport", default="auto")
        self.http = http or PixooHttp(
            cfg.http_base_url,
            connect_timeout=float(cfg.get("http_api", "connect_timeout", default=2.0)),
            read_timeout=float(cfg.get("http_api", "read_timeout", default=5.0)),
            max_retries=int(cfg.get("http_api", "max_retries", default=3)),
            backoff_factor=float(cfg.get("http_api", "backoff_factor", default=0.5)),
        )
        self._mqtt = mqtt_client
        if self.mode in ("auto", "mqtt"):
            self._mqtt = self._mqtt or PixooMqtt(cfg)
            self._mqtt.start()
        self._cached = None
        self._cached_at = 0.0
        self._cache_ttl = 3.0

    # -- selection -------------------------------------------------------
    def _http_ready(self) -> bool:
        try:
            return self.http.is_api_ready()
        except Exception:
            return False

    def active_transport(self, *, force: bool = False) -> str:
        now = time.time()
        if not force and self._cached and (now - self._cached_at) < self._cache_ttl:
            return self._cached
        if self.mode == "http":
            choice = "http"
        elif self.mode == "mqtt":
            choice = "mqtt"
        else:  # auto
            if self._http_ready():
                choice = "http"
            elif self._mqtt and self._mqtt.health():
                choice = "mqtt"
            else:
                choice = "http"  # default target; will surface unreachable errors
        if choice != self._cached:
            log.info("active transport -> %s (mode=%s)", choice, self.mode)
        self._cached = choice
        self._cached_at = now
        return choice

    def _backend(self, force: bool = False):
        return self.http if self.active_transport(force=force) == "http" else self._mqtt

    def _call(self, method: str, *args, **kwargs):
        backend = self._backend()
        try:
            return getattr(backend, method)(*args, **kwargs)
        except (PixooUnreachable, PixooBadResponse, PixooMqttUnavailable) as e:
            log.warning("%s via %s failed (%s); re-evaluating transport",
                        method, self.active_transport(), e)
            # force re-evaluation and try the other backend once
            other = self._backend(force=True)
            if other is not backend and other is not None:
                return getattr(other, method)(*args, **kwargs)
            raise

    # -- unified interface ----------------------------------------------
    def get_config(self) -> dict:
        # reads always prefer HTTP
        return self.http.get_config()

    def set_brightness(self, value: int) -> dict:
        return self._call("set_brightness", value)

    def screen_on(self) -> dict:
        return self._call("screen_on")

    def screen_off(self) -> dict:
        return self._call("screen_off")

    def reset_gif_id(self) -> dict:
        return self._call("reset_gif_id")

    def send_frame(self, image: Image.Image, **kw) -> dict:
        return self._call("send_frame", image, **kw)

    def health(self) -> dict:
        http_ok = self._http_ready()
        mqtt_ok = bool(self._mqtt and self._mqtt.health())
        return {
            "http_api": http_ok,
            "mqtt_connected": mqtt_ok,
            "active": self.active_transport(force=True),
            "mode": self.mode,
        }

    def close(self):
        self.http.close()
        if self._mqtt:
            self._mqtt.stop()
