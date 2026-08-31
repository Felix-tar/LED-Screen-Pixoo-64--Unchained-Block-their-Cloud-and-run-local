#!/usr/bin/env python3
"""Send the 64x64 acceptance test pattern to the Pixoo over the local HTTP API.

Resets the GIF id, sends the static test image, prints the response, and exits
0 ONLY on success (error_code 0). Uses config.yaml for the Pixoo IP.

Usage:  PYTHONPATH=/opt/pixoo-local python3 scripts/send_test_pattern.py
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/opt/pixoo-local")

from common.config import load as load_config
from controller.pixoo_http import PixooHttp, PixooError, PixooUnreachable, PixooBadResponse
from controller.test_pattern import build_test_image


def main() -> int:
    cfg = load_config()
    client = PixooHttp(
        cfg.http_base_url,
        connect_timeout=float(cfg.get("http_api", "connect_timeout", default=2.0)),
        read_timeout=float(cfg.get("http_api", "read_timeout", default=5.0)),
    )
    try:
        if not client.is_api_ready():
            print(f"FAIL: local API not ready at {cfg.http_base_url}", file=sys.stderr)
            return 2
        print("reset:", client.reset_gif_id())
        resp = client.send_frame(build_test_image(), pic_id=1)
        print("send_frame:", resp)
        print("OK: test pattern displayed on", cfg.pixoo_ip)
        return 0
    except (PixooUnreachable, PixooBadResponse) as e:
        print(f"FAIL: transport error: {e}", file=sys.stderr)
        return 3
    except PixooError as e:
        print(f"FAIL: device error_code={e.code}: {e}", file=sys.stderr)
        return 4
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
