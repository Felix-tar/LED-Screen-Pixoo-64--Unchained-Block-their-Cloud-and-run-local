"""Small network helpers (MAC normalization, own-IP detection)."""
from __future__ import annotations

import re
import socket


_MAC_RE = re.compile(r"[^0-9a-fA-F]")


def normalize_mac(mac: str | None) -> str:
    """Normalize a MAC to lowercase hex without separators.

    Accepts colon/dash separated, upper/lower case, or already-normalized input.
    Returns "" for falsy input. Raises ValueError if the cleaned value is not
    exactly 12 hex chars.
    """
    if not mac:
        return ""
    cleaned = _MAC_RE.sub("", str(mac)).lower()
    if len(cleaned) != 12:
        raise ValueError(f"invalid MAC address: {mac!r} -> {cleaned!r}")
    return cleaned


def macs_equal(a: str | None, b: str | None) -> bool:
    try:
        return normalize_mac(a) == normalize_mac(b) and bool(normalize_mac(a))
    except ValueError:
        return False


def tcp_probe(host: str, port: int, timeout: float = 1.0) -> str:
    """Classify reachability without ICMP/root.

    Returns one of:
      "open"    - TCP connect succeeded (service listening)
      "refused" - host answered with RST (up, port closed)
      "down"    - timeout / no route (treat as offline)
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return "open"
    except ConnectionRefusedError:
        return "refused"
    except (socket.timeout, TimeoutError):
        return "down"
    except OSError:
        return "down"
    finally:
        s.close()


def detect_own_ip(target: str = "10.10.20.254") -> str:
    """Best-effort detection of the LAN IP used to reach ``target``.

    Uses a connect-less UDP socket; no packets are actually sent. Falls back to
    the hostname resolution, then 127.0.0.1.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((target, 9))
        return s.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        s.close()
