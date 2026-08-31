"""Tiny shared status store.

The bootstrap, heartbeat, controller/supervisor and web run as separate
processes (under supervisord in the container). They share state through small
JSON files in a status dir written atomically. The web aggregates them for
/api/status. This avoids any cross-process DB while staying crash-safe.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

STATUS_DIR = Path(os.environ.get("PIXOO_STATUS_DIR", "/run/pixoo/status"))


def _dir() -> Path:
    try:
        STATUS_DIR.mkdir(parents=True, exist_ok=True)
        return STATUS_DIR
    except OSError:
        # fallback for host-side tests without /run/pixoo
        alt = Path(tempfile.gettempdir()) / "pixoo-status"
        alt.mkdir(parents=True, exist_ok=True)
        return alt


def write(name: str, data: dict[str, Any]) -> None:
    d = _dir()
    target = d / f"{name}.json"
    fd, tmp = tempfile.mkstemp(dir=str(d), prefix=f".{name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, target)
        # status carries NO secrets; make it readable across the container's
        # users (root bootstrap writes, non-root web reads).
        try:
            os.chmod(target, 0o644)
        except OSError:
            pass
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read(name: str) -> dict[str, Any]:
    p = _dir() / f"{name}.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def read_all() -> dict[str, dict]:
    out: dict[str, dict] = {}
    d = _dir()
    for p in d.glob("*.json"):
        try:
            out[p.stem] = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            out[p.stem] = {}
    return out
