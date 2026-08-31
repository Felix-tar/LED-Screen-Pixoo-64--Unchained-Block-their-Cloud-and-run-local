import time

from bootstrap.core import (
    BootstrapSettings,
    build_response,
    extract_mac,
    is_host_allowed,
    parse_request_json,
    token_for_mac,
)
from common.tokens import device_jwt

TOKEN = "abcd1234abcd1234abcd1234abcd1234"

S = BootstrapSettings(
    advertise_ip="10.10.20.160",
    device_id=300000064,
    timezone_code="CET-1CEST,M3.5.0,M10.5.0/3",
    summer_zone=1,
    last_clock_id=61,
    configured_mac="a1b2c3d4e5f6",
    device_token=TOKEN,
    allowed_hosts=["app.divoom-gz.com"],
    allow_direct_ip_host=True,
)


def test_parse_body_get_or_empty():
    assert parse_request_json(b'{"Command":"Device/InitV2"}') == {"Command": "Device/InitV2"}
    assert parse_request_json(b"") == {}
    assert parse_request_json(b"not json") == {}
    assert parse_request_json(None) == {}


def test_extract_mac_variants():
    assert extract_mac({"DeviceMacAddr": "1C:69:20:D5:B8:FC"}) == "a1b2c3d4e5f6"
    assert extract_mac({"DeviceMacAddr": "1c-69-20-d5-b8-fc"}) == "a1b2c3d4e5f6"
    assert extract_mac({"nope": 1}) is None


def test_known_mac_full_response():
    now = 1780000000
    req = {"Command": "Device/InitV2", "DeviceMacAddr": "a1b2c3d4e5f6", "PacketFlag": 40}
    r = build_response(req, remote_ip="10.10.20.161", settings=S, now_ts=now)
    assert r["ReturnCode"] == 0
    assert r["IP"] == "10.10.20.160"            # MQTT server IP = broker/advertise IP
    # DeviceToken is a JWT carrying the MAC, derived from the device-token secret
    assert r["DeviceToken"] == device_jwt("a1b2c3d4e5f6", TOKEN)
    assert r["DeviceToken"].count(".") == 2     # well-formed JWT (header.payload.sig)
    assert r["UTCTime"] == now                  # dynamic time echoed
    assert r["DeviceId"] == 300000064
    assert r["PacketFlag"] == 40                # echoed from request
    assert r["Command"] == "Device/InitV2"
    assert r["DevicePublicIP"] == "10.10.20.161"


def test_unknown_mac_gets_different_token():
    req = {"DeviceMacAddr": "aabbccddeeff", "PacketFlag": 7}
    r = build_response(req, remote_ip="10.10.20.99", settings=S, now_ts=1)
    assert r["DeviceToken"] != TOKEN            # never leak the real credentials
    assert r["PacketFlag"] == 7


def test_token_for_mac():
    assert token_for_mac("a1b2c3d4e5f6", S) == device_jwt("a1b2c3d4e5f6", TOKEN)
    assert token_for_mac("aabbccddeeff", S) != device_jwt("a1b2c3d4e5f6", TOKEN)
    assert token_for_mac("aabbccddeeff", S) == token_for_mac("aabbccddeeff", S)


def test_utctime_is_current_when_now_passed():
    r = build_response({}, remote_ip="x", settings=S, now_ts=int(time.time()))
    assert abs(r["UTCTime"] - int(time.time())) < 5


def test_packetflag_defaults_zero():
    r = build_response({"DeviceMacAddr": "a1b2c3d4e5f6"}, remote_ip="x", settings=S, now_ts=1)
    assert r["PacketFlag"] == 0


def test_host_allow_rules():
    assert is_host_allowed("app.divoom-gz.com", S) is True
    assert is_host_allowed("app.divoom-gz.com:80", S) is True
    assert is_host_allowed("10.10.20.160", S) is True          # allow_direct_ip_host
    assert is_host_allowed("evil.example.com", S) is False
