"""Pure MQTT protocol helpers (unit-testable, no network).

NOTE the deliberate upstream misspelling "Device/Hearbeat" — the Pixoo firmware
sends it exactly like that. We must NOT silently correct it to "Heartbeat", or
the device will not accept the reply.
"""
from __future__ import annotations

from typing import Any

HEARTBEAT_COMMAND = "Device/Hearbeat"  # sic — matches firmware spelling


def topics(prefix: str) -> dict[str, str]:
    prefix = prefix.rstrip("/")
    return {
        "set": f"{prefix}/set",       # Pixoo -> server (requests)
        "state": f"{prefix}/state",   # Pixoo -> server (state)
        "get": f"{prefix}/get",       # server -> Pixoo (replies/commands)
    }


def is_heartbeat(msg: dict[str, Any]) -> bool:
    return isinstance(msg, dict) and msg.get("Command") == HEARTBEAT_COMMAND


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# Commands the device fires during its connect/sync handshake on .../set. It
# keeps re-sending them (and shows the "Connecting" overlay) until the server
# acknowledges each on .../get. Observed on a Pixoo 64 (old HW):
#   Device/Connect, Device/GetAppIP, Sys/GetConf, Channel/OnOffScreen,
#   Device/GetCustomGalleryTimeList, Device/GetSubscribeGalleryTimeList,
#   Channel/GetNightView, Channel/GetAllCustomTime, Channel/GetEqTime,
#   Channel/GetSubscribeTime, Device/GetUserDefineList, Device/GetHotList
def build_ack_reply(
    msg: dict[str, Any],
    device_id: int,
    *,
    advertise_ip: str | None = None,
    now_ts: int | None = None,
    local_token: int = 0,
    software: int = 0,
    templates: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """Reply for a device request on divoom/2/<id>/get.

    Fills Command/DeviceId/PacketFlag/ReturnCode, then merges the command's
    captured real-server response fields from ``templates`` (config/
    mqtt_responses.json). Device/Connect additionally gets the numeric
    LocalToken the firmware needs to consider itself provisioned. Returns {}
    for messages that must NOT be answered (status/last-will/heartbeat).
    """
    cmd = msg.get("Command")
    if not cmd or cmd in _NO_REPLY_COMMANDS:
        return {}
    reply: dict[str, Any] = {
        "Command": cmd,
        "DeviceId": _as_int(msg.get("DeviceId", device_id), device_id) or device_id,
        "PacketFlag": _as_int(msg.get("PacketFlag", 0)),
        "ReturnCode": 0,
        "ReturnMessage": "",
    }
    if templates and cmd in templates:
        for k, v in templates[cmd].items():
            if not k.startswith("_"):
                reply[k] = v
    else:
        # generic fallback: an unknown Get<Name>List command expects an empty
        # "<Name>List" field present (data is cached on the device). Covers e.g.
        # Device/GetClockList, GetEqDataList, GetHistoryClockList, Channel/GetNotifyList.
        short = cmd.rsplit("/", 1)[-1]
        if short.startswith("Get") and short.endswith("List"):
            reply.setdefault(short[3:], [])
    if cmd == "Device/Connect":
        # matches the real server reply exactly (Software/LocalToken/ExpertList)
        reply["Software"] = int(software)
        reply["LocalToken"] = int(local_token)
        reply["ExpertList"] = []
    return reply


# device -> server status/last-will; never answer these
_NO_REPLY_COMMANDS = {"Device/lwt", HEARTBEAT_COMMAND}
_EMPTY_LIST_COMMANDS = {
    "Channel/GetAllCustomTime",
    "Channel/GetEqTime",
    "Channel/GetSubscribeTime",
    "Channel/GetNightView",
}


def build_heartbeat_reply(msg: dict[str, Any], device_id: int) -> dict[str, Any]:
    """Build the reply for a Device/Hearbeat. Echoes PacketFlag exactly.

    DeviceId from the request is validated: if present and it matches the
    configured id we keep it, otherwise we answer with the configured id.
    """
    packet_flag = msg.get("PacketFlag", 0)
    try:
        packet_flag = int(packet_flag)
    except (TypeError, ValueError):
        packet_flag = 0

    req_id = msg.get("DeviceId")
    try:
        out_id = int(req_id) if req_id is not None else int(device_id)
    except (TypeError, ValueError):
        out_id = int(device_id)

    return {
        "Command": HEARTBEAT_COMMAND,
        "DeviceId": out_id,
        "PacketFlag": packet_flag,
    }
