"""One-time capture/proxy fallback (task section 11).

DISABLED by default. When enabled it forwards the Pixoo's InitV2 request to the
REAL Divoom server (over a pinned upstream IP, keeping the Host header), saves
request + response (with secrets masked) for analysis, and returns the real
response so the exact upstream behaviour can be observed once. The final
operating mode must stay fully local — turn this off again after analysis.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import requests

from common.logutil import get_logger

log = get_logger("bootstrap.capture")

# The real app.divoom-gz.com A record observed during discovery. Configurable so
# it can be re-resolved via an EXTERNAL resolver before the DNS override is live.
DEFAULT_UPSTREAM_IP = "47.88.33.110"

_TOKEN_RE = re.compile(r'("DeviceToken"\s*:\s*")[^"]+(")')
_PUBIP_RE = re.compile(r'("DevicePublicIP"\s*:\s*")[^"]+(")')


def _mask(text: str) -> str:
    text = _TOKEN_RE.sub(r"\1<MASKED>\2", text)
    text = _PUBIP_RE.sub(r"\1<MASKED>\2", text)
    return text


def make_capture(cfg):
    upstream_ip = cfg.get("capture_proxy", "upstream_ip", default=DEFAULT_UPSTREAM_IP)
    host = (cfg.get("bootstrap", "allowed_hosts") or ["app.divoom-gz.com"])[0]
    save_dir = Path(cfg.get("capture_proxy", "save_dir",
                            default="/opt/pixoo-local/reports/bootstrap-capture"))
    save_dir.mkdir(parents=True, exist_ok=True)
    # a monotonically-increasing counter avoids Date.now()-style nondeterminism issues
    counter = {"n": 0}

    def capture(raw_body: bytes, headers: dict):
        counter["n"] += 1
        stamp = f"{int(time.time())}-{counter['n']:03d}"
        url = f"http://{upstream_ip}/Device/InitV2"
        fwd_headers = {
            "Host": host,
            "Content-Type": headers.get("Content-Type", "application/json"),
            "User-Agent": headers.get("User-Agent", "ESP32 HTTP Client/1.0"),
        }
        # Avoid proxy loops: never forward if we are the upstream.
        try:
            resp = requests.request(
                "GET", url, headers=fwd_headers, data=raw_body or b"", timeout=(3, 8),
            )
        except requests.RequestException as e:
            log.error("capture: upstream request failed: %s", e)
            (save_dir / f"{stamp}-error.txt").write_text(str(e))
            return None

        req_text = raw_body.decode("utf-8", "replace") if raw_body else ""
        (save_dir / f"{stamp}-request.json").write_text(_mask(req_text) or "{}")
        (save_dir / f"{stamp}-response.json").write_text(_mask(resp.text))
        log.warning("capture: saved upstream exchange %s (status=%s)", stamp, resp.status_code)

        try:
            return resp.json()
        except ValueError:
            return None

    return capture
