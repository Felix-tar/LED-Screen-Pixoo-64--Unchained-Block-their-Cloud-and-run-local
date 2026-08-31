#!/usr/bin/env bash
# Self-contained smoke test of the whole container stack on a THROWAWAY bridge
# network (no macvlan, no eth0 changes, no effect on the real Pixoo).
# Validates: bootstrap InitV2 (+host reject), mosquitto auth + ACL, heartbeat
# round-trip, web API. Tears everything down at the end.
set -uo pipefail
IMAGE="${PIXOO_IMAGE:-pixoo-local:latest}"
NAME=pixoo-smoke
SMOKE=/tmp/pixoo-smoke
CFG="/opt/pixoo-local/tests/smoke_config.yaml"
pass=0; fail=0
ok()   { echo "  [ OK ] $1"; pass=$((pass+1)); }
bad()  { echo "  [FAIL] $1"; fail=$((fail+1)); }

cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; rm -rf "$SMOKE" "$CFG"; }
trap cleanup EXIT
cleanup

echo "== preparing temp secrets + smoke config =="
mkdir -p "$SMOKE/secrets"
openssl rand -hex 16 > "$SMOKE/secrets/device-token"
openssl rand -hex 24 > "$SMOKE/secrets/server-mqtt-password"
openssl rand -hex 12 > "$SMOKE/secrets/web-auth-password"
chmod 600 "$SMOKE"/secrets/*
DEV_TOKEN="$(cat "$SMOKE/secrets/device-token")"
WEB_PW="$(cat "$SMOKE/secrets/web-auth-password")"
# smoke config: unroutable Pixoo IP + base_url so the supervisor never reaches
# the real device (no display/brightness changes during the smoke test).
sed -E \
  -e 's|(^\s*pixoo_ip:\s*).*|\1"10.255.255.1"|' \
  -e 's|(^\s*base_url:\s*).*|\1"http://10.255.255.1/post"|' \
  /opt/pixoo-local/config/config.yaml > "$CFG"

echo "== starting container on bridge =="
docker run -d --name "$NAME" \
  --network bridge -p 18080:80 -p 18090:8090 -p 18883:1883 \
  -v /opt/pixoo-local:/opt/pixoo-local \
  -v "$SMOKE/secrets:/etc/pixoo-local:ro" \
  --tmpfs /run:mode=0755 \
  -e PIXOO_CONFIG="$CFG" \
  -e PIXOO_SECRETS_DIR=/etc/pixoo-local \
  -e PIXOO_STATUS_DIR=/run/pixoo/status \
  --cap-drop ALL --cap-add NET_BIND_SERVICE --cap-add SETUID --cap-add SETGID \
  --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER --cap-add KILL \
  --security-opt no-new-privileges:true \
  "$IMAGE" >/dev/null

echo "== waiting for services (bootstrap + web) =="
for i in $(seq 1 30); do
  b=0; w=0
  curl -fsS --max-time 2 http://127.0.0.1:18080/health >/dev/null 2>&1 && b=1
  curl -fsS --max-time 2 http://127.0.0.1:18090/health >/dev/null 2>&1 && w=1
  [ "$b" = 1 ] && [ "$w" = 1 ] && break
  sleep 1
done

echo "== bootstrap =="
H="$(curl -fsS --max-time 3 http://127.0.0.1:18080/health 2>/dev/null || true)"
echo "$H" | grep -q '"status":"ok"' && ok "bootstrap /health" || bad "bootstrap /health ($H)"

INIT="$(curl -fsS --max-time 3 -H 'Host: app.divoom-gz.com' -H 'Content-Type: application/json' \
  --request GET --data '{"Command":"Device/InitV2","DeviceMacAddr":"a1b2c3d4e5f6","PacketFlag":40}' \
  http://127.0.0.1:18080/Device/InitV2 2>/dev/null || true)"
echo "  InitV2 -> $INIT"
echo "$INIT" | grep -qE '"DeviceToken":"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"' && ok "InitV2 returns JWT DeviceToken for known MAC" || bad "InitV2 token (want JWT)"
echo "$INIT" | grep -q '"PacketFlag":40' && ok "InitV2 echoes PacketFlag" || bad "InitV2 PacketFlag"
echo "$INIT" | grep -qE '"UTCTime":[0-9]{10}' && ok "InitV2 has fresh UTCTime" || bad "InitV2 UTCTime"

CODE="$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: evil.example' --request GET \
  --data '{"DeviceMacAddr":"a1b2c3d4e5f6"}' http://127.0.0.1:18080/Device/InitV2)"
[ "$CODE" = "404" ] && ok "bootstrap rejects wrong Host (404, no secrets)" || bad "wrong-host reject ($CODE)"

echo "== mosquitto auth + heartbeat =="
PREFIX="$(grep -E '^\s*topic_prefix:' "$CFG" | grep -oE 'divoom/2/[0-9]+' | head -1)"
DEVID="$(echo "$PREFIX" | grep -oE '[0-9]+$')"
SRV_PW="$(docker exec "$NAME" cat /run/pixoo/secrets/server-mqtt-password)"
HB="$(docker exec "$NAME" sh -c "
  mosquitto_sub -h 127.0.0.1 -u pixoo-server -P '$SRV_PW' -t '$PREFIX/get' -C 1 -W 6 > /tmp/hb.out 2>/dev/null &
  sleep 1
  mosquitto_pub -h 127.0.0.1 -u pixoo-server -P '$SRV_PW' -t '$PREFIX/set' \
    -m '{\"Command\":\"Device/Hearbeat\",\"DeviceId\":$DEVID,\"PacketFlag\":123456}'
  wait; cat /tmp/hb.out")"
echo "  heartbeat reply -> $HB"
echo "$HB" | grep -q '"PacketFlag": *123456' && ok "heartbeat answered with echoed PacketFlag" || bad "heartbeat reply"
echo "$HB" | grep -q 'Device/Hearbeat' && ok "heartbeat keeps firmware spelling" || bad "heartbeat spelling"

DENY="$(docker exec "$NAME" sh -c "mosquitto_pub -h 127.0.0.1 -u pixoo-server -P 'wrongpw' -t x -m y 2>&1 || echo REFUSED")"
echo "$DENY" | grep -qi 'refused\|not authorised\|Connection refused\|Connection error' && ok "bad password rejected (anonymous disabled)" || bad "auth not enforced ($DENY)"

echo "== web =="
WH="$(curl -fsS --max-time 3 http://127.0.0.1:18090/health 2>/dev/null || true)"
echo "$WH" | grep -q '"status":"ok"' && ok "web /health" || bad "web /health ($WH)"
UNAUTH="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18090/api/status)"
[ "$UNAUTH" = "401" ] && ok "web /api/status requires auth (401)" || bad "web auth missing ($UNAUTH)"
ST="$(curl -fsS -u "pixoo:$WEB_PW" --max-time 3 http://127.0.0.1:18090/api/status 2>/dev/null || true)"
echo "  /api/status -> $ST"
echo "$ST" | grep -q '"state"' && ok "web /api/status with auth returns status" || bad "web status"
PNG="$(curl -s -o /dev/null -w '%{content_type}' -u "pixoo:$WEB_PW" 'http://127.0.0.1:18090/api/preview.png?scale=4')"
echo "$PNG" | grep -q 'image/png' && ok "web preview.png renders" || bad "web preview ($PNG)"

echo
echo "==== SMOKE SUMMARY: PASS=$pass FAIL=$fail ===="
[ "$fail" -eq 0 ] || { echo "-- container logs --"; docker logs --tail 40 "$NAME"; }
exit "$([ "$fail" -eq 0 ] && echo 0 || echo 1)"
