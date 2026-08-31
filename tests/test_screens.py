import pytest

from common.config import Config
from dashboard import widgets
from dashboard.screens import PlaylistRenderer, default_screens, validate_playlist


def _cfg(tmp_path):
    data = {
        "network": {"pixoo_ip": "10.10.20.161", "pixoo_mac": "a1b2c3d4e5f6",
                    "advertise_ip": "10.10.20.160"},
        "bootstrap": {"listen_port": 80, "device_id": 300000000,
                      "allowed_hosts": ["app.divoom-gz.com"], "timezone_code": "X"},
        "mqtt": {"port": 1883, "device_username": "a1b2c3d4e5f6", "device_id": 300000000},
        "http_api": {}, "display": {"width": 64, "height": 64, "transport": "auto",
                                    "default_brightness": 30},
        "web": {"listen_port": 8090},
        "dashboard": {"market": {"symbols": [{"label": "DAX", "value": "+0.4%"}]}},
    }
    return Config(data, str(tmp_path / "config.yaml"))


def test_widget_registry_has_core_types():
    for t in ("text", "clock", "sysbars", "list", "kv", "bar", "metric", "date"):
        assert t in widgets.REGISTRY


def test_hexcolor():
    assert widgets.hexcolor("#ff0000") == (255, 0, 0)
    assert widgets.hexcolor("bad", (1, 2, 3)) == (1, 2, 3)
    assert widgets.hexcolor([10, 20, 30]) == (10, 20, 30)


def test_default_screens_render(tmp_path):
    r = PlaylistRenderer(_cfg(tmp_path))
    assert [s["name"] for s in r.screens()][:2] == ["Server", "Market"]  # default order
    for i in range(len(r.screens())):
        img = r.render_index(i)
        assert img.size == (64, 64) and img.mode == "RGB"


def test_render_all_widget_types(tmp_path):
    r = PlaylistRenderer(_cfg(tmp_path))
    screen = {"name": "t", "background": "#000000", "widgets": [
        {"type": "text", "x": 0, "y": 0, "w": 64, "h": 6, "text": "HI"},
        {"type": "clock", "x": 0, "y": 8, "w": 64, "h": 16, "tz": "UTC"},
        {"type": "date", "x": 0, "y": 26, "w": 64, "h": 6},
        {"type": "sysbars", "x": 0, "y": 34, "w": 64, "h": 21, "metrics": ["cpu", "ram"]},
        {"type": "list", "x": 0, "y": 34, "w": 64, "h": 20, "source": "crypto"},
        {"type": "bar", "x": 0, "y": 56, "w": 64, "h": 6, "source": "value", "value": 42},
    ]}
    img = r.render_screen(screen)
    assert img.size == (64, 64)


def test_validate_playlist_rejects_unknown_widget():
    with pytest.raises(ValueError):
        validate_playlist({"screens": [{"widgets": [{"type": "nope"}]}]})
    validate_playlist(default_screens())  # the default must be valid


def test_save_and_reload(tmp_path):
    r = PlaylistRenderer(_cfg(tmp_path))
    data = {"rotation": False, "screens": [{"name": "Solo", "duration": 5,
            "background": "#000000", "widgets": [{"type": "clock", "x": 0, "y": 0, "w": 64, "h": 32}]}]}
    r.save(data)
    r2 = PlaylistRenderer(_cfg(tmp_path))
    assert [s["name"] for s in r2.screens()] == ["Solo"]
