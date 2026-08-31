#!/usr/bin/env bash
# Discover YOUR Pixoo's real identity from the real Divoom server, so you can
# put the correct values in config.yaml. Run this ONCE, from a host that can
# reach the internet, with your Pixoo's MAC (no separators, lowercase).
#
#   scripts/discover_device.sh a1b2c3d4e5f6
#
# It prints the DeviceId / UserId the real server assigns to your device. These
# MUST match in config.yaml (bootstrap.device_id / bootstrap.user_id and
# mqtt.device_id / mqtt.topic_prefix), or the firmware re-runs InitV2 in a loop.
set -euo pipefail
MAC="${1:-}"
[ -n "$MAC" ] || { echo "usage: $0 <pixoo-mac, e.g. a1b2c3d4e5f6>"; exit 1; }
MAC="$(echo "$MAC" | tr 'A-F' 'a-f' | tr -cd '0-9a-f')"
[ "${#MAC}" -eq 12 ] || { echo "MAC must be 12 hex chars, got: $MAC"; exit 1; }

# resolve the real app.divoom-gz.com via a PUBLIC resolver (bypass any local override)
IP="$(dig @1.1.1.1 app.divoom-gz.com A +short | grep -E '^[0-9.]+$' | head -1)"
IP="${IP:-47.88.33.110}"
echo "querying real Divoom server $IP for device $MAC ..."
RESP="$(curl -sS --max-time 8 -H 'Host: app.divoom-gz.com' -H 'Content-Type: application/json' \
  --request GET \
  --data "{\"Command\":\"Device/InitV2\",\"DeviceMacAddr\":\"$MAC\",\"ServerType\":2,\"PacketFlag\":40}" \
  "http://$IP/Device/InitV2")"
echo "raw response:"; echo "$RESP"; echo
DID="$(echo "$RESP" | grep -oE '"DeviceId":[0-9]+' | grep -oE '[0-9]+' | head -1)"
UID_="$(echo "$RESP" | grep -oE '"UserId":[0-9]+' | grep -oE '[0-9]+' | head -1)"
echo "==================================================================="
echo " Put these into config/config.yaml:"
echo "   bootstrap.device_id : ${DID:-<none returned>}"
echo "   bootstrap.user_id   : ${UID_:-0}"
echo "   mqtt.device_id      : ${DID:-<none returned>}"
echo "   mqtt.topic_prefix   : divoom/2/${DID:-<none returned>}"
echo "   mqtt.device_username: $MAC"
echo "   network.pixoo_mac   : $MAC"
echo "==================================================================="
