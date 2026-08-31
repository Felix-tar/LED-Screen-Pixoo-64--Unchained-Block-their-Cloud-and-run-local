"""Secret file access.

Secrets are stored one-per-file with chmod 600 under the secrets dir
(default /etc/pixoo-local). They are generated once by install.sh and never
committed to git or written into config.yaml.
"""
from __future__ import annotations

import os
from pathlib import Path


class SecretError(RuntimeError):
    pass


def read_secret(path: str | os.PathLike, *, required: bool = True) -> str:
    p = Path(path)
    if not p.exists():
        if required:
            raise SecretError(f"required secret file missing: {p}")
        return ""
    value = p.read_text(encoding="utf-8").strip()
    if required and not value:
        raise SecretError(f"secret file is empty: {p}")
    return value


def secret_present(path: str | os.PathLike) -> bool:
    p = Path(path)
    return p.exists() and bool(p.read_text(encoding="utf-8").strip())
