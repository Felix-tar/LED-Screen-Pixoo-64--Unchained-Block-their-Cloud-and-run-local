#!/usr/bin/env python3
"""Generate the dedicated broker's password + ACL files from config + secrets.

Called by the container entrypoint at start. Writes:
  /run/pixoo/passwd   (mosquitto_passwd hashes for the Pixoo + server users)
  /run/pixoo/acl      (both users limited to the device topic subtree)

Secrets are read from the mounted secrets dir; nothing is printed.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/opt/pixoo-local")

from common.config import load as load_config  # noqa: E402
from common.secrets import read_secret  # noqa: E402
from common.tokens import device_jwt  # noqa: E402

RUN = Path(os.environ.get("PIXOO_RUN_DIR", "/run/pixoo"))
PASSWD = RUN / "passwd"
ACL = RUN / "acl"


def main() -> int:
    cfg = load_config()
    RUN.mkdir(parents=True, exist_ok=True)

    device_user = cfg.get("mqtt", "device_username")
    server_user = cfg.get("mqtt", "server_username", default="pixoo-server")
    prefix = cfg.topic_prefix

    # The device's MQTT password == the JWT DeviceToken returned by the bootstrap
    # (both derived from the same device-token secret, so they always match).
    device_token_secret = read_secret(cfg.secret_path(cfg.get("mqtt", "device_token_file")))
    device_pw = device_jwt(device_user, device_token_secret)
    server_pw = read_secret(cfg.secret_path(cfg.get("mqtt", "server_password_file")))

    # build password file (mosquitto_passwd -c creates, then add second user)
    if PASSWD.exists():
        PASSWD.unlink()
    subprocess.run(["mosquitto_passwd", "-c", "-b", str(PASSWD), device_user, device_pw],
                   check=True)
    subprocess.run(["mosquitto_passwd", "-b", str(PASSWD), server_user, server_pw],
                   check=True)

    # The firmware also subscribes to the GLOBAL server-heartbeat topic
    # divoom/2/DeviceHeart and stays connected only while it receives messages
    # there. Allow it (device: read; server: write) alongside the device subtree.
    heart = prefix.rsplit("/", 1)[0] + "/DeviceHeart"  # e.g. divoom/2/DeviceHeart
    acl = (
        f"# generated — do not edit\n"
        f"user {device_user}\n"
        f"topic readwrite {prefix}/#\n"
        f"topic read {heart}\n\n"
        f"user {server_user}\n"
        f"topic readwrite {prefix}/#\n"
        f"topic readwrite {heart}\n"
    )
    ACL.write_text(acl, encoding="utf-8")

    # readable only by the mosquitto user
    for p in (PASSWD, ACL):
        os.chmod(p, 0o640)
    try:
        import pwd
        mq = pwd.getpwnam("mosquitto")
        for p in (PASSWD, ACL):
            os.chown(p, mq.pw_uid, mq.pw_gid)
    except (KeyError, PermissionError):
        pass

    print(f"generated {PASSWD} and {ACL} for users: {device_user}, {server_user}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
