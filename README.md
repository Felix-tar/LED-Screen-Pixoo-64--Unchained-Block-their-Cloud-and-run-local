# Pixoo‑Local

A **fully local, cloud‑free** control platform for the Divoom **Pixoo 64**. The
device boots and runs with **no internet**: a small LAN server impersonates
Divoom's bootstrap + MQTT so the Pixoo never talks to the cloud, and a browser GUI
lets you build the screens and control it — including a weekday on/off timer and
Apple Home / Alexa / Google / Homebridge.

<p>
<img src="docs/screenshots/home-server.png" width="150" alt="Home Server">
<img src="docs/screenshots/clocks.png" width="150" alt="Clocks">
<img src="docs/screenshots/market.png" width="150" alt="Market">
<img src="docs/screenshots/xmr.png" width="150" alt="XMR node">
<img src="docs/screenshots/claude.png" width="150" alt="Claude usage">
</p>

## Features
- **Cloud‑free boot** — emulates `Device/InitV2` + MQTT so the WAN‑blocked Pixoo
  stays connected with no internet ([how it works](docs/HOW-IT-WORKS.md)).
- **Browser screen editor** — clock & world clocks, system stats, crypto & stocks
  (EUR/USD/%), a Monero node, Claude usage, and **your own MQTT/JSON sources**.
- **Runs isolated** in one Docker macvlan container; the host (Pi‑hole, shared
  Mosquitto, Home Assistant, Homebridge) is never modified.
- **Screen on/off** on a per‑weekday timer and via Apple Home / Alexa / Google /
  Homebridge.
- **Survives power loss** — fast, automatic reconnect after a cold boot.

## Documentation
- [How it works](docs/HOW-IT-WORKS.md) — replacing the Divoom cloud, architecture,
  screens, and where the data comes from (crypto / stocks / Claude / Monero node).
- [The GUI](docs/GUI.md) · [Custom data sources](docs/CUSTOM-SOURCES.md)
- [Install guide](INSTALL.md)

> **Placeholders:** every IP, device id and MAC in this repo is an example. Fill in
> your own (see [INSTALL.md](INSTALL.md)). No tokens/passwords are in the repo —
> they live only in `/etc/pixoo-local/` (chmod 600) on your host.

---


## 1. Problem & cause

On cold boot the Pixoo calls `http://app.divoom-gz.com/Device/InitV2` (port 80).
The reply carries the **MQTT server IP, the current Unix time, the device id and
an MQTT token**. Only after it connects to that MQTT broker does the device
finish booting and open its local HTTP API (port 80) for control.

Your Omada firewall has a permanent **LAN→WAN deny for the Pixoo** (10.10.20.161),
so it can never reach the real Divoom server. Result after a power cut: the Pixoo
gets its IP and answers ping, but **port 80 stays closed**, the clock shows
1970, and the display is stuck on *"Connecting"*. (You can observe this exact
state now with `nmap -Pn -sT -p80 10.10.20.161` → `closed`.)

**Fix:** the Pi impersonates the Divoom bootstrap + MQTT infrastructure on the
LAN. DNS points `app.divoom-gz.com` at the Pi-side stack; it answers InitV2 with
a *local* MQTT IP + a fresh time, runs a local Mosquitto broker, and answers the
device heartbeat — so the Pixoo boots fully with the WAN deny still active.

## 2. Architecture (why it does not disturb the busy homeserver)

This Pi already runs Pi-hole (**owns :80**), a shared Mosquitto (**:1883,
anonymous, used by Home Assistant/Homebridge/other nodes**), Home Assistant
Supervised, Grafana, InfluxDB, OctoPrint, filebrowser (:8090), OpenVPN, etc.
Taking port 80 or reconfiguring the shared broker would break those services.

So the whole Pixoo stack runs in **one Docker `macvlan` container on a dedicated
LAN IP `10.10.20.160`** with its own MAC. It binds :80 and :1883 **on its own IP**,
so there is **no conflict** with Pi-hole or the shared Mosquitto, and **eth0,
Pi-hole and the shared broker are never modified.**

```
Pixoo 10.10.20.161
   │  DNS: app.divoom-gz.com ─► (record on 10.10.20.231) ─► 10.10.20.160
   ▼
10.10.20.160  (macvlan container "pixoo-local")           [ host 10.10.20.50 UNTOUCHED ]
   ├─ :80    bootstrap  (Device/InitV2 → MQTT IP=10.10.20.160, UTCTime, token)
   ├─ :1883  mosquitto  (dedicated, authenticated, ACL divoom/2/300000064/#)
   ├─         heartbeat  (answers Device/Hearbeat on …/get)
   └─ :8090  web UI + supervisor + controller (HTTP↔Pixoo, MQTT fallback)
```

Inside the container `supervisord` runs mosquitto + bootstrap (root, only to bind
:80) + heartbeat + web (unprivileged user `pixoo`). See [docker/](docker/).

> macvlan note: by design the **host cannot reach the container's IP** directly.
> The Pixoo and your other LAN devices (laptop → web UI) reach `10.10.20.160`
> fine. For host-side access run the optional `scripts/enable_host_access.sh`.

## 3. Network facts

| item | value |
|------|-------|
| Pixoo | `10.10.20.161`, MAC `1c:69:20:d5:b8:fc` → `a1b2c3d4e5f6` |
| Dedicated stack IP (`advertise_ip`) | `10.10.20.160` (macvlan, on `eth0`) |
| Host (this Pi / Pi-hole) | `10.10.20.50` |
| DNS server the Pixoo uses | `10.10.20.231` (separate box — **override goes here**) |
| Gateway (Omada) | `10.10.20.254` |
| MQTT device id | `300000000` (real, from cloud) · username `a1b2c3d4e5f6` · topics `divoom/2/300000000/#` + global `divoom/2/DeviceHeart` |

## 4. Bootstrap protocol (`Device/InitV2`)

The Pixoo sends a **GET with a JSON body** to `/Device/InitV2`. The server reads
the body regardless of method, normalizes the MAC (accepts `:`/`-`/case), and
replies HTTP 200 JSON. Key fields: `ReturnCode:0`, `IP` = MQTT server
(`10.10.20.160`), `UTCTime` = `int(time.time())` per request, `DeviceId`,
`DeviceToken` (= MQTT password), `PacketFlag` echoed. Wrong `Host` header →
`404`, no secrets. Unknown MACs get a deterministic *non-authorizing* token.
Also serves `GET /health`. Code: [bootstrap/](bootstrap/), pure logic in
[bootstrap/core.py](bootstrap/core.py).

## 5. MQTT protocol  (the part that stops the "Connecting" flicker)

Broker: `10.10.20.160:1883`, `allow_anonymous false`, ACL limited to
`divoom/2/300000000/#` + read on `divoom/2/DeviceHeart`. The Pixoo logs in as
`a1b2c3d4e5f6` / the JWT DeviceToken, subscribes (QoS 1) to `…/get` **and**
`divoom/2/DeviceHeart`, and publishes its connect/sync batch on `…/set`.

To keep the device connected (port 80 open) instead of re-bootstrapping every
~28s, [mqtt/heartbeat.py](mqtt/heartbeat.py) must, at **QoS 1**:
1. publish `{"Command":"Device/Hearbeat"}` to `divoom/2/DeviceHeart` every 5s;
2. answer `Device/Connect` with a numeric **`LocalToken`** (+ Software/ExpertList);
3. answer `Sys/GetConf` with the **full device config** and all other Get* commands
   ([config/mqtt_responses.json](config/mqtt_responses.json), captured from the real server).

See [FINAL_REPORT.md](FINAL_REPORT.md) §7–8 for the full handshake. Diagnostic sub:
`docker exec pixoo-local mosquitto_sub -u pixoo-server -P <pw> -v -t 'divoom/#'`.

## 6. DNS record (you apply this on 10.10.20.231)

```
app.divoom-gz.com   A   10.10.20.160
```
No wildcard for `*.divoom-gz.com`, no `AAAA`. Verify from a client:
`dig @10.10.20.231 app.divoom-gz.com +short` → `10.10.20.160`.

## 7. Install / uninstall

```bash
sudo scripts/install.sh          # build, test, provision secrets+env, install unit (no start)
sudo scripts/install.sh --start  # …and start the stack immediately
sudo scripts/uninstall.sh        # stop+remove stack, unit, macvlan net (keeps secrets/files)
sudo scripts/uninstall.sh --purge# also remove image + (prompted) secrets/project files
```
Secrets live in `/etc/pixoo-local/*` (chmod 600). Install is idempotent and
keeps an existing `device-token` so the Pixoo pairing survives reinstalls.
Backups of anything overwritten go to `backups/<timestamp>/`.

## 8. Services, logs, web UI, API

* Start/stop: `systemctl start|stop|status pixoo-local`
* Logs: `docker logs -f pixoo-local` (all services, journald-style lines)
* Web UI: `http://10.10.20.160:8090` — basic auth user `pixoo`, password in
  `/etc/pixoo-local/web-auth-password`.
* **Screen editor: `http://10.10.20.160:8091`** — build/rearrange the widget
  screens (clock, world clocks, system stats, crypto/market, XMR node, generic
  http/file JSON, bars, text) and their rotation in the browser, with live
  preview. Saves to `config/screens.json` and applies live. See §9.1.
* **Same editor also on the host: `http://10.10.20.50:8091`** — a small host
  service (`pixoo-editor-host.service`, own venv `/opt/pixoo-local/.hostvenv`)
  exposes the editor on the Pi's own IP too, so it's reachable even from the Pi
  itself (the macvlan container on .160 is not reachable from the host). It only
  reads/writes the shared `config/screens.json` (no device control, no second
  supervisor); port 8091 is free on the host (8090 is taken by `filebrowser` —
  which is exactly why the stack itself uses macvlan). `systemctl
  start|stop|status pixoo-editor-host`.

REST API (all except `/health` require auth):

| method | path | purpose |
|--------|------|---------|
| GET | `/api/status` | aggregated state (see below) |
| GET | `/api/preview.png?scale=6` | live 64×64 dashboard, nearest-scaled |
| GET | `/api/config` | non-secret config |
| POST | `/api/brightness` `{ "value": 0..100 }` | set brightness |
| POST | `/api/screen/on` · `/api/screen/off` | display power |
| POST | `/api/test-pattern` | send the LOCAL test image |
| POST | `/api/dashboard/push` | force-render + push a frame |
| POST | `/api/reconnect` | re-evaluate transport |
| POST | `/api/hooks/power-on` · `/api/hooks/power-off` | smart-home hooks |
| GET | `/health` | liveness |

## 9. Dashboard & image format

* **Frame:** exactly `64*64*3 = 12288` raw **RGB** bytes, row-major
  (left→right, top→bottom), then base64. No alpha/BGR/PNG/JPEG. Resize uses
  NEAREST (crisp pixel art). See [controller/image.py](controller/image.py).
* **Renderer** ([dashboard/renderer.py](dashboard/renderer.py)): top half =
  homeserver (CPU/RAM/DISK bars, temp, health dot); bottom half = market rows
  (label + coloured change + arrow). Self-contained 3×5 pixel font (no external
  font shipped). Frames are only sent when the content hash changes, ≤1 fps.
  Market data is pluggable — see [dashboard/market_plugins/](dashboard/market_plugins/).

### 9.1 Screens & widgets (the editor)

The display is a **playlist of screens** in [config/screens.json](config/screens.json);
each screen is a background + widgets, each widget drawn into an x/y/w/h box.
The renderer ([dashboard/screens.py](dashboard/screens.py)) rotates screens by
duration and hot-reloads the file, so editor saves apply live.

Widget types ([dashboard/widgets.py](dashboard/widgets.py)): `text`, `clock`
(any timezone), `date`, `sysbars`, `metric` (cpu/ram/disk/temp), `list`
(crypto/http/static rows), `kv` (JSON from an http URL **or a local file**),
`bar` (usage %), `rect`, `line`. Build them at `:8091`.

**Host data (e.g. XMR):** the macvlan container can't reach the host, so the
**data bridge** (`pixoo-databridge.service`, `scripts/data_bridge.py`) fetches
configured host APIs into `/opt/pixoo-local/data/*.json` (see `data_bridge` in
config.yaml); a `kv` widget with `source: file` displays them. Data on *other*
LAN hosts can be fetched directly with `source: http`. For a value you compute
yourself (e.g. a Claude usage %), write a JSON file into `data/` and point a
`kv`/`bar` widget at it.

## 10. Smart-plug / cold-boot behaviour

The supervisor ([controller/supervisor.py](controller/supervisor.py)) runs a
state machine `OFFLINE → PING_ONLY → BOOTSTRAP_SEEN → MQTT_CONNECTED →
API_READY → DISPLAY_ACTIVE`, polling fast during boot and slow when stable. On
the rising edge to `API_READY` it sets brightness and pushes the current frame;
after a reconnect it re-pushes. The container's `restart: unless-stopped` +
`pixoo-local.service` (enabled) bring the stack up automatically after any Pi
reboot / power loss — no manual internet window needed.

## 11. Test flow

```bash
scripts/run_tests.sh          # 28 pytest unit tests (offline, in the image)
scripts/smoke_container.sh    # 12 full-stack checks on a throwaway bridge (no device/eth0 impact)
scripts/test_bootstrap.sh     # InitV2 + wrong-host reject (container-internal)
scripts/test_mqtt.sh          # heartbeat round-trip + ACL deny (container-internal)
PYTHONPATH=/opt/pixoo-local python3 scripts/test_api.py          # live, non-destructive Pixoo API
PYTHONPATH=/opt/pixoo-local python3 scripts/send_test_pattern.py # live: show the LOCAL test image
scripts/cold_boot_check.sh    # the acceptance test — run AFTER DNS is set + a power cycle
scripts/diagnose.sh           # snapshot to reports/
```
**Do the cold-boot test only after the DNS record is confirmed.**

## 12. Troubleshooting

* Pixoo stuck "Connecting" after cold boot → `dig @10.10.20.231
  app.divoom-gz.com +short` must return `10.10.20.160`; check
  `docker logs pixoo-local | grep InitV2`.
* No InitV2 seen → DNS not effective on `10.10.20.231`, or the Pixoo hasn't
  re-initialized yet (power-cycle it).
* Bootstrap seen but no heartbeat → check broker auth
  (`docker logs pixoo-local | grep -i mosquitto`).
* IP collision → ensure `10.10.20.160` is reserved/excluded on the `10.10.20.231`
  DHCP scope.
* One-time capture fallback (§11 of the spec): set `capture_proxy.enabled: true`
  in `config/config.yaml` to record the *real* upstream reply once, then turn it
  back off. Captures go to `reports/bootstrap-capture/` with tokens masked.

## 13. Security model

No Divoom credentials or cloud. Pixoo WAN deny stays active. Dedicated broker is
`allow_anonymous false` + ACL; the shared host broker is untouched. Secrets are
chmod-600 files outside git; `.gitignore` excludes secrets/env/reports. Web is
LAN-bound with basic auth; no WAN binding, no UPnP, no telemetry. The Omada
firewall rule is never changed. See [reports/system-discovery.txt](reports/system-discovery.txt)
for the full audit and [CHANGELOG.md](CHANGELOG.md) for every change made.
