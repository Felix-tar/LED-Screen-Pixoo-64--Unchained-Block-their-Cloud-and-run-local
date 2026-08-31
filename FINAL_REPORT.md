# Pixoo-Local — Abschlussdokumentation

Lokale, cloud-freie Steuerung des Divoom Pixoo 64 auf dem Raspberry Pi
`raspberrypi` (10.10.20.50). Der Pixoo bootet und läuft vollständig lokal — ohne
Internet, mit dauerhaft aktiver Omada-LAN→WAN-Sperre.

> Keine Passwörter/Tokens in diesem Dokument — nur die Pfade, an denen sie liegen.

---

## 1. Zweck

Der Pixoo 64 holt beim Start Zeit, MQTT-Server und Token von
`app.divoom-gz.com`. Da der Pixoo per Omada dauerhaft vom WAN getrennt ist,
blieb er sonst bei „Connecting" / 01.01.1970 hängen. Dieses Projekt bildet die
Divoom-Server (HTTP-Bootstrap + MQTT) lokal und isoliert nach, sodass der Pixoo
ohne Cloud vollständig startet und lokal steuerbar ist (eigenes 64×64-Dashboard).

## 2. Endgültige Netzwerkarchitektur

```
Pixoo 10.10.20.161
   │  DNS app.divoom-gz.com  ─(Record auf 10.10.20.231)─►  10.10.20.160
   ▼
Docker-macvlan-Container "pixoo-local"  @ 10.10.20.160  (eigene MAC, eigenes LAN-IP)
   ├─ :80    Bootstrap  (Device/InitV2, /Test/GetIP)
   ├─ :1883  Mosquitto  (dediziert, authentifiziert, ACL)
   ├─         Heartbeat + Server-Push  (divoom/2/DeviceHeart, Command-Antworten)
   └─ :8090  Web-UI + Supervisor + Controller (HTTP↔Pixoo)

Host 10.10.20.50 (Pi-hole, Home Assistant, geteilter Mosquitto) — UNVERÄNDERT
```

Die Isolation per macvlan gibt dem Pixoo-Stack eine eigene LAN-IP + MAC. Er
belegt Port 80/1883/8090 nur auf `10.10.20.160`; Pi-hole (:80), der geteilte
Mosquitto (:1883) und `eth0` werden nicht angefasst.

## 3. Adressen (IP / MAC)

| Rolle | IP | MAC |
|------|------|------|
| Raspberry Pi (Host, Pi-hole) | 10.10.20.50 | (eth0) |
| Pixoo 64 | 10.10.20.161 | `1C:69:20:D5:B8:FC` → `a1b2c3d4e5f6` |
| Pixoo-Container (macvlan) | 10.10.20.160 | `02:42:0a:0a:14:a0` (lokal administriert) |
| DNS + DHCP (Synology NAS) | 10.10.20.231 | — |
| Gateway (Omada) | 10.10.20.254 | — |

- **MQTT-DeviceId:** `300000000`  ·  **UserId:** `400000000`
- **MQTT-Benutzer:** `a1b2c3d4e5f6` (die MAC)  ·  **Server-Benutzer:** `pixoo-server`

## 4. DNS-Eintrag (auf 10.10.20.231)

```
app.divoom-gz.com   A   10.10.20.160
```
Kein Wildcard, kein AAAA. Prüfen: `dig @10.10.20.231 app.divoom-gz.com +short` → `10.10.20.160`.

## 5. DHCP-Reservierung des Pixoo

Auf der Synology reserviert: MAC `1C:69:20:D5:B8:FC` → `10.10.20.161` (aktiv).
Zusätzlich muss `10.10.20.160` (Container-IP) im DHCP ausgeschlossen/reserviert
sein, damit sie nie an ein anderes Gerät vergeben wird.

## 6. Docker-macvlan-Aufbau

- Netzwerk `pixoo_macvlan` (driver macvlan, parent `eth0`, subnet 10.10.20.0/24,
  gateway 10.10.20.254) — definiert in [docker-compose.yml](docker-compose.yml).
- Container `pixoo-local`: feste IP `10.10.20.160`, feste MAC `02:42:0a:0a:14:a0`,
  `restart: unless-stopped`, `cap_drop: ALL` + minimale Caps, `no-new-privileges`.
- Prozesse im Container (supervisord): mosquitto, bootstrap (root, nur für Port 80),
  heartbeat + web (unprivilegierter User `pixoo`).
- Hinweis macvlan: der **Host** erreicht `10.10.20.160` nicht direkt (LAN-Geräte
  wie der Pixoo und dein Laptop schon). Für Host-Zugriff: `scripts/enable_host_access.sh`.

## 7. Bootstrap-Ablauf (HTTP, Port 80)

Der Pixoo ruft beim Start `GET /Device/InitV2` (mit JSON-Body) und `GET /Test/GetIP`.
Der Bootstrap-Server antwortet mit einer **exakten Nachbildung** der echten
Divoom-Antwort — das war für diese Firmware zwingend, sonst startet der Pixoo alle
~28 s neu (Flackern). Wichtige Felder (in [config/config.yaml](config/config.yaml)):

- Echte Geräteidentität: `DeviceId 300000000`, `UserId 400000000`, `SummerZone 0`,
  `LastClockId`, `ScreenOnOff`, `BackupIP`, `OfflineTime/OnlineTime`, `CustomType`.
- `DeviceToken` als **JWT** (`{"username":"<mac>"}`, HS256) — dient auch als MQTT-Passwort.
- `/Test/GetIP` liefert eine **öffentliche** IP (`CustonIP`) — eine private IP lässt
  den Pixoo „offline" annehmen und neu bootstrappen.

## 8. MQTT-Ablauf und Topics

Broker: `10.10.20.160:1883`, `allow_anonymous false`, ACL auf die Gerätetopics.
Der Pixoo abonniert `divoom/2/300000000/get` **und** `divoom/2/DeviceHeart`.

| Topic | Richtung | Zweck |
|------|------|------|
| `divoom/2/300000000/set` | Pixoo → Server | Anfragen (Device/Connect, Sys/GetConf, …) |
| `divoom/2/300000000/state` | Pixoo → Server | Status / Last-Will |
| `divoom/2/300000000/get` | Server → Pixoo | Antworten auf die Anfragen |
| `divoom/2/DeviceHeart` | Server → alle | globaler Server-Heartbeat `{"Command":"Device/Hearbeat"}` alle 5 s |

Damit der Pixoo **verbunden bleibt**, muss der Server:
1. periodisch `divoom/2/DeviceHeart` senden (sonst trennt der Pixoo nach ~5 s),
2. auf `Device/Connect` mit einem **`LocalToken`** (Zahl) + `Software` + `ExpertList` antworten,
3. auf `Sys/GetConf` die **vollständige Gerätekonfiguration** zurückgeben,
4. alle Anfragen mit **QoS 1** beantworten (der Pixoo abonniert mit QoS 1).

Die exakten Antworten liegen in [config/mqtt_responses.json](config/mqtt_responses.json)
(aus dem echten Server aufgezeichnet). Die Werte sind konfigurierbar.

## 9. Lokale Pixoo-HTTP-API

`POST http://10.10.20.161/post` — z. B. `{"Command":"Channel/GetAllConf"}` →
`{"error_code":0,...}`. Der Controller nutzt sie für Helligkeit, Screen on/off,
`Draw/ResetHttpGifId` + `Draw/SendHttpGif` (64×64×3 RGB → Base64).

## 10. Wichtige Dateien

| Datei | Zweck |
|------|------|
| [docker-compose.yml](docker-compose.yml) | macvlan-Netz + Container (IP/MAC/Caps) |
| [.env](.env) | Compose-Overrides (eth0, .160, Subnetz, Gateway, MAC) |
| [config/config.yaml](config/config.yaml) | Haupt-Konfiguration (Identität, Topics, Heartbeat) |
| [config/mqtt_responses.json](config/mqtt_responses.json) | echte MQTT-Antwort-Templates |
| [bootstrap/](bootstrap/) | InitV2/Test/GetIP-Server |
| [mqtt/heartbeat.py](mqtt/heartbeat.py) | DeviceHeart-Push + Command-Antworten |
| [controller/](controller/) · [dashboard/](dashboard/) · [web/](web/) | Steuerung, Dashboard, Web-UI |
| `/etc/pixoo-local/device-token` | Secret → JWT-Token + MQTT-Passwort (chmod 600) |
| `/etc/pixoo-local/server-mqtt-password` | Secret → pixoo-server MQTT-Passwort (chmod 600) |
| `/etc/pixoo-local/web-auth-password` | Secret → Web-UI Basic-Auth (chmod 600) |

## 11. systemd- und Docker-Befehle / Start-Stop-Neustart

```bash
sudo systemctl start   pixoo-local      # Stack starten
sudo systemctl stop    pixoo-local      # Stack stoppen
sudo systemctl restart pixoo-local      # neu starten
systemctl status pixoo-local            # Status (enabled = Autostart nach Reboot)
cd /opt/pixoo-local && docker compose ps # Container-Status
docker restart pixoo-local              # nur den Container neu starten
```

## 12. Logs und Fehlerdiagnose

```bash
docker logs -f pixoo-local                       # alle Dienste
docker exec pixoo-local sh -c 'cat /run/pixoo/status/*.json'   # Live-Status
/opt/pixoo-local/scripts/diagnose.sh             # Snapshot -> reports/
```
Prüfsteine: `dig @10.10.20.231 app.divoom-gz.com +short` = `10.10.20.160`;
`docker logs pixoo-local | grep InitV2` (nach Kaltstart 1× erwartet, danach ruhig);
`curl -sS --max-time 5 -X POST http://10.10.20.161/post -d '{"Command":"Channel/GetAllConf"}'`
= `error_code 0`. Port 80 dauerhaft offen = Pixoo stabil verbunden.

## 13. Kaltstart über Smart-Steckdose

1. Omada-LAN→WAN-Sperre bleibt aktiv.
2. Smart-Steckdose AUS → 10 s → EIN.
3. Der Pixoo bekommt per DHCP wieder `10.10.20.161`, ruft
   `app.divoom-gz.com` → `10.10.20.160`, bootstrappt lokal, verbindet MQTT,
   erhält Server-Heartbeats, öffnet Port 80 und zeigt das lokale Dashboard.
   Kein Interneteingriff nötig. (Prüfskript: `scripts/cold_boot_check.sh`.)

## 14. Testbild senden

```bash
PYTHONPATH=/opt/pixoo-local python3 /opt/pixoo-local/scripts/send_test_pattern.py
```
Sendet das 64×64-„LOCAL"-Testbild (Exitcode 0 nur bei Erfolg). Das Dashboard
wird vom Supervisor automatisch alle paar Sekunden aktualisiert.

## 15. Backup und Deinstallation

- Backups vor Änderungen: `/opt/pixoo-local/backups/<timestamp>/`.
- Vollständiger Rollback: `sudo /opt/pixoo-local/scripts/uninstall.sh`
  (stoppt Stack, entfernt Unit + macvlan-Netz; Pi-hole/Mosquitto/HA bleiben unberührt).
  `--purge` entfernt zusätzlich Image/Secrets/Projektdateien (nach Rückfrage).

## 16. Sicherheitsmodell

Keine Divoom-Cloud/Anmeldedaten. Pixoo-WAN-Sperre bleibt aktiv. Dedizierter
Broker `allow_anonymous false` + ACL (der geteilte Host-Broker bleibt unberührt).
Secrets als chmod-600-Dateien außerhalb von Git (`.gitignore`). Web-UI nur im LAN
mit Basic-Auth. Keine UPnP/WAN-Freigaben, keine Telemetrie, Omada-Firewall
unverändert. Container mit `cap_drop: ALL` + `no-new-privileges`.

## 17. Bekannte Einschränkungen

- **Öffentliche IP statisch:** `bootstrap.public_ip` in config.yaml ist fest
  hinterlegt. Bei ISP-IP-Wechsel ggf. anpassen (Wert unkritisch, muss nur
  „öffentlich" aussehen).
- **Cloud-Uhren laden nicht:** die in der Divoom-App gewählte Bitcoin-/Cloud-Uhr
  braucht die Divoom-Cloud und bleibt ohne Internet im Ladebildschirm. Lösung:
  das **lokale Dashboard** (wird automatisch gepusht) oder eine lokale Uhr nutzen.
- **LocalToken/Software** sind feste Werte (aus dem echten Server übernommen);
  bei Firmware-Updates evtl. neu aufzeichnen.
- **`mqtt_connected`**-Statusflag basiert auf zuletzt empfangenen Gerätenachrichten
  (die Firmware sendet kein klassisches Device/Hearbeat, sondern den Connect-Handshake).
- Details/Historie der Server-Nachbildung: [README.md](README.md) · [CHANGELOG.md](CHANGELOG.md).
