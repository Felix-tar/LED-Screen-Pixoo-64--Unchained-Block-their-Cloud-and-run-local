# Adding your own data (custom sources)

Show anything on the Pixoo — a sensor from an MQTT service, a JSON API on your LAN
or the internet, or a file you write yourself. Two steps: **add a source**, then
**point a widget at it**.

## 1. Add a source (browser)

Editor (`http://<advertise_ip>:8091`) → **⚙ Data & Sources** → **Custom** tab.

* **JSON URL** — name + URL (+ refresh seconds). The host data bridge fetches it
  and writes the response to `data/<name>.json`.
* **MQTT topic** — name + broker host/port + topic. The bridge subscribes and
  writes the topic's latest payload to `data/<name>.json` (a JSON payload is stored
  as‑is; a plain value becomes `{"value": …, "topic": …, "updated": …}`).

Click **+ add**, then **Save sources**. Data appears within a few seconds. (Names
are `a‑z 0‑9 _`; sources are stored in `data/custom_sources.json`.)

Equivalent by hand — `data/custom_sources.json`:

```json
[
  { "name": "weather", "type": "url",  "url": "http://10.0.0.5:8080/weather.json", "every": 300 },
  { "name": "livingroom", "type": "mqtt", "mqtt_host": "10.0.0.50", "mqtt_port": 1883, "topic": "zigbee2mqtt/livingroom" }
]
```

## 2. Show it on a screen

Add a widget and set **source: file**, **path:** `/opt/pixoo-local/data/<name>.json`:

* **`kv`** — label/value rows. The **fields** editor has a **path picker** that
  lists every JSON field actually present in that file — pick the ones you want and
  give each a short label.
* **`bar`** — a 0–100 % bar from a numeric field (`source: http`/`file` + `path`).
* **`list`** — rows from a JSON array.

Example `kv` widget:

```json
{ "type": "kv", "x": 1, "y": 10, "w": 62, "h": 40, "source": "file",
  "path": "/opt/pixoo-local/data/weather.json",
  "fields": [
    { "label": "TEMP", "path": "temp_c", "suffix": "C" },
    { "label": "HUM",  "path": "humidity", "suffix": "%" }
  ] }
```

Notes: the pixel font is uppercase‑ASCII, so keep labels short and prefer numeric
values. MQTT custom sources need the data bridge running under the host venv
(paho‑mqtt); the shipped systemd unit does this automatically.
