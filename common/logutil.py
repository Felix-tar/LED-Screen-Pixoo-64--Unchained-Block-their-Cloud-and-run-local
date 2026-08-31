"""Structured, journald-friendly logging setup.

Every service uses ``get_logger(name)`` so log lines look uniform and can be
filtered per service in ``journalctl``/container logs. Sensitive values (tokens,
long payloads) must be redacted by callers via :func:`redact` / :func:`shorten`.
"""
from __future__ import annotations

import logging
import os
import sys

_CONFIGURED = False


def configure(level: str | None = None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    lvl = (level or os.environ.get("PIXOO_LOG_LEVEL") or "INFO").upper()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(getattr(logging, lvl, logging.INFO))
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure()
    return logging.getLogger(name)


def redact(secret: str | None) -> str:
    """Return a non-reversible hint for a secret, safe to log."""
    if not secret:
        return "<none>"
    if len(secret) <= 6:
        return "***"
    return f"{secret[:2]}…{secret[-2:]}(len={len(secret)})"


def shorten(payload: str | bytes, limit: int = 240) -> str:
    """Truncate large/sensitive payloads for normal logs."""
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8", "replace")
        except Exception:
            payload = repr(payload)
    if len(payload) <= limit:
        return payload
    return f"{payload[:limit]}…(+{len(payload) - limit} chars)"
