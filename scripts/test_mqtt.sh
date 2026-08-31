#!/usr/bin/env bash
# Exercise the dedicated broker + heartbeat from INSIDE the container.
# 1) server user can sub/pub on the device topic subtree
# 2) a Device/Hearbeat published on .../set is answered on .../get
# 3) the device user is DENIED a topic outside its subtree (ACL works)
set -uo pipefail
CT=pixoo-local
PROJ=/opt/pixoo-local

PREFIX="$(grep -E '^\s*topic_prefix:' "$PROJ/config/config.yaml" | head -1 | grep -oE 'divoom/2/[0-9]+')"
DEV_ID="$(echo "$PREFIX" | grep -oE '[0-9]+$')"
SRV_USER="$(grep -E '^\s*server_username:' "$PROJ/config/config.yaml" | head -1 | sed -E 's/.*"(.*)".*/\1/')"

echo "prefix=$PREFIX device_id=$DEV_ID server_user=$SRV_USER"

# server password from the container's materialized secret
SRV_PW="$(docker exec "$CT" cat /run/pixoo/secrets/server-mqtt-password)"

echo "== heartbeat round-trip =="
docker exec "$CT" sh -c "
  mosquitto_sub -h 127.0.0.1 -u '$SRV_USER' -P '$SRV_PW' -t '$PREFIX/get' -C 1 -W 6 > /tmp/hb.out &
  SUBPID=\$!
  sleep 1
  mosquitto_pub -h 127.0.0.1 -u '$SRV_USER' -P '$SRV_PW' -t '$PREFIX/set' \
    -m '{\"Command\":\"Device/Hearbeat\",\"DeviceId\":$DEV_ID,\"PacketFlag\":123456}'
  wait \$SUBPID
  echo 'heartbeat reply:'; cat /tmp/hb.out
"

echo "== ACL: device user denied outside subtree (expect failure) =="
DEV_USER="$(grep -E '^\s*device_username:' "$PROJ/config/config.yaml" | head -1 | sed -E 's/.*"(.*)".*/\1/')"
DEV_PW="$(docker exec "$CT" cat /run/pixoo/secrets/device-token 2>/dev/null || echo '')"
if [ -n "$DEV_PW" ]; then
  docker exec "$CT" sh -c "
    mosquitto_pub -h 127.0.0.1 -u '$DEV_USER' -P '$DEV_PW' -t 'divoom/other/topic' -m 'x' 2>&1 \
      && echo 'UNEXPECTED: publish allowed' || echo 'OK: publish outside subtree denied'
  "
else
  echo "(device token not readable in this context; skipped ACL-deny check)"
fi
