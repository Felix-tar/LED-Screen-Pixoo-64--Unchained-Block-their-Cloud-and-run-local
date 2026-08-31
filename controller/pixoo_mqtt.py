"""MQTT control transport (fallback for the local HTTP API).

The reverse-engineering notes report that the same local REST command JSON can
be delivered over MQTT. Because the exact command/response topic direction must
be confirmed against real device logs, the command topic is configurable
(default: the `get` topic, server -> Pixoo) and every observed reply on
`set`/`state` is logged. This transport is best-effort for control (brightness,
screen, frame); config reads stay on HTTP.

Run standalone diagnostics with:  python -m controller.pixoo_mqtt --watch
"""
from __future__ import annotations

import json
import threading
import time

import paho.mqtt.client as mqtt
from PIL import Image

from common.logutil import get_logger, shorten
from common.secrets import read_secret
from .image import encode_frame
from mqtt.protocol import topics

log = get_logger("controller.mqtt")


class PixooMqttUnavailable(RuntimeError):
    pass


class PixooMqtt:
    def __init__(self, cfg):
        self.cfg = cfg
        self.device_id = cfg.device_id
        self.prefix = cfg.topic_prefix
        self.t = topics(self.prefix)
        self.command_topic = cfg.get("mqtt", "command_topic", default=self.t["get"])
        self.qos = int(cfg.get("mqtt", "qos", default=0))
        self.host = cfg.get("mqtt", "connect_host", default="127.0.0.1")
        self.port = int(cfg.get("mqtt", "port", default=1883))
        self.username = cfg.get("mqtt", "server_username", default="pixoo-server")
        self.password = read_secret(
            cfg.secret_path(cfg.get("mqtt", "server_password_file")), required=False
        )
        self._connected = threading.Event()
        self._last_reply: dict | None = None

        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id="pixoo-controller",
            clean_session=True,
        )
        if self.username:
            self.client.username_pw_set(self.username, self.password)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self._started = False

    def start(self):
        if self._started:
            return
        self._started = True
        try:
            self.client.connect_async(self.host, self.port, keepalive=30)
            self.client.loop_start()
        except OSError as e:
            log.error("mqtt control connect failed: %s", e)

    def stop(self):
        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass
        self._started = False

    # -- callbacks -------------------------------------------------------
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        ok = reason_code == 0 or getattr(reason_code, "is_failure", False) is False
        if ok:
            self._connected.set()
            client.subscribe([(self.t["set"], self.qos), (self.t["state"], self.qos)])
            log.info("mqtt control connected to %s:%s", self.host, self.port)
        else:
            self._connected.clear()
            log.error("mqtt control connect refused: %s", reason_code)

    def _on_disconnect(self, client, userdata, *args):
        self._connected.clear()

    def _on_message(self, client, userdata, message):
        try:
            data = json.loads((message.payload or b"").decode("utf-8", "replace"))
            self._last_reply = data if isinstance(data, dict) else None
            log.debug("mqtt reply on %s: %s", message.topic, shorten(message.payload))
        except ValueError:
            pass

    # -- transport API ---------------------------------------------------
    def health(self) -> bool:
        return self._connected.is_set()

    def _publish(self, payload: dict) -> dict:
        if not self._connected.is_set():
            raise PixooMqttUnavailable("mqtt control transport not connected")
        info = self.client.publish(
            self.command_topic, json.dumps(payload), qos=self.qos, retain=False
        )
        info.wait_for_publish(timeout=5)
        return {"error_code": 0, "via": "mqtt", "topic": self.command_topic}

    def get_config(self) -> dict:
        # No reliable request/response correlation over MQTT without confirmed
        # topics; force callers to use HTTP for reads.
        raise PixooMqttUnavailable("get_config not supported over MQTT transport")

    def set_brightness(self, value: int) -> dict:
        value = int(value)
        if not (0 <= value <= 100):
            raise ValueError("brightness must be 0..100")
        return self._publish({"Command": "Channel/SetBrightness", "Brightness": value})

    def screen_on(self) -> dict:
        return self._publish({"Command": "Channel/OnOffScreen", "OnOff": 1})

    def screen_off(self) -> dict:
        return self._publish({"Command": "Channel/OnOffScreen", "OnOff": 0})

    def reset_gif_id(self) -> dict:
        return self._publish({"Command": "Draw/ResetHttpGifId"})

    def send_frame(self, image: Image.Image, *, pic_id: int = 1, pic_speed: int = 1000) -> dict:
        pic_data = encode_frame(image)
        return self._publish(
            {
                "Command": "Draw/SendHttpGif",
                "PicNum": 1, "PicWidth": 64, "PicOffset": 0,
                "PicID": pic_id, "PicSpeed": pic_speed, "PicData": pic_data,
            }
        )


def _watch():  # pragma: no cover - manual diagnostic
    from common.config import load as load_config
    cfg = load_config()
    m = PixooMqtt(cfg)
    m.start()
    log.info("watching MQTT for %s ... Ctrl-C to stop", m.prefix)
    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        m.stop()


if __name__ == "__main__":
    import sys
    if "--watch" in sys.argv:
        _watch()
