"""Supervisor: readiness state machine + automatic dashboard push.

Handles the smart-plug / full-power-loss cycle without manual intervention:
detects when the Pixoo comes back, waits for the local API (or MQTT) to be
ready, then sets brightness and pushes the current frame. Runs two threads that
share one transport:

  * readiness loop  — variable cadence (fast during boot, slow when stable)
  * dashboard loop  — steady cadence, only pushes when a frame changed

States: OFFLINE, PING_ONLY, BOOTSTRAP_SEEN, MQTT_CONNECTED, API_READY,
DISPLAY_ACTIVE, ERROR.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from common import status
from common.logutil import get_logger
from common.netutil import tcp_probe
from controller.image import frame_hash

log = get_logger("supervisor")

_DATA_DIR = os.environ.get("PIXOO_DATA_DIR", "/opt/pixoo-local/data")
_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

OFFLINE = "OFFLINE"
PING_ONLY = "PING_ONLY"
BOOTSTRAP_SEEN = "BOOTSTRAP_SEEN"
MQTT_CONNECTED = "MQTT_CONNECTED"
API_READY = "API_READY"
DISPLAY_ACTIVE = "DISPLAY_ACTIVE"
ERROR = "ERROR"

# freshness windows (seconds)
HEARTBEAT_FRESH = 90
BOOTSTRAP_FRESH = 300


class Supervisor:
    def __init__(self, cfg, transport, frame_provider=None):
        self.cfg = cfg
        self.transport = transport
        self.frame_provider = frame_provider  # callable -> PIL.Image | None
        self.pixoo_ip = cfg.pixoo_ip

        self.state = OFFLINE
        self._display_pushed = False
        self._last_frame_hash = None
        self._last_frame_sent_ts = None
        self._last_transition_ts = time.time()
        # After a (re)start or any device drop, poll fast for this long so the
        # Pixoo reconnects ASAP after a power cut. Frequent probing also keeps the
        # macvlan ARP entry for the device warm (avoids "No route to host" stalls).
        self.fast_recover_seconds = int(cfg.get("display", "fast_recover_seconds", default=180))
        self._fast_boot_until = time.time() + self.fast_recover_seconds
        self.brightness = int(cfg.get("display", "default_brightness", default=30))
        self.safe_clock_id = cfg.get("display", "safe_clock_id", default=None)
        self.cloud_clock_ids = set(cfg.get("display", "cloud_clock_ids", default=[]) or [])

        self.system_update_seconds = int(cfg.get("display", "system_update_seconds", default=5))
        self.max_fps = float(cfg.get("display", "maximum_frame_rate", default=1.0))
        self.stable_interval = int(cfg.get("display", "supervisor_stable_seconds", default=20))
        self.dashboard_enabled = bool(cfg.get("dashboard", "enabled", default=True))

        # screen on/off: when off we stop pushing frames (a push would wake it) and
        # keep the panel off. Controlled by the schedule, the web API, and (via a
        # command file the host HA-bridge writes) Apple Home / Alexa / Google.
        self.screen_on_flag = True
        self._screen_source = "init"
        self._screen_state_file = os.path.join(_DATA_DIR, "screen_state.json")
        self._screen_cmd_file = os.path.join(_DATA_DIR, "screen_cmd.json")
        self._cmd_last_ts = 0.0
        self._sched_fired: dict[str, str] = {}
        # schedule lives in data/schedule.json (editable in the web UI, writable by
        # the container); falls back to config.yaml's schedule. Hot-reloaded.
        self._schedule_path = os.path.join(_DATA_DIR, "schedule.json")
        self._schedule_mtime = 0.0
        self.schedule_enabled = False
        self.schedule_tz = "Europe/Berlin"
        self.schedule_days: dict = {}
        self._load_schedule()

        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._threads: list[threading.Thread] = []

    # -- helpers ---------------------------------------------------------
    def _set_state(self, new: str):
        if new != self.state:
            prev = self.state
            log.info("state %s -> %s", self.state, new)
            self.state = new
            self._last_transition_ts = time.time()
            if new not in (API_READY, DISPLAY_ACTIVE):
                self._display_pushed = False
            # the device just dropped -> re-arm fast polling so it reconnects ASAP
            if new == OFFLINE and prev != OFFLINE:
                self._fast_boot_until = time.time() + self.fast_recover_seconds
        self._publish_status()

    def _publish_status(self):
        hb = status.read("heartbeat")
        boot = status.read("bootstrap")
        data = {
            "service": "supervisor",
            "pixoo_ip": self.pixoo_ip,
            "state": self.state,
            "ping": self.state != OFFLINE,
            "http_api": self.state in (API_READY, DISPLAY_ACTIVE),
            "mqtt_connected": bool(hb.get("mqtt_connected"))
            and (self._fresh(hb.get("last_heartbeat_ts"), HEARTBEAT_FRESH)
                 or self._fresh(hb.get("last_device_msg_ts"), HEARTBEAT_FRESH)),
            "transport": self._active_transport_safe(),
            "brightness": self.brightness,
            "screen_on": self.screen_on_flag,
            "screen_source": self._screen_source,
            "last_bootstrap_request": boot.get("last_request_iso"),
            "last_frame_sent": self._iso(self._last_frame_sent_ts),
            "updated_ts": int(time.time()),
        }
        status.write("supervisor", data)

    def _active_transport_safe(self) -> str:
        try:
            return self.transport.active_transport()
        except Exception:
            return "unknown"

    @staticmethod
    def _fresh(ts, window) -> bool:
        return bool(ts) and (time.time() - float(ts)) <= window

    @staticmethod
    def _iso(ts):
        if not ts:
            return None
        return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")

    # -- readiness evaluation -------------------------------------------
    def _evaluate(self) -> str:
        probe = tcp_probe(self.pixoo_ip, 80, timeout=1.0)
        if probe == "down":
            return OFFLINE

        http_ready = False
        if probe == "open":
            try:
                http_ready = self.transport.http.is_api_ready()
            except Exception:
                http_ready = False
        if http_ready:
            return DISPLAY_ACTIVE if self._display_pushed else API_READY

        hb = status.read("heartbeat")
        if self._fresh(hb.get("last_heartbeat_ts"), HEARTBEAT_FRESH):
            return MQTT_CONNECTED

        boot = status.read("bootstrap")
        if self._fresh(boot.get("last_request_ts"), BOOTSTRAP_FRESH):
            return BOOTSTRAP_SEEN

        return PING_ONLY

    def _force_safe_clock(self):
        """Switch the device off any cloud face onto the safe LOCAL clock, so
        the firmware never hangs loading cloud data offline (that hang is what
        pulls the device back into the connect loop)."""
        if not self.safe_clock_id:
            return
        try:
            cur = self.transport.http.get_current_clock_id()
            if cur is not None and cur != int(self.safe_clock_id) and \
                    (not self.cloud_clock_ids or cur in self.cloud_clock_ids):
                self.transport.http.set_clock(int(self.safe_clock_id))
                log.info("forced clock %s -> safe local clock %s", cur, self.safe_clock_id)
        except Exception as e:
            log.warning("could not enforce safe clock: %s", e)

    def _on_ready(self):
        """Rising edge into API_READY: force safe clock + set brightness, then push
        a frame — unless the screen is meant to be off, in which case keep it off."""
        self._force_safe_clock()
        if not self.screen_on_flag:      # meant to be off -> keep it dark, don't push
            try:
                self.transport.set_brightness(0)
                self.transport.screen_off()
            except Exception:
                pass
            return
        try:
            self.transport.set_brightness(self.brightness)
        except Exception as e:
            log.warning("could not set brightness on ready: %s", e)
        pushed = self.push_dashboard(force=True)
        if pushed:
            self._display_pushed = True
            self._set_state(DISPLAY_ACTIVE)

    # -- dashboard push --------------------------------------------------
    def push_dashboard(self, *, force: bool = False) -> bool:
        if not self.frame_provider:
            return False
        try:
            img = self.frame_provider()
        except Exception as e:
            log.error("frame_provider failed: %s", e)
            return False
        if img is None:
            return False
        h = frame_hash(img)
        now = time.time()
        min_interval = 1.0 / self.max_fps if self.max_fps > 0 else 0
        if not force:
            if h == self._last_frame_hash:
                return False  # nothing changed
            if self._last_frame_sent_ts and (now - self._last_frame_sent_ts) < min_interval:
                return False  # rate limit
        try:
            with self._lock:
                self.transport.reset_gif_id()
                self.transport.send_frame(img)
            self._last_frame_hash = h
            self._last_frame_sent_ts = now
            self._publish_status()
            return True
        except Exception as e:
            log.warning("frame push failed: %s", e)
            return False

    def send_test_pattern(self) -> bool:
        from controller.test_pattern import build_test_image
        try:
            with self._lock:
                self.transport.reset_gif_id()
                self.transport.send_frame(build_test_image())
            self._last_frame_hash = None  # force dashboard to redraw next cycle
            self._last_frame_sent_ts = time.time()
            self._publish_status()
            return True
        except Exception as e:
            log.error("test pattern failed: %s", e)
            return False

    # -- external control (web) -----------------------------------------
    def set_brightness(self, value: int):
        self.brightness = int(value)
        self.transport.set_brightness(self.brightness)
        self._publish_status()

    def set_screen(self, on: bool, source: str = "manual"):
        """Turn the panel on/off and remember it, so the dashboard loop stops
        pushing while off (a push would wake the screen) and a reconnect restores
        the right state. Also written to a file the host HA-bridge mirrors to HA."""
        on = bool(on)
        self.screen_on_flag = on
        self._screen_source = source
        # This firmware's OnOffScreen is unreliable (LightSwitch stays 0 and the
        # panel keeps showing pushed frames), so brightness is the real lever:
        # 0 = dark, restore = on. OnOffScreen is still sent best-effort.
        try:
            with self._lock:
                if on:
                    self.transport.set_brightness(self.brightness)
                    self.transport.screen_on()
                else:
                    self.transport.set_brightness(0)
                    self.transport.screen_off()
        except Exception as e:
            log.warning("set_screen(%s) transport error: %s", on, e)
        log.info("screen -> %s (source=%s)", "ON" if on else "OFF", source)
        if on and self.state in (API_READY, DISPLAY_ACTIVE):
            self._last_frame_hash = None          # force a fresh frame on wake
            self.push_dashboard(force=True)
        self._write_screen_state()
        self._publish_status()

    def screen_on(self):
        self.set_screen(True, "manual")

    def screen_off(self):
        self.set_screen(False, "manual")

    def _write_screen_state(self):
        try:
            os.makedirs(_DATA_DIR, exist_ok=True)
            tmp = self._screen_state_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"on": self.screen_on_flag, "source": self._screen_source,
                           "updated": time.time()}, f)
            os.replace(tmp, self._screen_state_file)
        except Exception as e:
            log.debug("write screen_state failed: %s", e)

    def reconnect(self):
        log.info("manual reconnect requested")
        self._last_frame_hash = None
        self._display_pushed = False
        self.transport.active_transport(force=True)

    def power_on(self):
        """Smart-home hook: enter fast boot monitoring."""
        log.info("power-on hook: fast boot monitoring for 180s")
        self._fast_boot_until = time.time() + 180
        self._display_pushed = False
        self._last_frame_hash = None

    def power_off(self):
        """Smart-home hook: persist state, optionally blank the screen."""
        log.info("power-off hook")
        try:
            self.transport.screen_off()
        except Exception:
            pass
        self._set_state(OFFLINE)

    def snapshot(self) -> dict:
        return status.read("supervisor")

    # -- loops -----------------------------------------------------------
    def _interval(self) -> float:
        if time.time() < self._fast_boot_until:
            return 1.0
        if self.state in (OFFLINE, PING_ONLY):
            return 2.0        # still responsive long after a drop (was 5s)
        if self.state in (BOOTSTRAP_SEEN, MQTT_CONNECTED):
            return 1.0
        return float(self.stable_interval)

    def _readiness_loop(self):
        while not self._stop.is_set():
            try:
                prev = self.state
                new = self._evaluate()
                # rising edge into readiness -> push frame
                if new == API_READY and prev not in (API_READY, DISPLAY_ACTIVE):
                    self._set_state(API_READY)
                    self._on_ready()
                else:
                    self._set_state(new)
            except Exception as e:
                log.error("readiness loop error: %s", e)
                self._set_state(ERROR)
            self._stop.wait(self._interval())

    def _dashboard_loop(self):
        while not self._stop.is_set():
            try:
                if self.dashboard_enabled and self.screen_on_flag and \
                        self.state in (API_READY, DISPLAY_ACTIVE):
                    if self.push_dashboard(force=False) and self.state == API_READY:
                        self._display_pushed = True
                        self._set_state(DISPLAY_ACTIVE)
            except Exception as e:
                log.error("dashboard loop error: %s", e)
            # retry quickly until the first frame lands, then settle to the normal rate
            pending = self.state == API_READY and not self._display_pushed
            self._stop.wait(1.0 if pending else self.system_update_seconds)

    # -- schedule + external (smart-home) screen control ----------------
    def _load_schedule(self):
        try:
            st = os.stat(self._schedule_path)
        except OSError:
            st = None
        if st is not None:
            if st.st_mtime == self._schedule_mtime:
                return
            try:
                with open(self._schedule_path, encoding="utf-8") as f:
                    data = json.load(f)
                self._schedule_mtime = st.st_mtime
            except Exception as e:
                log.warning("schedule.json load failed: %s", e)
                return
        else:
            data = {"enabled": bool(self.cfg.get("schedule", "enabled", default=False)),
                    "timezone": self.cfg.get("schedule", "timezone", default="Europe/Berlin"),
                    "days": self.cfg.get("schedule", "days", default={}) or {}}
        self.schedule_enabled = bool(data.get("enabled", False))
        self.schedule_tz = data.get("timezone", "Europe/Berlin")
        self.schedule_days = data.get("days", {}) or {}

    def reload_schedule(self):
        """Re-read the schedule and immediately match the panel to it (web UI save)."""
        self._schedule_mtime = 0.0
        self._load_schedule()
        self._enforce_schedule_now()

    def _day_cfg(self, now) -> dict:
        return self.schedule_days.get(_WEEKDAYS[now.weekday()], {}) or {}

    def _enforce_schedule_now(self):
        """At startup, match the panel to the schedule's current window."""
        if not self.schedule_enabled:
            return
        try:
            now = datetime.now(ZoneInfo(self.schedule_tz))
            dc = self._day_cfg(now)
            on_t, off_t = dc.get("on"), dc.get("off")
            if on_t and off_t:
                cur = now.strftime("%H:%M")
                target = (on_t <= cur < off_t) if on_t <= off_t else (cur >= on_t or cur < off_t)
                self.set_screen(target, "schedule")
        except Exception as e:
            log.warning("schedule enforce failed: %s", e)

    def _schedule_loop(self):
        while not self._stop.is_set():
            try:
                self._load_schedule()          # pick up web-UI edits
                if self.schedule_enabled:
                    now = datetime.now(ZoneInfo(self.schedule_tz))
                    dc = self._day_cfg(now)
                    hhmm = now.strftime("%H:%M")
                    for kind, on in (("on", True), ("off", False)):
                        if dc.get(kind) == hhmm:
                            key = f"{now.date()}:{kind}"
                            if self._sched_fired.get(kind) != key:
                                self._sched_fired[kind] = key
                                log.info("schedule %s at %s", "ON" if on else "OFF", hhmm)
                                self.set_screen(on, "schedule")
            except Exception as e:
                log.error("schedule loop error: %s", e)
            self._stop.wait(20)

    def _command_loop(self):
        """Apply screen commands the host HA-bridge writes (Apple/Alexa/Google)."""
        while not self._stop.is_set():
            try:
                with open(self._screen_cmd_file, encoding="utf-8") as f:
                    cmd = json.load(f)
                ts = float(cmd.get("ts", 0))
                if ts > self._cmd_last_ts:
                    self._cmd_last_ts = ts
                    st = str(cmd.get("state", "")).lower()
                    if st in ("on", "off", "toggle"):
                        on = (st == "on") or (st == "toggle" and not self.screen_on_flag)
                        self.set_screen(on, cmd.get("source", "smart-home"))
            except FileNotFoundError:
                pass
            except Exception as e:
                log.debug("command loop: %s", e)
            self._stop.wait(2)

    def start(self):
        self._publish_status()
        self._enforce_schedule_now()
        self._write_screen_state()
        t1 = threading.Thread(target=self._readiness_loop, name="supervisor-readiness", daemon=True)
        t2 = threading.Thread(target=self._dashboard_loop, name="supervisor-dashboard", daemon=True)
        t3 = threading.Thread(target=self._schedule_loop, name="supervisor-schedule", daemon=True)
        t4 = threading.Thread(target=self._command_loop, name="supervisor-command", daemon=True)
        self._threads = [t1, t2, t3, t4]
        for t in self._threads:
            t.start()
        log.info("supervisor started (pixoo=%s, dashboard=%s)", self.pixoo_ip, self.dashboard_enabled)

    def run(self):
        self.start()
        try:
            while not self._stop.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        self._stop.set()
        for t in self._threads:
            t.join(timeout=3)


def main():  # pragma: no cover - standalone mode
    from common.config import load as load_config
    from controller.transport import PixooTransport
    cfg = load_config()
    transport = PixooTransport(cfg)
    frame_provider = None
    if bool(cfg.get("dashboard", "enabled", default=True)):
        from dashboard.renderer import DashboardRenderer
        renderer = DashboardRenderer(cfg)
        frame_provider = renderer.render
    Supervisor(cfg, transport, frame_provider=frame_provider).run()


if __name__ == "__main__":
    main()
