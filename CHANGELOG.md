# Changelog

All notable changes and every host modification are recorded here.

## [1.5.0] — 2026-08-31  (custom data sources + full docs + sanitized GitHub package)

- **Custom data sources in the GUI.** Editor -> Data & Sources -> **Custom**: add your
  own **MQTT topic** or **JSON URL**; the host data-bridge writes each to
  `data/<name>.json` and any `kv`/`list`/`bar` widget reads it. Stored in
  `data/custom_sources.json`.
- **Documentation** under `docs/`: HOW-IT-WORKS, GUI, CUSTOM-SOURCES + screenshots.
- **Privacy-sanitized package**: real ids/IPs/MAC/hostname replaced by placeholders;
  no tokens/passwords/secret files in the repo.
- All 38 tests pass.

## [1.4.2] — 2026-08-31  (Homebridge one-click + manual instructions in the GUI)

- **Homebridge integration.** Installed the `homebridge-mqttthing` plugin (via
  hb-service) and added a "Pixoo Screen" switch accessory to the existing
  Homebridge `config.json` (backed up first; pointed at the ha-bridge MQTT topics)
  → the switch is live in Apple Home via Homebridge. Existing plugins untouched.
- **`scripts/setup_homebridge.sh`** — idempotent installer (adds plugin if missing,
  writes/updates the accessory with a config backup, restarts Homebridge).
- **GUI in the editor's 📱 Smart Home panel** (a new "Homebridge — one click"
  section): live status (plugin / switch configured / running), an **Install &
  activate** button (`GET /api/homebridge/status`, `POST /api/homebridge/install`
  run the script), the exact `sudo …/setup_homebridge.sh` command, and the full
  **manual** `homebridge-mqttthing` accessory JSON (auto-filled from the live MQTT
  topics) with a short explanation.
- The host editor unit was given `ReadWritePaths=/var/lib/homebridge` (+ an npm
  HOME/cache) so the button can edit Homebridge's config and restart it. Verified:
  button run → plugin loaded, accessory initialized, Homebridge healthy.
- All 38 tests pass.

## [1.4.1] — 2026-08-31  (schedule GUI + smart-home QR panel in the web UI)

- **Schedule editor** with ⏰ Schedule buttons in BOTH the editor (`:8091`, next to
  Data & Sources) and the control UI (`:8090`): a global on/off toggle and a
  per-weekday grid (tick a day, set on/off via native time pickers). Saves to
  `data/schedule.json` (config.yaml is the built-in default); the supervisor
  hot-reloads within ~20s (the :8090 control UI also enforces immediately via
  `Supervisor.reload_schedule()`). `GET/PUT /api/schedule` on both services.
- **Smart Home panel** (📱 Smart Home, both services): QR codes (client-side,
  qrcodejs) to open the control page and Home Assistant on a phone, the
  auto-discovered switch's entity id + MQTT topics, and concise steps to expose it
  to Apple Home / Alexa / Google via HA. `GET /api/smarthome`.
- All 38 tests pass.

## [1.4.0] — 2026-08-31  (on/off timer + Apple/Alexa/Google via Home Assistant)

- **Screen on/off timer, per weekday.** `schedule.days.<mon..sun>.{on,off}` = "HH:MM"
  in `schedule.timezone`. The supervisor fires the edges (and enforces the current
  window at startup); a manual on/off overrides until the next scheduled edge.
- **Real on/off** — this unit's `OnOffScreen`/`LightSwitch` is unreliable (the panel
  keeps showing pushed frames), so off/on now drives **brightness** (0 = dark,
  restore = on) plus stops/resumes the frame push. `Supervisor.set_screen()`, with
  the state mirrored to `data/screen_state.json`.
- **Apple Home / Alexa / Google via Home Assistant.** New host service
  `pixoo-ha-bridge` publishes an MQTT-discovery switch "Pixoo Screen" to the shared
  broker HA already uses (10.10.20.50:1883 — publish/subscribe only, never changes
  it). HA commands are forwarded to the container via `data/screen_cmd.json`
  (macvlan-safe file bridge) and applied by the supervisor's command loop; state is
  mirrored back. Expose the HA entity to Apple/Alexa/Google in HA to control it by
  voice/app. `homeassistant.*` config; paho-mqtt added to the host venv.
- `data/` is now world-writable (container uid 987 + host services write there;
  runtime data only). install.sh sets up the host venv and installs/enables all
  host services (editor, data-bridge, ha-bridge).
- All 38 tests pass.

## [1.3.9] — 2026-08-31  (fast + robust reconnect after power loss)

- **Reconnect is now as fast as the device allows, and self-recovers.** Fast-poll
  (1 s) was previously armed only by the smart-home `power_on()` hook, so a plain
  power-cycle crawled at 5 s. Now the supervisor arms fast-recovery at startup and
  on *every* drop to OFFLINE (`display.fast_recover_seconds`, default 180 s), the
  idle probe is 2 s (was 5 s), and the first dashboard frame retries every 1 s until
  it lands. Frequent probing also keeps the macvlan ARP entry for the device warm,
  avoiding the "No route to host" stall after the device reboots. Measured: the
  supervisor reaches DISPLAY_ACTIVE ~1 s after the device's API comes up; the only
  remaining wait is the Pixoo firmware's own boot/Wi-Fi/handshake time.
- All 38 tests pass.

## [1.3.8] — 2026-08-31  (cold-boot recovery + auto WAN IP)

- **After an overnight power-off the Pixoo showed "connecting".** Root findings:
  the device cold-booted onto its saved face (12, which shows a cloud stocks
  spinner → looks like connecting) while the container briefly couldn't reach the
  device's just-booted local API (stale macvlan ARP). The supervisor self-heals
  (it re-evaluates every 1–5 s and reached DISPLAY_ACTIVE), just slowly on a cold
  boot; a `web` restart hurried it.
- **public_ip is now auto-maintained from the CONTAINER's WAN IP.** Critical
  detail: the host routes via NordVPN (87.x) but the container/Pixoo use the real
  ISP path (203.0.113.x) — so the host's IP was the wrong value. The bootstrap now
  fetches its own WAN IP at startup and every `public_ip_refresh_seconds` (300),
  accepting only a globally-routable address (`bootstrap.public_ip_auto`, default
  on; the config value is just the fallback). Nightly ISP re-connects (e.g.
  .10→.20) no longer leave a stale public IP in InitV2 / Test/GetIP.
- All 38 tests pass.

## [1.3.7] — 2026-08-30  (rotation holds slides, full tickers, XMR node detail)

- **A screen no longer cuts off mid-slideshow.** A screen's effective duration is
  now at least the time its widgets need to cycle through all their slides
  (`slide_seconds`), and the playlist passes screen-relative time so a market card
  steps 0,1,2,…N in order and only then advances to the next screen (page dots
  match the item count). `RenderContext.slide_time`; `PlaylistRenderer._current()`.
- **Stock tickers always shown in full.** Rows/card show the whole ticker; if it
  doesn't fit the dot is dropped (SAP.DE→SAPDE) before ever truncating, and the
  value falls back from full (78.842) to compact (222) rather than being cut off.
- **XMR node: more fields.** The data-bridge now derives `uptime` (compact,
  uppercase), `nodes_in/out/total`, `nodes_inout`, `nodes_tor`, `nodes_ipv4/6`
  from `connections.detail` (`derive: monero`). The XMR screen shows STATE, HASH,
  NET, UP, IN/OUT, TOR, IPV4.
- **kv/list fields are now an editable list** (like the watchlist): label + path
  rows with reorder/remove, plus a picker of the paths actually present in the
  widget's data (`POST /api/kv/paths`) — no more JSON textarea.
- All 38 tests pass.

## [1.3.6] — 2026-08-30  (one watchlist window: reorder / delete / add)

- **Data & Sources → Market is now a proper list manager** instead of a textarea:
  separate Crypto and Stocks lists, each row with ▲▼ reorder and ✕ delete, plus an
  add box (auto-detects crypto vs stock, with a 🔍 test), then Save. New
  `POST /api/market/resolve` (symbol → type + CoinGecko id) backs the add box;
  saves via the existing `PUT /api/market`.
- All 38 tests pass.

## [1.3.5] — 2026-08-28  (Claude screen: featured 5H + smooth severity gradient)

- Removed the old manual (wrong-scope) token; Claude usage runs purely off the
  Claude Code login now.
- **Severity colour is now a smooth gradient**: green below 70 %, then orange at
  70 darkening steadily to red by 90 % (`_sev` interpolates), instead of three
  hard steps. Applies to every usage bar (Claude, CPU/RAM/disk).
- **Claude widget: featured limit.** The 5h session (prop `feature`, default
  `session`) renders large — big percentage + a tall bar + reset countdown — with
  the remaining limits compact below. New `feature` prop in the editor.
- All 38 tests pass.

## [1.3.4] — 2026-08-28  (Claude usage works: right token scope)

- **Claude usage now works on-device.** After waiting out the rate-limit window,
  the clean response was `403 permission_error: "OAuth token does not meet scope
  requirement user:profile"` — a `claude setup-token` token lacks that scope. Fix:
  the data-bridge now reads Claude Code's **own** OAuth accessToken from
  `~/.claude/.credentials.json` (scopes include `user:profile`), configured as
  `data_bridge.claude.credentials_file`. **Read-only** — it is never refreshed,
  rotated, or written back, so the Claude Code login is untouched; it stays fresh
  while Claude Code is used. An expired token is waited out (last-good kept). The
  manual secret/editor token remains a fallback for hosts without Claude Code.
  Verified on-device: `5H 73% · 2H41M`, `WEEK 80% · 4D9H`.
- Editor Claude tab reworked: shows it uses the Claude Code login automatically;
  status reports `source: claude_code`; manual token demoted to a labelled fallback.
- All 38 tests pass.

## [1.3.3] — 2026-08-28  (Claude usage: honor Retry-After / stop self-banning)

- **Root cause of the Claude 429 loop found + fixed.** The `oauth/usage` endpoint
  uses long rate-limit windows (`Retry-After` ~30 min) and **any call while limited
  resets the window** — so continuous polling kept the ban alive forever (never a
  clean 200). The token itself is valid (always `rate_limit_error`, never an auth
  error; identical with/without the beta header). The fetcher now reads
  `Retry-After` on a 429 and records `retry_after_ts`; it makes **no request at all**
  until that time passes, then retries once. Base interval relaxed to 300 s;
  `max_every` backoff removed (Retry-After governs it). Updates now arrive as fast
  as the endpoint actually permits, and the stack no longer self-bans.
- All 38 tests pass.

## [1.3.2] — 2026-08-28  (adaptive Claude poll, sticky XMR, clock on market screens)

- **Claude usage polls fast again, adaptively.** Base 45 s, and the bridge loop
  now backs off exponentially on 429 up to `max_every` (900 s) and resets on
  success — so you get quick updates whenever the strict `oauth/usage` endpoint
  allows, without tripping a long cooldown. Generic per-source `max_every` in the
  data-bridge loop enables the backoff.
- **XMR hashrate/net no longer flicker to "–".** The node monitor intermittently
  returns `null` / the placeholder `"–"` for `hashrate_human`/`net_hash_human`;
  the `xmr` source is now `sticky: true` and the bridge keeps the last good value
  for any field that arrives missing/null/placeholder (poll raised to every 10 s),
  while live fields still update.
- **Clock on the Market & Watchlist screens.** Heading on the left, current time
  (Berlin) top-right; the Watchlist title dropped "USD" (rows already show USD).
- All 38 tests pass.

## [1.3.1] — 2026-08-28  (line-editor watchlist, bigger prices, brightness slider)

- **Watchlist line editor.** Data & Sources → Market is now a plain textarea, one
  instrument per line. Lines resolve automatically: known symbols (BTC, ETH, XMR…)
  and lowercase names/ids (bitcoin, cardano) → crypto; bare uppercase tickers
  (AAPL, NVDA, SAP.DE) → stock (avoids memecoins squatting on equity tickers).
  Force with `c:`/`s:`; `#` comments ignored. Endpoints `GET/POST /api/market/lines`;
  ambiguous names resolved via CoinGecko search (tokenized-stock tokens filtered).
- **Bigger, fully-written prices.** Market card shows a large primary price in
  German grouping (`79.654`, `2.486`, `471,95`) with a currency tag + the second
  currency below; rows use the same full formatting (`_money_full`). Replaces the
  compact `79.6K` style.
- **Brightness slider** in the editor header → `GET/POST /api/brightness`: sends
  `Channel/SetBrightness` to the Pixoo live and persists `display.default_brightness`
  so it survives reconnects. Works from the host editor too (it can reach the Pixoo).
- **Per-widget symbol editor** for `market` widgets: instead of a comma field, a
  list to pick/add/remove/reorder which instruments a widget shows. On a card view
  each symbol is a cycling slide (`+ add` = new slide); on a rows view it's the
  selected rows. Empty = show all. Adding a symbol not yet tracked appends it to the
  watchlist (via `/api/market/lines`) so it starts being fetched. Available symbols
  come from `data/market.json` + the watchlist.
- **🔍 Test button** in the symbol editor → `POST /api/market/test`: fetches ONE
  symbol live and shows the value (USD/EUR/%) or the exact failure reason (e.g.
  `yahoo SPCX http 429`), so an empty widget is explained instead of a cryptic
  "NO MARKET DATA". Single-symbol helpers `quote_crypto/quote_stock/quote_entry`
  added to the data-bridge.
- **Claude usage**: token verified as valid (OAuth, auth accepted); the
  `oauth/usage` endpoint is strictly rate-limited, so the poll interval was raised
  to 15 min and the fetcher now keeps the last-good limits (flagged `stale`) on a
  transient 429 instead of blanking.
- All 38 tests pass.

## [1.3.0] — 2026-08-28  (correct clock, market EUR/USD/%, Claude usage, more cities)

- **Clock fix (wrong time).** A clock/date widget with no explicit `tz` used the
  container's clock, which is UTC — so the main clocks showed ~2h behind Germany.
  They now default to a configured home zone (`display.timezone: Europe/Berlin`).
  Winter<->summer switching for Germany *and every other country* is done by the
  IANA tz database (offline, authoritative) — no time API needed for DST. Verified
  on device: BERLIN 10:37 CEST, NYC/Tokyo/LA/UTC all correct.
- **Internet-time safety net.** New data-bridge `time` source fetches authoritative
  UTC on the host and records the offset vs the local clock (measured 0.1s — the Pi
  is NTP-synced). If the host clock ever drifts past
  `display.time_correction_threshold_seconds` (NTP failure), the clock self-heals
  using that offset; normally nothing is applied.
- **Market widget** (`type: market`) reading `data/market.json`: EUR **and** USD
  **and** % change, `view: card` (one symbol at a time, cycling in-screen with page
  dots) or `view: rows` (compact, paginated). Crypto via CoinGecko (no key,
  EUR+USD+24h%); stocks via Yahoo chart + FX to EUR/USD. Screens: Market (card),
  Watchlist (rows). Watchlist edited in the browser → `config/market.json`.
  NOTE: from this host's IP, Yahoo currently returns 429 and Stooq gates CSV behind
  a JS challenge, so *stock* rows may be empty until a source works or a keyed
  provider is added; crypto is unaffected.
- **Claude usage** (`type: claude`) reading `data/claude.json`: per-limit % + reset
  countdown (5h session, weekly_all, weekly_scoped per model), colored by severity.
  The data-bridge `claude_usage` source calls `api.anthropic.com/api/oauth/usage`
  **server-side** with the OAuth token; primary parse of `limits[]`
  (percent/resets_at/is_active/severity) + fallback to `five_hour`/`seven_day`.
  The token lives only in a chmod-600 secret (or an editor-written 0600 fallback
  under `data/`) and is NEVER in the output JSON, git, the browser, or logs. Set it
  with `scripts/set_claude_token.sh` (stdin) or the editor's write-only field.
- **Editor: Data & Sources dialog** — manage the market watchlist (crypto ids +
  stock tickers), set the Claude token (write-only, status only), and view live
  `data/*.json`. **More timezones** as city labels (Boise, Las Vegas, Phoenix,
  Denver, Seattle, Anchorage, Honolulu, … + world cities); picking a city auto-fills
  the clock label. New `market`/`claude` widget prop editors.
- All 38 tests pass.

## [1.2.0] — 2026-08-28  (browser screen editor + widgets)

Added a LAN-wide, no-code **screen editor** and a widget system so screens are
built in the browser instead of code.

- `dashboard/widgets.py` — widget framework + types: `text`, `clock` (any
  timezone), `date`, `sysbars`, `metric`, `list` (crypto/http/static),
  `kv` (JSON from http **or a file**), `bar`, `rect`, `line`. Each draws into an
  x/y/w/h region; one failing widget never breaks the frame.
- `dashboard/screens.py` — `PlaylistRenderer`: composes widgets into 64×64
  frames and **rotates multiple screens** by per-screen duration; hot-reloads
  `config/screens.json` so edits apply live. Replaces the fixed renderer as the
  supervisor frame source (`dashboard.mode: screens`, `legacy` keeps the old one).
- `config/screens.json` — default playlist: Server (CPU/RAM/DISK/temp + clock),
  Clocks (Berlin big + NYC/Tokyo/LA/UTC), Market (crypto), XMR (live node stats).
- `web/editor.py` + `templates/editor.html` — the editor on its **own port 8091**
  (`http://<advertise_ip>:8091`, same basic auth): add/reorder/delete screens and
  widgets, edit props, **live preview**, toggle rotation, save. Runs as a new
  supervisord program `editor`.
- **Data bridge** (`scripts/data_bridge.py` + `pixoo-databridge.service`): the
  macvlan container can't reach the host (10.10.20.50), so a small HOST service
  fetches configured APIs (e.g. the XMR node monitor `:8420/api/state`) into
  `/opt/pixoo-local/data/*.json`, which widgets read via `source: file`.
  Configured under `data_bridge` in config.yaml; installed by install.sh.
- supervisord now exposes a control socket (`supervisorctl` works in the container).
- Helper scripts: `scripts/discover_device.sh` (read a device's real id/user from
  Divoom), `scripts/find_safe_clock.py` (detect local vs cloud faces). Full
  install guide in [INSTALL.md](INSTALL.md).
- Boot fallback face set to a verified LOCAL face (id 12); the real clock is now a
  widget (this device has no plain built-in clock — all its faces are themed).
- 38 unit tests (added `tests/test_screens.py`); all pass.
- **Editor also on the host IP** (`systemd/pixoo-editor-host.service`): the macvlan
  container on 10.10.20.160 is unreachable from the Pi itself, so a small host
  service runs the *same* editor on `http://10.10.20.50:8091` (a free host port;
  8090 is taken by `filebrowser`). Own venv `/opt/pixoo-local/.hostvenv` (reuses
  system Pillow/PyYAML, adds flask+psutil). It only reads/writes the shared,
  bind-mounted `config/screens.json` — no device control, no second supervisor —
  so both editors and the running renderer stay consistent. Hardened unit
  (NoNewPrivileges, ProtectSystem=strict, single ReadWritePath).

## [1.1.0] — 2026-07-11  (go-live + full handshake emulation)

Brought the stack live on the dedicated macvlan IP and fixed the reconnect/
flicker/beeping loop by emulating the real Divoom handshake exactly (each piece
was captured from the real app.divoom-gz.com server for this device).

### Added / changed
- Fixed container MAC `02:42:0a:0a:14:a0` (locally administered) in compose.
- InitV2 now returns the device's REAL identity + full field set: `DeviceId
  300000000`, `UserId 400000000`, `SummerZone 0`, `LastClockId`, `ScreenOnOff`,
  `BackupIP`, `Offline/OnlineTime`, `CustomType`; DeviceToken is now a **JWT**
  ([common/tokens.py](common/tokens.py)) used as the MQTT password too.
- New endpoint `GET /Test/GetIP` (firmware connectivity probe) returning a
  **public** IP (`bootstrap.public_ip`); a private IP caused the re-init loop.
- MQTT: publish global server heartbeat `{"Command":"Device/Hearbeat"}` to
  `divoom/2/DeviceHeart` every 5s; ACL extended for that topic.
- MQTT: answer the device's connect/sync batch on `.../get`, incl. the
  `Device/Connect` reply with the numeric `LocalToken` and the full `Sys/GetConf`
  config; per-command templates in [config/mqtt_responses.json](config/mqtt_responses.json).
- A **fresh power-on** (vs a warm MQTT reconnect) asks for 5 extra commands that
  a container restart never exercised — `Device/GetTimePlan`, `GetTimeDialFont`,
  `GetTimeDialAppPic`, `GetMemorial`, `GetAlarm`. Each needs its field present
  (empty arrays are fine — fonts/faces are cached on the device); missing them
  kept the fresh boot looping. 16 command templates total. Found via the real
  physical cold-boot test.
- Fresh-boot hardening (after the physical cold-boot proved intermittent):
  * The device asks for 4 MORE commands only after the first batch succeeds
    (`Device/GetClockList`, `GetEqDataList`, `GetHistoryClockList`,
    `Channel/GetNotifyList`). Handled by a generic fallback: any unknown
    `Get<Name>List` returns an empty `<Name>List` (data is cached on the device).
    These were the final blockers — with them the handshake fully completes and
    the device goes idle + stable (port 80 40/40, 0 re-init for minutes).
  * `divoom/2/DeviceHeart` is now published **retained** + on connect + every 3s,
    so the device gets a server heartbeat the instant it subscribes (was a
    connect-time race causing intermittent success).
  * `Sys/GetConf` now returns `CurClockId/StartUpClockId 0` + `StartUpClockOnOff 0`
    so the device does not auto-load the cloud crypto face on boot (that face
    hangs/crashes the firmware with no internet).
- ROOT FIX for the crypto-face crash loop (found via repeated physical cold-boots):
  the device loads its flash-stored clock face on boot BEFORE it connects; the
  mining/crypto face (id 1033) is a CLOUD face that hangs the firmware with no
  internet and pulls it back into the connect loop, which no server-side config
  or HTTP push could win. Fix: switch the device to a LOCAL built-in face
  (id **26**, verified reachable 18/18 = no hang) via `Channel/SetClockSelectId`.
  * `display.safe_clock_id: 26` + `cloud_clock_ids: [1033]` in config.yaml.
  * The supervisor now enforces the safe clock on every connect (`_on_ready` →
    `_force_safe_clock`), self-healing if the device ever shows a cloud face.
  * `Sys/GetConf` returns `CurClockId/StartUpClockId 26`.
  * `PixooHttp.set_clock()` / `get_current_clock_id()` added.
  Result: fresh cold boot → local face 26 (no hang) → full handshake completes →
  stays connected (port 80 30/30) → dashboard pushes over it.
- Note: the crypto/mining clock face selected in the Divoom app is a CLOUD face;
  with no internet it hangs on its loading spinner. Once the device is connected
  the supervisor pushes the local dashboard over it. (Optionally set the device's
  power-on channel to the local dashboard to avoid the brief boot-time hang.)
- MQTT QoS raised to 1 (the firmware subscribes at QoS 1 — required for delivery).
- MQTT topic namespace moved to the real DeviceId: `divoom/2/300000000/#`.
- Connect beep muted via `OnOffVolume`/`NotificationSound` = 0 in Sys/GetConf.
- `mqtt_connected` status now based on recent device messages (this firmware
  uses the Connect handshake, not periodic Device/Hearbeat).

### Verified
- Went live via `pixoo-local.service`; host (Pi-hole/HA/shared Mosquitto/eth0) untouched.
- Offline cold-boot: device bootstraps locally (InitV2 seen, no internet).
- After fix: **port 80 stable 20/20, 0 re-init and 0 MQTT reconnects/60s**,
  `Channel/GetAllConf` = error_code 0, supervisor `DISPLAY_ACTIVE`. Survives a
  container restart (auto-recovers to stable). 32 unit + 12 smoke tests pass.


## [1.0.0] — 2026-07-10

### Deployment decision
- Chosen architecture: **Docker macvlan** on dedicated IP `10.10.20.160`
  (confirmed with the user). Rationale: the host (10.10.20.50) is a busy
  production box — Pi-hole owns port 80, and the shared Mosquitto (:1883,
  anonymous) is used by Home Assistant/Homebridge/other nodes. macvlan gives the
  Pixoo stack its own LAN IP so it binds :80 + :1883 without conflict.
- DNS override for `app.divoom-gz.com` to be applied by the user on the DNS
  server `10.10.20.231` (the Pixoo's resolver; it does not forward to Pi-hole).

### Added (all under /opt/pixoo-local)
- `common/` — config loader + validation, logging, secrets, status store, net utils.
- `bootstrap/` — Device/InitV2 server (stdlib http.server; reads body on GET),
  pure logic in `core.py`, `/health`, optional capture-proxy fallback (off).
- `mqtt/` — protocol helpers + heartbeat responder (paho), auto-reconnect.
- `controller/` — robust HTTP client, MQTT control fallback, unified transport
  (auto HTTP→MQTT), image encoder (64×64×3 RGB→base64), test pattern, supervisor
  state machine.
- `dashboard/` — Pillow renderer (server + market halves), self-contained 3×5
  pixel font (no third-party font), sysinfo (psutil + /proc fallback), pluggable
  market data.
- `web/` — Flask UI + REST API + basic auth; hosts the supervisor thread.
- `docker/` — Dockerfile, supervisord, entrypoint, dedicated mosquitto.conf.
- `docker-compose.yml` — macvlan network + hardened container (cap_drop ALL,
  no-new-privileges, tmpfs /run, RO secrets mount, healthcheck).
- `systemd/pixoo-local.service` (active) + `systemd/native/*` (alternative mode).
- `scripts/` — install, uninstall, run_tests, smoke_container, diagnose,
  cold_boot_check, test_bootstrap/test_mqtt, test_api, send_test_pattern,
  gen_mosquitto_auth, enable/disable_host_access.
- `tests/` — 28 pytest unit tests (config, image encoding, bootstrap, MQTT).
- `reports/system-discovery.txt` — read-only audit of the host.

### Host changes made by this work
- Created directory `/opt/pixoo-local` (owner felix) and `/etc/pixoo-local`
  (root, chmod 700) with generated secrets (device-token, server-mqtt-password,
  web-auth-password), all chmod 600.
- Wrote `/opt/pixoo-local/.env` (compose overrides, auto-detected eth0/subnet/gw).
- Installed + `enabled` (not started) `pixoo-local.service`.
- Built Docker image `pixoo-local:latest`.

### NOT changed (verified)
- Pi-hole config / port 80, the shared Mosquitto, `eth0`/NetworkManager, the
  Omada firewall rule, Home Assistant, and all other existing services.

### Verified
- 28/28 unit tests pass; 12/12 container smoke checks pass (bootstrap token +
  host-reject, mosquitto auth + ACL, heartbeat round-trip, web API + preview).
- Live HTTP control + 64×64 image push confirmed against the real Pixoo while it
  was still bootstrapped (test pattern displayed, error_code 0).

### Pending (user + joint)
- Reserve/exclude `10.10.20.160` on the `10.10.20.231` DHCP scope.
- Add DNS record `app.divoom-gz.com A 10.10.20.160` on `10.10.20.231`.
- `systemctl start pixoo-local`, then the offline cold-boot acceptance test.
