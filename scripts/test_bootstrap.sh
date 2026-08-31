#!/usr/bin/env bash
# Exercise the bootstrap InitV2 endpoint from INSIDE the container (the host
# can't reach the macvlan IP without the optional shim). Verifies /health and
# that a GET-with-body InitV2 returns our device token + a fresh UTCTime.
set -uo pipefail
CT=pixoo-local
PROJ=/opt/pixoo-local
MAC="$(grep -E '^\s*pixoo_mac:' "$PROJ/config/config.yaml" | head -1 | grep -oE '[0-9a-fA-F]{12}')"

echo "== /health =="
docker exec "$CT" curl -sS --max-time 3 http://127.0.0.1/health
echo; echo "== Device/InitV2 (GET with JSON body, MAC=$MAC) =="
docker exec "$CT" curl -sS --max-time 3 \
    -H 'Host: app.divoom-gz.com' -H 'Content-Type: application/json' \
    --request GET --data "{\"Command\":\"Device/InitV2\",\"DeviceMacAddr\":\"$MAC\",\"PacketFlag\":40}" \
    http://127.0.0.1/Device/InitV2
echo
echo "== reject: wrong Host must NOT return a token =="
docker exec "$CT" curl -sS --max-time 3 -o /dev/null -w '%{http_code}\n' \
    -H 'Host: evil.example' http://127.0.0.1/Device/InitV2 \
    --request GET --data "{\"DeviceMacAddr\":\"$MAC\"}"
