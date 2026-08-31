# pixoo-local — Installation Guide

Run a **fully-local, cloud-free** server for an (old-hardware) **Divoom Pixoo 64**.
Your Pixoo boots and runs with **no internet** — a small Docker container on a
dedicated LAN IP impersonates the Divoom bootstrap + MQTT servers, keeps the
device connected, and renders your own 64×64 dashboard.

This guide is generic. Values like `10.10.20.160` are examples — replace them
with your own. The reference deployment details are in
[FINAL_REPORT.md](FINAL_REPORT.md); the full change history is in
[CHANGELOG.md](CHANGELOG.md).

> ⚠️ This talks to Divoom's real servers **once** (to read your device's id) and
> otherwise runs entirely on your LAN. It is unofficial and specific to the
> older Pixoo 64 hardware/firmware; other models may differ.

---

## 1. What you need

- A **Linux host** on your LAN with **Docker** + **Docker Compose v2**
  (a Raspberry Pi 4 / Debian bookworm is what this was built on; arm64 or amd64).
- A **Divoom Pixoo 64** with a **static/reserved** LAN IP (DHCP reservation).
- Ability to add a **local DNS record** on whatever DNS server your Pixoo uses.
- One **free LAN IP** for the container (macvlan), ideally reserved/excluded on
  your DHCP server so it is never handed out.
- The `macvlan` kernel module (standard on Pi OS / Debian).
- Recommended: a firewall rule that blocks the **Pixoo → WAN** (so it truly
  runs offline). LAN→LAN must stay allowed.

Networking model (nothing on the host is modified — the container gets its own
MAC + IP, so it binds :80 / :1883 / :8090 without touching host services):

```
Pixoo  ──DNS: app.divoom-gz.com ─► (record on YOUR dns) ─►  CONTAINER_IP
                                                             ├─ :80   bootstrap
                                                             ├─ :1883 mosquitto
                                                             └─ :8090 web UI
```

## 2. Clone

```bash
sudo mkdir -p /opt/pixoo-local
sudo chown "$USER" /opt/pixoo-local
git clone <your-fork-url> /opt/pixoo-local
cd /opt/pixoo-local
```

## 3. Find your Pixoo's IP + MAC

From your router/DHCP leases, or:

```bash
sudo nmap -sn 10.10.20.0/24        # find it, then:
ip neigh show <PIXOO_IP>           # shows the MAC
```

Normalize the MAC to 12 lowercase hex chars, no separators (e.g.
`1C:69:20:D5:B8:FC` → `a1b2c3d4e5f6`).

## 4. Discover your device's real identity (important!)

The firmware compares the id/user the server returns against what it has stored;
if they don't match it loops forever. Read the real values **once**:

```bash
scripts/discover_device.sh a1b2c3d4e5f6       # your MAC
```

It prints your real `DeviceId` and `UserId`. Note them.

## 5. Configure

Edit **`config/config.yaml`**:

```yaml
network:
  pixoo_ip: "10.10.20.161"       # your Pixoo
  pixoo_mac: "a1b2c3d4e5f6"      # your MAC
  gateway_ip: "10.10.20.254"
  dns_ip: "10.10.20.231"         # the DNS server your Pixoo actually uses
  advertise_ip: "10.10.20.160"   # the FREE LAN IP for the container

bootstrap:
  device_id: 300000000           # from step 4
  user_id: 400000000             # from step 4
  public_ip: "<your WAN IP>"     # any public-looking IP; a private one makes it loop
  # ...timezone_code etc. to taste...

mqtt:
  device_id: 300000000           # same as bootstrap.device_id
  topic_prefix: "divoom/2/300000000"
  device_username: "a1b2c3d4e5f6"

http_api:
  base_url: "http://10.10.20.161/post"

display:
  safe_clock_id: 26              # set in step 9 (a LOCAL clock face)
```

Edit **`.env`** (compose network) — or let `install.sh` auto-detect it:

```ini
PIXOO_PARENT_IF=eth0
PIXOO_ADVERTISE_IP=10.10.20.160
PIXOO_SUBNET=10.10.20.0/24
PIXOO_GATEWAY=10.10.20.254
PIXOO_MAC=02:42:0a:0a:14:a0      # fixed local MAC for the container
```

## 6. Install (builds image, generates secrets, runs tests, installs the unit)

```bash
sudo scripts/install.sh
```

This creates chmod-600 secrets under `/etc/pixoo-local/`, builds the image, runs
the test suite, and installs+enables `pixoo-local.service`. It does **not** start
the live stack yet (see next steps). Nothing on the host (Pi-hole, other MQTT,
`eth0`) is modified.

## 7. DNS + DHCP

- On **your DNS server** (the one your Pixoo uses) add:
  ```
  app.divoom-gz.com   A   10.10.20.160      (= advertise_ip)
  ```
  No wildcard, no AAAA. Verify from a client:
  `dig @<your-dns> app.divoom-gz.com +short` → `10.10.20.160`.
- On **DHCP**: reserve/exclude `10.10.20.160` so it is never assigned elsewhere.

## 8. Go live

```bash
sudo systemctl start pixoo-local          # or: docker compose up -d
docker logs -f pixoo-local                 # watch it
```

Reach the web UI from any LAN device at `http://10.10.20.160:8090`
(user `pixoo`, password in `/etc/pixoo-local/web-auth-password`).
Note: by macvlan design the **host itself** can't reach that IP — use another
LAN device, or `scripts/enable_host_access.sh`.

## 9. Pick a safe LOCAL clock face

Cloud faces (crypto/mining, social counters, weather) hang the firmware offline.
Find a LOCAL one and set it as the boot face:

```bash
PYTHONPATH=/opt/pixoo-local python3 scripts/find_safe_clock.py          # lists LOCAL faces
PYTHONPATH=/opt/pixoo-local python3 scripts/find_safe_clock.py --show 48 # look at one
```

Put the id you like into `config/config.yaml` → `display.safe_clock_id`, then
`sudo systemctl restart pixoo-local`. The supervisor forces the device onto this
local face on every connect, so it never boots into a hanging cloud face.

## 10. Cold-boot test

Keep the Pixoo's WAN blocked. Power-cycle it (smart plug off ~10s, on) and watch:

```bash
scripts/cold_boot_check.sh
```

Success = the Pixoo boots, hits your local `/Device/InitV2`, connects to the
local MQTT, opens port 80, `Channel/GetAllConf` returns `error_code 0`, and your
dashboard displays — all with no internet.

## 11. Day-to-day

```bash
sudo systemctl {start,stop,restart,status} pixoo-local
docker logs -f pixoo-local
scripts/diagnose.sh                                   # snapshot -> reports/
PYTHONPATH=/opt/pixoo-local python3 scripts/send_test_pattern.py   # test image
sudo scripts/uninstall.sh            # clean rollback (--purge to remove image/secrets too)
```

## 12. Troubleshooting

| symptom | check |
|---|---|
| stuck "Connecting" | `dig @<dns> app.divoom-gz.com +short` must be `advertise_ip`; `docker logs pixoo-local \| grep InitV2` |
| boots then loops / beeps every ~28s | wrong `device_id`/`user_id` (step 4), or a cloud clock face (step 9) |
| device hangs on a face (frozen loader) | that face is a cloud face — pick a LOCAL one (step 9) |
| host can't reach `advertise_ip` | expected (macvlan) — use another LAN device or `enable_host_access.sh` |
| IP conflict | reserve/exclude `advertise_ip` on DHCP |

## 13. How it works (short)

The Pixoo's firmware needs the server to emulate Divoom's full handshake: a
correct `Device/InitV2` (real ids, a JWT token, a public IP, a `/Test/GetIP`
probe), a **retained** global heartbeat on `divoom/2/DeviceHeart`, a
`Device/Connect` reply carrying a numeric `LocalToken`, and replies (at **QoS 1**)
to its whole `Sys/GetConf` + `Get…List` command batch — including 5 extra
commands only a fresh boot sends. And its boot **clock face must be local**, or
it hangs offline. All of that is implemented here and captured in
[FINAL_REPORT.md](FINAL_REPORT.md) §7–8 and [CHANGELOG.md](CHANGELOG.md).

**Security:** no Divoom credentials/cloud; dedicated broker is auth-only + ACL;
secrets are chmod-600 outside git; web UI is LAN-only; the host's Pi-hole /
shared MQTT / `eth0` are never touched.
