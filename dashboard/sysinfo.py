"""Local system metrics for the dashboard.

Prefers psutil; degrades gracefully to /proc and /sys so a single failing
source never blocks the whole dashboard.
"""
from __future__ import annotations

import os
import time

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover - psutil always present in container
    psutil = None

_last_cpu_call = {"t": 0.0}


def cpu_percent() -> float:
    if psutil:
        # non-blocking; first call returns 0.0 then meaningful values
        return float(psutil.cpu_percent(interval=None))
    try:
        with open("/proc/loadavg") as f:
            load1 = float(f.read().split()[0])
        ncpu = os.cpu_count() or 1
        return min(100.0, 100.0 * load1 / ncpu)
    except Exception:
        return 0.0


def mem_percent() -> float:
    if psutil:
        return float(psutil.virtual_memory().percent)
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                info[k] = float(v.strip().split()[0])
        total = info.get("MemTotal", 0)
        avail = info.get("MemAvailable", info.get("MemFree", 0))
        if total <= 0:
            return 0.0
        return 100.0 * (total - avail) / total
    except Exception:
        return 0.0


def disk_percent(path: str = "/") -> float:
    if psutil:
        return float(psutil.disk_usage(path).percent)
    try:
        st = os.statvfs(path)
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        if total <= 0:
            return 0.0
        return 100.0 * (total - free) / total
    except Exception:
        return 0.0


def temperature_c() -> float | None:
    # try common thermal zones (Raspberry Pi -> thermal_zone0)
    for zone in ("/sys/class/thermal/thermal_zone0/temp",
                 "/sys/class/thermal/thermal_zone1/temp"):
        try:
            with open(zone) as f:
                raw = int(f.read().strip())
            return raw / 1000.0 if raw > 1000 else float(raw)
        except Exception:
            continue
    if psutil and hasattr(psutil, "sensors_temperatures"):
        try:
            temps = psutil.sensors_temperatures()
            for entries in temps.values():
                if entries:
                    return float(entries[0].current)
        except Exception:
            pass
    return None


def uptime_seconds() -> float:
    try:
        with open("/proc/uptime") as f:
            return float(f.read().split()[0])
    except Exception:
        return 0.0


def collect(disk_path: str = "/") -> dict:
    cpu = cpu_percent()
    ram = mem_percent()
    disk = disk_percent(disk_path)
    temp = temperature_c()
    healthy = cpu < 92 and ram < 92 and disk < 95 and (temp is None or temp < 78)
    return {
        "cpu": round(cpu, 1),
        "ram": round(ram, 1),
        "disk": round(disk, 1),
        "temp_c": round(temp, 1) if temp is not None else None,
        "uptime_s": int(uptime_seconds()),
        "healthy": healthy,
        "ts": int(time.time()),
    }
