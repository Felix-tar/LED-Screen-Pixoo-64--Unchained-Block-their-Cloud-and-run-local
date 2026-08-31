import pytest

from common.config import Config, ConfigError, load, validate


def base():
    return {
        "network": {
            "pixoo_ip": "10.10.20.161",
            "pixoo_mac": "a1b2c3d4e5f6",
            "advertise_ip": "10.10.20.160",
            "gateway_ip": "10.10.20.254",
        },
        "bootstrap": {
            "listen_port": 80,
            "device_id": 300000064,
            "allowed_hosts": ["app.divoom-gz.com"],
            "timezone_code": "CET-1CEST,M3.5.0,M10.5.0/3",
            "summer_zone": 1,
            "last_clock_id": 61,
        },
        "mqtt": {"port": 1883, "device_username": "a1b2c3d4e5f6", "device_id": 300000064},
        "http_api": {},
        "display": {"default_brightness": 30, "transport": "auto", "width": 64, "height": 64},
        "web": {"listen_port": 8090},
    }


def test_valid_ok():
    validate(base())


def test_bad_ip():
    d = base()
    d["network"]["pixoo_ip"] = "999.1.1.1"
    with pytest.raises(ConfigError):
        validate(d)


def test_bad_mac():
    d = base()
    d["network"]["pixoo_mac"] = "zz:zz"
    with pytest.raises(ConfigError):
        validate(d)


def test_brightness_range():
    d = base()
    d["display"]["default_brightness"] = 250
    with pytest.raises(ConfigError):
        validate(d)


def test_bad_transport():
    d = base()
    d["display"]["transport"] = "carrierpigeon"
    with pytest.raises(ConfigError):
        validate(d)


def test_missing_section():
    d = base()
    del d["mqtt"]
    with pytest.raises(ConfigError):
        validate(d)


def test_accessors_and_secret_path(monkeypatch):
    cfg = Config(base(), "mem")
    assert cfg.pixoo_ip == "10.10.20.161"
    assert cfg.pixoo_mac == "a1b2c3d4e5f6"
    assert cfg.advertise_ip == "10.10.20.160"
    assert cfg.http_base_url == "http://10.10.20.161/post"
    assert cfg.device_id == 300000064
    assert cfg.topic_prefix == "divoom/2/300000064"
    # bare filename resolves against secrets dir
    monkeypatch.setenv("PIXOO_SECRETS_DIR", "/etc/pixoo-local")
    cfg2 = Config(base(), "mem")
    assert cfg2.secret_path("device-token") == "/etc/pixoo-local/device-token"
    assert cfg2.secret_path("/abs/path") == "/abs/path"


def test_load_from_file(tmp_path):
    import yaml
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(base()))
    cfg = load(str(p))
    assert cfg.pixoo_mac == "a1b2c3d4e5f6"
