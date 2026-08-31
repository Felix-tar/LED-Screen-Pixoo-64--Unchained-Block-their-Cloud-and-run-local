# The GUI — walkthrough

Two LAN‑only web apps (basic auth `pixoo` / the password in
`/etc/pixoo-local/web-auth-password`). Open them from a phone or laptop on the LAN.

## Control UI — `http://<advertise_ip>:8090`

* **Status** (state, transport, brightness, last frame) + a live 64×64 **preview**.
* **Brightness** slider · **Display On/Off** · **Test pattern** · **Push dashboard**
  · **Recheck/Reconnect**.
* **⏰ Schedule** — global on/off toggle + a 7‑day grid: tick a day, set on/off with
  the native time picker, **Save**. Off dims the panel to 0; a manual on/off wins
  until the next scheduled edge.
* **📱 Smart Home** — QR codes (control page, Home Assistant), the auto‑discovered
  switch name + MQTT topics, and per‑service steps for Apple/Alexa/Google/Homebridge.

## Screen editor — `http://<advertise_ip>:8091`

Left: the **screens** list (add / reorder ▲▼ / delete). Middle: the selected
screen's **name / duration / background** and its **widgets** — add a type, set
`x/y/w/h` and the type‑specific props. Right: a **live preview** that updates as
you edit. **Save & Apply** writes `config/screens.json`; the device updates within
seconds.

Widget props worth knowing:

* **clock** — pick a city (auto‑fills the label); no timezone = the home zone
  (`display.timezone`), with automatic DST.
* **market** — `view: card` (one symbol at a time, cycling) or `rows`; the
  **symbols** editor picks/reorders which instruments show (empty = all) and adding
  a new one also adds it to the watchlist.
* **claude** — `feature` names the limit shown large (default the 5 h session).
* **kv / list / bar** — the **fields** editor has a path picker listing the JSON
  fields present in the source file/URL.

Header → **⚙ Data & Sources**:

* **Market** — the watchlist (crypto ids + stock tickers), reorder/add/remove, with
  a **🔍 test** button that fetches one symbol live and shows the value or the exact
  error.
* **Claude usage** — status (uses your Claude Code login automatically).
* **Live data** — view the current `data/*.json`.
* **Custom** — add your own MQTT topic / JSON URL source (see
  [CUSTOM-SOURCES.md](CUSTOM-SOURCES.md)).

Header → **⏰ Schedule** and **📱 Smart Home** are also here, next to Data & Sources.
The Smart Home panel includes a **Homebridge — one click** section: live status, an
**Install & activate** button (installs `homebridge-mqttthing`, adds the switch,
restarts Homebridge), the `sudo scripts/setup_homebridge.sh` command, and the full
manual accessory JSON.
