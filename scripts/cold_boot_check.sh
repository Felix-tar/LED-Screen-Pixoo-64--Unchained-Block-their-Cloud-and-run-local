#!/usr/bin/env bash
# =====================================================================
# Offline cold-boot acceptance check (task section 22).
# Run this AFTER the DNS record for app.divoom-gz.com points at the dedicated
# IP and the stack is running. Power-cycle the Pixoo (smart plug off/on), then
# run this. It records the whole bring-up and writes a report.
#
# Success = the Pixoo boots fully via the LOCAL bootstrap+MQTT, with the Omada
# LAN->WAN deny for the Pixoo still ACTIVE (no temporary internet needed).
# =====================================================================
set -uo pipefail
PROJ=/opt/pixoo-local
CT=pixoo-local
TS="$(date +%Y%m%d-%H%M%S)"
OUT="$PROJ/reports/cold-boot-$TS.txt"
mkdir -p "$PROJ/reports"

PIXOO_IP="$(grep -E '^\s*pixoo_ip:' "$PROJ/config/config.yaml" | head -1 | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' | head -1)"
TIMEOUT="${1:-180}"   # seconds to wait for full readiness

pass=0; fail=0
mark() { if [ "$1" = ok ]; then echo "  [ OK ] $2"; pass=$((pass+1)); else echo "  [FAIL] $2"; fail=$((fail+1)); fi; }

exec > >(tee "$OUT") 2>&1
echo "==== cold-boot check $TS (timeout ${TIMEOUT}s) ===="
echo "Pixoo=$PIXOO_IP  container=$CT"
START=$(date +%s)

# capture the current bootstrap request count to detect a NEW InitV2
boot_count_before="$(docker exec "$CT" sh -c 'cat /run/pixoo/status/bootstrap.json 2>/dev/null' | grep -oE '"count":[0-9]+' | grep -oE '[0-9]+' || echo 0)"
echo "bootstrap requests before: ${boot_count_before:-0}"
echo ">> Now power-cycle the Pixoo (smart plug OFF, wait 10s, ON). Waiting..."

ping_ok=fail; port_ok=fail; boot_ok=fail; hb_ok=fail; api_ok=fail
while : ; do
    now=$(date +%s); elapsed=$((now-START))
    [ "$elapsed" -ge "$TIMEOUT" ] && break
    # fresh signals re-evaluated each loop (boot_ok/hb_ok stay sticky once seen)
    ping_ok=fail; port_ok=fail; api_ok=fail

    ping -c1 -W1 "$PIXOO_IP" >/dev/null 2>&1 && ping_ok=ok

    boot_now="$(docker exec "$CT" sh -c 'cat /run/pixoo/status/bootstrap.json 2>/dev/null' | grep -oE '"count":[0-9]+' | grep -oE '[0-9]+' || echo 0)"
    [ "${boot_now:-0}" -gt "${boot_count_before:-0}" ] && boot_ok=ok

    # this firmware uses the Device/Connect handshake, not Device/Hearbeat, so
    # "MQTT connected" = the server acked at least one device command
    hb_now="$(docker exec "$CT" sh -c 'cat /run/pixoo/status/heartbeat.json 2>/dev/null' | grep -oE '"acks_sent":[0-9]+' | grep -oE '[0-9]+' || echo 0)"
    [ "${hb_now:-0}" -gt 0 ] && hb_ok=ok

    (exec 3<>/dev/tcp/"$PIXOO_IP"/80) 2>/dev/null && port_ok=ok
    if [ "$port_ok" = ok ]; then
        if curl -sS --max-time 5 -X POST "http://$PIXOO_IP/post" \
            -H 'Content-Type: application/json' \
            -d '{"Command":"Channel/GetAllConf"}' 2>/dev/null | grep -q '"error_code": *0'; then
            api_ok=ok
        fi
    fi

    # success = a FRESH InitV2 after the reboot AND the local API answering now
    if [ "$boot_ok" = ok ] && [ "$api_ok" = ok ]; then
        break
    fi
    sleep 2
done
elapsed=$(($(date +%s)-START))
echo "-- results after ${elapsed}s --"
mark "$ping_ok"  "Pixoo answers ping ($PIXOO_IP)"
mark "$boot_ok"  "Local bootstrap received a NEW /Device/InitV2 (count > $boot_count_before)"
mark "$hb_ok"    "Pixoo MQTT connect handshake acknowledged (acks_sent > 0)"
mark "$port_ok"  "Pixoo port 80 open"
mark "$api_ok"   "Channel/GetAllConf returns error_code 0"

echo "-- sending 64x64 test pattern --"
if python3 "$PROJ/scripts/send_test_pattern.py" >/dev/null 2>&1; then
    mark ok "Test pattern displayed"
else
    mark fail "Test pattern send"
fi

echo "-- last bootstrap/heartbeat status --"
docker exec "$CT" sh -c 'cat /run/pixoo/status/bootstrap.json /run/pixoo/status/heartbeat.json 2>/dev/null' | head

echo "==== summary: PASS=$pass FAIL=$fail ===="
[ "$fail" -eq 0 ] && echo "COLD BOOT: SUCCESS" || echo "COLD BOOT: INCOMPLETE — see report"
echo "saved: $OUT"
exit "$([ "$fail" -eq 0 ] && echo 0 || echo 1)"
