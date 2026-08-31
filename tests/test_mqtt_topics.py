from mqtt.protocol import (
    HEARTBEAT_COMMAND,
    build_ack_reply,
    build_heartbeat_reply,
    is_heartbeat,
    topics,
)


def test_topics():
    t = topics("divoom/2/300000064")
    assert t["set"] == "divoom/2/300000064/set"
    assert t["state"] == "divoom/2/300000064/state"
    assert t["get"] == "divoom/2/300000064/get"


def test_heartbeat_spelling_is_preserved():
    # the firmware misspelling must NOT be auto-corrected
    assert HEARTBEAT_COMMAND == "Device/Hearbeat"
    assert is_heartbeat({"Command": "Device/Hearbeat"}) is True
    assert is_heartbeat({"Command": "Device/Heartbeat"}) is False  # corrected spelling != match


def test_reply_echoes_packetflag_and_id():
    msg = {"Command": HEARTBEAT_COMMAND, "DeviceId": 300000064, "PacketFlag": 123456}
    r = build_heartbeat_reply(msg, device_id=300000064)
    assert r["Command"] == "Device/Hearbeat"
    assert r["DeviceId"] == 300000064
    assert r["PacketFlag"] == 123456


def test_reply_device_id_fallback():
    r = build_heartbeat_reply({"Command": HEARTBEAT_COMMAND, "PacketFlag": 1}, device_id=300000064)
    assert r["DeviceId"] == 300000064


def test_reply_bad_packetflag_defaults():
    r = build_heartbeat_reply({"Command": HEARTBEAT_COMMAND, "PacketFlag": "oops"}, device_id=1)
    assert r["PacketFlag"] == 0


def test_ack_reply_device_connect_has_localtoken():
    # matches the real server reply; LocalToken is what provisions the device
    msg = {"Command": "Device/Connect", "DeviceId": 300000000, "PacketFlag": 353}
    r = build_ack_reply(msg, 300000000, local_token=100000, software=92079)
    assert r["Command"] == "Device/Connect"
    assert r["DeviceId"] == 300000000
    assert r["PacketFlag"] == 353
    assert r["ReturnCode"] == 0
    assert r["LocalToken"] == 100000
    assert r["Software"] == 92079
    assert r["ExpertList"] == []


def test_ack_reply_merges_captured_templates():
    # per-command fields (captured from the real server) are merged in
    templates = {
        "Sys/GetConf": {"Brightness": 35, "CurClockId": 1033},
        "Device/GetAppIP": {"AppIpList": []},
        "Device/GetHotList": {"FileList": [], "FileNum": 0},
    }
    r = build_ack_reply({"Command": "Sys/GetConf", "PacketFlag": 202}, 300000000, templates=templates)
    assert r["Brightness"] == 35 and r["CurClockId"] == 1033
    assert r["Command"] == "Sys/GetConf" and r["ReturnCode"] == 0
    assert build_ack_reply({"Command": "Device/GetAppIP"}, 1, templates=templates)["AppIpList"] == []
    assert build_ack_reply({"Command": "Device/GetHotList"}, 1, templates=templates)["FileNum"] == 0


def test_ack_reply_skips_status_and_heartbeat():
    # last-will and heartbeat must NOT be answered by the generic ack path
    assert build_ack_reply({"Command": "Device/lwt"}, 1) == {}
    assert build_ack_reply({"Command": HEARTBEAT_COMMAND}, 1) == {}
    assert build_ack_reply({}, 1) == {}


def test_ack_reply_device_connect_localtoken_always_present():
    # even with no template, Device/Connect must carry LocalToken/Software
    r = build_ack_reply({"Command": "Device/Connect", "PacketFlag": 1}, 300000000,
                        local_token=100000, software=92079)
    assert r["LocalToken"] == 100000 and r["Software"] == 92079 and r["ExpertList"] == []
