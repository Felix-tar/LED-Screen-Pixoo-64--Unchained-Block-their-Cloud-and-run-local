"""Pure, unit-testable bootstrap logic (no sockets).

The Pixoo, on cold boot, requests ``/Device/InitV2`` from app.divoom-gz.com
(port 80) — a GET that carries a JSON body — and expects a JSON reply carrying
the MQTT server IP, the current Unix time, its device id and an MQTT token.
This module builds that reply. Everything here is deterministic given a fixed
``now_ts`` so tests can assert exact output.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
from dataclasses import dataclass
from typing import Any

from common.netutil import normalize_mac
from common.tokens import device_jwt


@dataclass
class BootstrapSettings:
    advertise_ip: str          # MQTT server IP returned to the Pixoo ("IP")
    device_id: int
    timezone_code: str
    summer_zone: int
    last_clock_id: int
    configured_mac: str        # normalized MAC of our Pixoo
    device_token: str          # real MQTT token (== broker password); NEVER log per-request
    allowed_hosts: list[str]
    allow_direct_ip_host: bool
    # Extra identity/state fields the real server returns. The firmware compares
    # these against its stored provisioning; if they are missing it keeps
    # re-running InitV2 (and reconnecting MQTT) every ~28s. Defaults keep the
    # unit tests and older configs working.
    user_id: int = 0
    backup_ip: str = ""
    screen_on_off: int = 1
    custom_type: int = 0
    lot: float = 0.0
    lat: float = 0.0
    # A PUBLIC IP to report to the firmware's connectivity checks (InitV2
    # DevicePublicIP + /Test/GetIP). Returning the device's private LAN IP makes
    # the firmware believe it is not really online and re-run InitV2 in a loop.
    public_ip: str = ""


def parse_request_json(raw: bytes | str | None) -> dict[str, Any]:
    """Leniently parse the request body. Returns {} on empty/invalid input.

    A GET request may still carry a JSON body, which is why callers must read
    the body regardless of method.
    """
    if not raw:
        return {}
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8", "replace")
        except Exception:
            return {}
    raw = raw.strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def extract_mac(req: dict[str, Any]) -> str | None:
    """Extract + normalize the MAC from a request body (case-insensitive keys)."""
    for key in ("DeviceMacAddr", "deviceMacAddr", "MacAddr", "Mac", "mac"):
        if key in req and req[key]:
            try:
                return normalize_mac(str(req[key]))
            except ValueError:
                return None
    # some firmwares send arbitrary key casing
    for k, v in req.items():
        if k.lower().replace("_", "") in ("devicemacaddr", "macaddr", "mac") and v:
            try:
                return normalize_mac(str(v))
            except ValueError:
                return None
    return None


def _host_only(host_header: str | None) -> str:
    if not host_header:
        return ""
    return host_header.split(":", 1)[0].strip().lower()


def is_host_allowed(host_header: str | None, settings: BootstrapSettings) -> bool:
    host = _host_only(host_header)
    if not host:
        # Some minimal ESP clients omit Host; accept (the DNS override already
        # guarantees only intended clients reach us) but this is logged.
        return True
    if host in (h.lower() for h in settings.allowed_hosts):
        return True
    if settings.allow_direct_ip_host:
        try:
            if ipaddress.ip_address(host) == ipaddress.ip_address(settings.advertise_ip):
                return True
        except ValueError:
            pass
    return False


def token_for_mac(mac: str | None, settings: BootstrapSettings) -> str:
    """Real token only for the configured Pixoo; deterministic non-authorizing
    token for any other device so unknown devices never receive our credentials.
    """
    if mac and settings.configured_mac and mac == settings.configured_mac:
        # JWT carrying the device username (MAC); used as the MQTT password too.
        return device_jwt(mac, settings.device_token)
    seed = (settings.device_token or "seed").encode("utf-8")
    digest = hmac.new(seed, (mac or "unknown").encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()[:32]


def build_response(
    req: dict[str, Any],
    *,
    remote_ip: str,
    settings: BootstrapSettings,
    now_ts: int,
) -> dict[str, Any]:
    """Build the InitV2 JSON response body."""
    mac = extract_mac(req)
    token = token_for_mac(mac, settings)
    packet_flag = req.get("PacketFlag", 0)
    try:
        packet_flag = int(packet_flag)
    except (TypeError, ValueError):
        packet_flag = 0

    device_public_ip = settings.public_ip or remote_ip or settings.advertise_ip
    backup_ip = settings.backup_ip or settings.advertise_ip
    # Field order/content mirrors the real app.divoom-gz.com InitV2 reply so the
    # firmware treats the device as fully provisioned and stops the re-init loop.
    return {
        "ReturnCode": 0,
        "ReturnMessage": "",
        "DevicePublicIP": device_public_ip,
        "IP": settings.advertise_ip,
        "BackupIP": backup_ip,
        "lot": settings.lot,
        "lat": settings.lat,
        "SummerZone": int(settings.summer_zone),
        "TimeZoneCode": settings.timezone_code,
        "UTCTime": int(now_ts),
        "DeviceId": int(settings.device_id),
        "UserId": int(settings.user_id),
        "LogLevel": 0,
        "IsResetAll": 0,
        "DeviceToken": token,
        "ServerType": 1,
        "LastClockId": int(settings.last_clock_id),
        "OfflineTime": int(now_ts) - 1800,
        "OnlineTime": int(now_ts) - 3600,
        "ScreenOnOff": int(settings.screen_on_off),
        "CustomType": int(settings.custom_type),
        "Command": "Device/InitV2",
        "PacketFlag": packet_flag,
    }
