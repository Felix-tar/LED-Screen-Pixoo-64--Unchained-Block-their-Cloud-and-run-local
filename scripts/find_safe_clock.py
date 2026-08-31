#!/usr/bin/env python3
"""Find a LOCAL clock face for display.safe_clock_id.

Some Divoom clock faces (crypto/mining, social counters, weather, …) are CLOUD
faces: with no internet they hang the firmware on boot and trap the device in a
reconnect loop. This helper sets each candidate face via the local HTTP API and
checks whether the device STAYS reachable (local) or hangs (cloud).

Usage:
    PYTHONPATH=/opt/pixoo-local python3 scripts/find_safe_clock.py            # scan defaults
    PYTHONPATH=/opt/pixoo-local python3 scripts/find_safe_clock.py 26 38 40   # scan given ids
    PYTHONPATH=/opt/pixoo-local python3 scripts/find_safe_clock.py --show 48  # hold face 48 for 20s to look at it

Pick a LOCAL face that looks like a plain clock, then set it:
    display.safe_clock_id: <id>   (config.yaml)  and re-run the stack.
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, "/opt/pixoo-local")
import requests  # noqa: E402

from common.config import load as load_config  # noqa: E402

cfg = load_config()
URL = cfg.http_base_url


def post(cmd, timeout=(1, 3)):
    try:
        return requests.post(URL, json=cmd, timeout=timeout).json()
    except Exception:
        return None


def reachable() -> bool:
    r = post({"Command": "Channel/GetAllConf"}, timeout=(1, 2))
    return bool(r and r.get("error_code") == 0)


def set_face(fid: int):
    post({"Command": "Channel/SetClockSelectId", "ClockId": int(fid)})


def scan(ids):
    local = []
    for fid in ids:
        if not reachable():
            time.sleep(1)
        set_face(fid)
        time.sleep(3)
        ok = sum(1 for _ in range(4) if (reachable() or time.sleep(0.5)))
        tag = "LOCAL " if ok >= 3 else "cloud "
        print(f"face {fid:>5}: {tag} ({ok}/4 reachable)")
        if ok >= 3:
            local.append(fid)
    print("\nLOCAL (safe) faces:", local)
    print("Look at the device while running with --show <id> to pick a plain clock.")
    return local


def show(fid: int, seconds: int = 20):
    print(f"holding face {fid} for {seconds}s — look at the Pixoo...")
    end = time.time() + seconds
    while time.time() < end:
        set_face(fid)
        time.sleep(0.5)
    print("done")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--show":
        show(int(args[1]), int(args[2]) if len(args) > 2 else 20)
    else:
        ids = [int(a) for a in args] if args else [
            26, 38, 40, 46, 48, 52, 53, 61, 62, 100, 102, 114, 116, 182, 258, 300, 400
        ]
        scan(ids)
