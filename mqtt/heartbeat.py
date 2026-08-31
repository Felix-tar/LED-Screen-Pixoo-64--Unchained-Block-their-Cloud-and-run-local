"""MQTT heartbeat responder.

Subscribes to the Pixoo's `set` and `state` topics and answers every
Device/Hearbeat on the `get` topic, which is what keeps the device from falling
back to "Connecting". Robust to malformed JSON, auto-reconnects, logs unknown
commands for diagnosis, and publishes its liveness to the shared status store.

Run as:  python -m mqtt.heartbeat
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt

from common import status
from common.config import load as load_config
from common.logutil import get_logger, shorten
from common.secrets import read_secret
from .protocol import build_ack_reply, build_heartbeat_reply, is_heartbeat, topics

log = get_logger("heartbeat")


class Heartbeat:
    def __init__(self, cfg):
        self.cfg = cfg
        self.device_id = cfg.device_id
        self.prefix = cfg.topic_prefix
        self.topics = topics(self.prefix)
        # Global server-heartbeat topic the firmware subscribes to; it stays
        # "connected" only while it keeps receiving messages here.
        self.heart_topic = self.prefix.rsplit("/", 1)[0] + "/DeviceHeart"
        self.heart_interval = int(cfg.get("mqtt", "server_heartbeat_seconds", default=5))
        self.qos = int(cfg.get("mqtt", "qos", default=0))
        # values the real server returns in the Device/Connect reply
        self.local_token = int(cfg.get("mqtt", "local_token", default=100000))
        self.software = int(cfg.get("mqtt", "software_version", default=92079))
        # per-command reply templates captured from the real server
        self.templates: dict[str, dict] = {}
        try:
            tpl = Path(cfg.path).parent / "mqtt_responses.json"
            if tpl.exists():
                self.templates = json.loads(tpl.read_text(encoding="utf-8"))
        except Exception as e:  # never let a bad template file kill the responder
            log.warning("could not load mqtt_responses.json: %s", e)
        self.host = cfg.get("mqtt", "connect_host", default="127.0.0.1")
        self.port = int(cfg.get("mqtt", "port", default=1883))
        self.username = cfg.get("mqtt", "server_username", default="pixoo-server")
        self.password = read_secret(cfg.secret_path(cfg.get("mqtt", "server_password_file")))
        self.advertise_ip = cfg.advertise_ip
        self._hb_count = 0
        self._ack_count = 0
        self._seen_cmds: set[str] = set()
        self._last_device_msg_ts = 0.0
        self._connected = False

        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id="pixoo-heartbeat",
            clean_session=True,
        )
        self.client.username_pw_set(self.username, self.password)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        # let paho manage reconnect backoff
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)

    # -- status ----------------------------------------------------------
    def _publish_status(self, *, last_hb_ts: float | None = None):
        data = {
            "service": "heartbeat",
            "mqtt_connected": self._connected,
            "heartbeats_answered": self._hb_count,
            "acks_sent": self._ack_count,
            "last_device_msg_ts": int(self._last_device_msg_ts),
            "updated_ts": int(time.time()),
        }
        if last_hb_ts is not None:
            data["last_heartbeat_ts"] = int(last_hb_ts)
            data["last_heartbeat_iso"] = time.strftime(
                "%Y-%m-%dT%H:%M:%S%z", time.localtime(last_hb_ts)
            )
        prev = status.read("heartbeat")
        prev.update(data)
        status.write("heartbeat", prev)

    # -- callbacks -------------------------------------------------------
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code == 0 or getattr(reason_code, "is_failure", False) is False:
            self._connected = True
            client.subscribe([(self.topics["set"], self.qos), (self.topics["state"], self.qos)])
            # RETAINED so the device gets a server heartbeat the instant it
            # subscribes to divoom/2/DeviceHeart — closes the connect-time race
            client.publish(self.heart_topic, '{"Command":"Device/Hearbeat"}', qos=self.qos, retain=True)
            log.info("connected to broker %s:%s, subscribed to %s + %s",
                     self.host, self.port, self.topics["set"], self.topics["state"])
        else:
            self._connected = False
            log.error("connect failed: %s", reason_code)
        self._publish_status()

    def _on_disconnect(self, client, userdata, *args):
        self._connected = False
        log.warning("disconnected from broker; will auto-reconnect")
        self._publish_status()

    def _on_message(self, client, userdata, message):
        payload = message.payload or b""
        try:
            msg = json.loads(payload.decode("utf-8", "replace"))
        except (ValueError, UnicodeDecodeError):
            log.warning("non-JSON message on %s: %s", message.topic, shorten(payload))
            return
        if not isinstance(msg, dict):
            log.warning("unexpected non-object message on %s: %s", message.topic, shorten(payload))
            return
        self._last_device_msg_ts = time.time()  # device is alive on MQTT

        cmd = msg.get("Command")
        if is_heartbeat(msg):
            reply = build_heartbeat_reply(msg, self.device_id)
            # publish ONLY to get -> never re-triggers our own subscriptions
            client.publish(self.topics["get"], json.dumps(reply), qos=self.qos, retain=False)
            self._hb_count += 1
            now = time.time()
            if self._hb_count <= 3 or self._hb_count % 20 == 0:
                log.info("heartbeat #%d from %s -> reply PacketFlag=%s DeviceId=%s",
                         self._hb_count, message.topic, reply["PacketFlag"], reply["DeviceId"])
            self._publish_status(last_hb_ts=now)
            return

        # The device fires a connect/sync handshake on .../set and shows
        # "Connecting" until every request is acknowledged on .../get.
        if message.topic == self.topics["set"]:
            reply = build_ack_reply(
                msg, self.device_id, advertise_ip=self.advertise_ip, now_ts=int(time.time()),
                local_token=self.local_token, software=self.software, templates=self.templates,
            )
            if reply:
                client.publish(self.topics["get"], json.dumps(reply), qos=self.qos, retain=False)
                self._ack_count += 1
                if cmd not in self._seen_cmds:  # log each command once, avoid spam
                    self._seen_cmds.add(cmd)
                    log.info("ack %r (PacketFlag=%s) on %s -> get", cmd,
                             reply.get("PacketFlag"), message.topic)
                    self._publish_status()  # refresh acks_sent for the web UI
                return

        # status / last-will / anything else: log for diagnosis, do not answer
        log.debug("no-reply message %r on %s: %s", cmd, message.topic, shorten(payload))

    # -- global server heartbeat ----------------------------------------
    def _heart_loop(self):
        """Publish to divoom/2/DeviceHeart so the device knows the server is
        alive and keeps its local API (port 80) open instead of re-bootstrapping."""
        n = 0
        while True:
            try:
                if self._connected:
                    n += 1
                    # exact real-server broadcast payload (captured): minimal
                    self.client.publish(
                        self.heart_topic, '{"Command":"Device/Hearbeat"}', qos=self.qos, retain=True
                    )
                    if n <= 2 or n % 60 == 0:
                        log.info("server heartbeat -> %s (#%d)", self.heart_topic, n)
            except Exception as e:
                log.debug("heart publish failed: %s", e)
            time.sleep(self.heart_interval)

    # -- run -------------------------------------------------------------
    def run(self):
        log.info("starting heartbeat (device_id=%s prefix=%s heart=%s@%ss)",
                 self.device_id, self.prefix, self.heart_topic, self.heart_interval)
        self._publish_status()
        threading.Thread(target=self._heart_loop, name="server-heartbeat", daemon=True).start()
        while True:
            try:
                self.client.connect(self.host, self.port, keepalive=30)
                self.client.loop_forever(retry_first_connection=True)
            except (OSError, mqtt.MQTTException) as e:
                log.error("broker connection error: %s; retrying in 3s", e)
                self._connected = False
                self._publish_status()
                time.sleep(3)


def main():
    cfg = load_config()
    Heartbeat(cfg).run()


if __name__ == "__main__":
    main()
