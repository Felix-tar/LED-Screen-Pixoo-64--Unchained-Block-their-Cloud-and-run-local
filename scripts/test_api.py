#!/usr/bin/env python3
"""Non-destructive live check of the Pixoo local HTTP API.

Reads config, calls GetAllConf, then does a brightness round-trip that restores
the original value. Exits 0 on success. Does NOT change what is displayed.

Usage:  PYTHONPATH=/opt/pixoo-local python3 scripts/test_api.py
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/opt/pixoo-local")

from common.config import load as load_config
from controller.pixoo_http import PixooHttp


def main() -> int:
    cfg = load_config()
    c = PixooHttp(cfg.http_base_url)
    try:
        conf = c.get_config()
        print("GetAllConf error_code:", conf.get("error_code"))
        orig = int(conf.get("Brightness", 30))
        print("current brightness:", orig)
        target = 100 - orig if orig != 50 else 50  # a different value, then restore
        print("set brightness ->", target, ":", c.set_brightness(target))
        print("restore brightness ->", orig, ":", c.set_brightness(orig))
        print("OK: HTTP API works and brightness restored")
        return 0
    except Exception as e:
        print(f"FAIL: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
