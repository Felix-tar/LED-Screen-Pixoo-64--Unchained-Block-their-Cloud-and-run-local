"""Config loading + validation for pixoo-local.

Loads the YAML config (path from $PIXOO_CONFIG or the default), validates the
values that would otherwise cause a confusing half-broken start, and exposes a
small typed-ish accessor. On error it raises :class:`ConfigError` with a clear
message and never prints secrets.
"""
from __future__ import annotations

import ipaddress
import os
from pathlib import Path
from typing import Any

import yaml

from .netutil import normalize_mac

DEFAULT_CONFIG_PATH = "/opt/pixoo-local/config/config.yaml"


class ConfigError(ValueError):
    pass


def _require(d: dict, key: str, ctx: str):
    if key not in d:
        raise ConfigError(f"missing required config key: {ctx}.{key}")
    return d[key]


def _check_ip(value: str, ctx: str) -> str:
    try:
        ipaddress.IPv4Address(value)
    except (ipaddress.AddressValueError, ValueError):
        raise ConfigError(f"{ctx} is not a valid IPv4 address: {value!r}")
    return value


class Config:
    def __init__(self, data: dict[str, Any], path: str):
        self.path = path
        self.data = data
        self.secrets_dir = os.environ.get("PIXOO_SECRETS_DIR") or "/etc/pixoo-local"

    def secret_path(self, value: str) -> str:
        """Resolve a secret reference. Absolute paths are used as-is; bare
        filenames are resolved against PIXOO_SECRETS_DIR so the container can
        point services at re-materialized, correctly-owned copies."""
        if not value:
            return value
        if os.path.isabs(value):
            return value
        return os.path.join(self.secrets_dir, value)

    # -- convenience accessors -------------------------------------------
    def get(self, *keys, default=None):
        node: Any = self.data
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node

    @property
    def pixoo_ip(self) -> str:
        return self.get("network", "pixoo_ip")

    @property
    def pixoo_mac(self) -> str:
        return normalize_mac(self.get("network", "pixoo_mac"))

    @property
    def advertise_ip(self) -> str:
        return self.get("network", "advertise_ip")

    @property
    def http_base_url(self) -> str:
        url = self.get("http_api", "base_url")
        if url:
            return url
        return f"http://{self.pixoo_ip}/post"

    @property
    def device_id(self) -> int:
        return int(self.get("mqtt", "device_id", default=self.get("bootstrap", "device_id")))

    @property
    def topic_prefix(self) -> str:
        return self.get("mqtt", "topic_prefix") or f"divoom/2/{self.device_id}"


def validate(data: dict[str, Any]) -> None:
    for section in ("network", "bootstrap", "mqtt", "http_api", "display", "web"):
        if section not in data:
            raise ConfigError(f"missing required config section: {section}")

    net = data["network"]
    _check_ip(_require(net, "pixoo_ip", "network"), "network.pixoo_ip")
    _check_ip(_require(net, "advertise_ip", "network"), "network.advertise_ip")
    if net.get("gateway_ip"):
        _check_ip(net["gateway_ip"], "network.gateway_ip")
    try:
        normalize_mac(_require(net, "pixoo_mac", "network"))
    except ValueError as e:
        raise ConfigError(str(e))

    boot = data["bootstrap"]
    port = int(_require(boot, "listen_port", "bootstrap"))
    if not (1 <= port <= 65535):
        raise ConfigError(f"bootstrap.listen_port out of range: {port}")
    if not isinstance(boot.get("allowed_hosts"), list) or not boot["allowed_hosts"]:
        raise ConfigError("bootstrap.allowed_hosts must be a non-empty list")
    device_id = int(_require(boot, "device_id", "bootstrap"))
    if device_id <= 0:
        raise ConfigError(f"bootstrap.device_id must be positive: {device_id}")

    disp = data["display"]
    b = int(disp.get("default_brightness", 30))
    if not (0 <= b <= 100):
        raise ConfigError(f"display.default_brightness must be 0..100: {b}")
    if disp.get("transport", "auto") not in ("auto", "http", "mqtt"):
        raise ConfigError("display.transport must be one of auto|http|mqtt")
    if int(disp.get("width", 64)) != 64 or int(disp.get("height", 64)) != 64:
        raise ConfigError("display.width/height must be 64x64 for the Pixoo 64")

    mqtt = data["mqtt"]
    mp = int(mqtt.get("port", 1883))
    if not (1 <= mp <= 65535):
        raise ConfigError(f"mqtt.port out of range: {mp}")
    if not mqtt.get("device_username"):
        raise ConfigError("mqtt.device_username is required")

    web = data["web"]
    wp = int(web.get("listen_port", 8090))
    if not (1 <= wp <= 65535):
        raise ConfigError(f"web.listen_port out of range: {wp}")


def load(path: str | None = None) -> Config:
    cfg_path = path or os.environ.get("PIXOO_CONFIG") or DEFAULT_CONFIG_PATH
    p = Path(cfg_path)
    if not p.exists():
        raise ConfigError(f"config file not found: {cfg_path}")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"invalid YAML in {cfg_path}: {e}")
    if not isinstance(data, dict):
        raise ConfigError(f"config root must be a mapping in {cfg_path}")
    validate(data)
    return Config(data, str(p))
