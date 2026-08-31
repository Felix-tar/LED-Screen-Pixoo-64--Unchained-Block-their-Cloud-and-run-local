#!/usr/bin/env python3
"""Host-side Home Assistant bridge for the Pixoo screen on/off switch.

The container (macvlan) can't reach the host's shared Mosquitto, so this runs on
the HOST. It publishes an MQTT-discovery switch "Pixoo Screen" to the broker Home
Assistant already uses (10.10.20.50:1883), mirrors the screen state, and forwards
ON/OFF commands to the container by writing data/screen_cmd.json (which the
supervisor applies). Through HA the switch reaches Apple Home / Alexa / Google.

It ONLY publishes/subscribes — it never changes the broker configuration.
Run via pixoo-ha-bridge.service.
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, "/opt/pixoo-local")
import paho.mqtt.client as mqtt  # noqa: E402

from common.config import load as load_config  # noqa: E402
from common.logutil import get_logger  # noqa: E402

log = get_logger("ha-bridge")
DATA_DIR = os.environ.get("PIXOO_DATA_DIR", "/opt/pixoo-local/data")
STATE_FILE = os.path.join(DATA_DIR, "screen_state.json")
CMD_FILE = os.path.join(DATA_DIR, "screen_cmd.json")


def _new_client(client_id):
    try:  # paho-mqtt >= 2.0
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
    except Exception:
        return mqtt.Client(client_id=client_id)


def _read_state_on():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return bool(json.load(f).get("on", True))
    except Exception:
        return None


def _write_cmd(state: str):
    tmp = CMD_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"state": state, "ts": time.time(), "source": "homeassistant"}, f)
    os.replace(tmp, CMD_FILE)


def main():
    cfg = load_config()
    hc = cfg.get("homeassistant", default={}) or {}
    if not hc.get("enabled", False):
        log.info("homeassistant.enabled is false — bridge idle")
        return
    host = hc.get("mqtt_host", "10.10.20.50")
    port = int(hc.get("mqtt_port", 1883))
    prefix = hc.get("discovery_prefix", "homeassistant")
    node = hc.get("node_id", "pixoo64")
    name = hc.get("device_name", "Pixoo Screen")

    base = f"pixoo/{node}"
    t_cmd = f"{base}/screen/set"
    t_state = f"{base}/screen/state"
    t_avail = f"{base}/availability"
    t_disc = f"{prefix}/switch/{node}/screen/config"
    discovery = {
        "name": name, "unique_id": f"{node}_screen",
        "command_topic": t_cmd, "state_topic": t_state, "availability_topic": t_avail,
        "payload_on": "ON", "payload_off": "OFF", "state_on": "ON", "state_off": "OFF",
        "icon": "mdi:television-ambient-light",
        "device": {"identifiers": [node], "name": name,
                   "manufacturer": "Divoom", "model": "Pixoo 64 (local)"},
    }

    cli = _new_client(f"{node}-ha-bridge")
    cli.will_set(t_avail, "offline", qos=1, retain=True)

    def on_connect(c, u, flags, rc):
        log.info("connected to %s:%s rc=%s", host, port, rc)
        c.publish(t_disc, json.dumps(discovery), qos=1, retain=True)
        c.publish(t_avail, "online", qos=1, retain=True)
        c.subscribe(t_cmd, qos=1)
        st = _read_state_on()
        if st is not None:
            c.publish(t_state, "ON" if st else "OFF", qos=1, retain=True)

    def on_message(c, u, msg):
        payload = msg.payload.decode("utf-8", "ignore").strip().upper()
        if payload in ("ON", "OFF", "TOGGLE"):
            log.info("HA command: %s", payload)
            try:
                _write_cmd(payload.lower())
            except Exception as e:
                log.warning("write cmd failed: %s", e)

    cli.on_connect = on_connect
    cli.on_message = on_message
    while True:
        try:
            cli.connect(host, port, keepalive=30)
            break
        except Exception as e:
            log.warning("broker %s:%s not reachable (%s), retrying…", host, port, e)
            time.sleep(5)
    cli.loop_start()

    last = object()
    try:
        while True:
            st = _read_state_on()
            if st is not None and st != last:
                cli.publish(t_state, "ON" if st else "OFF", qos=1, retain=True)
                last = st
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        cli.publish(t_avail, "offline", qos=1, retain=True)
        cli.loop_stop()


if __name__ == "__main__":
    main()
