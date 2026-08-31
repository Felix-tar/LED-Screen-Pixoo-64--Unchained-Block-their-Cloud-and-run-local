#!/usr/bin/env bash
# Collect a diagnostic snapshot of the running pixoo-local stack + Pixoo.
# Host-side checks hit the Pixoo directly (same LAN); container-internal checks
# use `docker exec` (the host can't reach the macvlan IP without the shim).
set -uo pipefail
PROJ=/opt/pixoo-local
CT=pixoo-local
TS="$(date +%Y%m%d-%H%M%S)"
OUT="$PROJ/reports/diagnose-$TS.txt"
mkdir -p "$PROJ/reports"

PIXOO_IP="$(grep -E '^\s*pixoo_ip:' "$PROJ/config/config.yaml" | head -1 | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' | head -1)"
ADV_IP="$(grep -E 'PIXOO_ADVERTISE_IP=' "$PROJ/.env" 2>/dev/null | cut -d= -f2)"
ADV_IP="${ADV_IP:-10.10.20.160}"

{
echo "==== pixoo-local diagnose $TS ===="
echo "-- container --"
docker ps --filter "name=$CT" --format 'table {{.Names}}\t{{.Status}}\t{{.Networks}}' 2>&1
echo; echo "-- container health/state --"
docker inspect -f '{{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}}' "$CT" 2>&1

echo; echo "-- Pixoo reachability (host -> $PIXOO_IP) --"
ping -c2 -W2 "$PIXOO_IP" 2>&1 | tail -3
echo "port 80:"; (exec 3<>/dev/tcp/"$PIXOO_IP"/80) 2>/dev/null && echo "  OPEN" || echo "  closed"
echo "GetAllConf:"; curl -sS --max-time 5 -X POST "http://$PIXOO_IP/post" \
    -H 'Content-Type: application/json' -d '{"Command":"Channel/GetAllConf"}' 2>&1 | head -1

echo; echo "-- bootstrap /health (container-internal) --"
docker exec "$CT" curl -sS --max-time 3 http://127.0.0.1/health 2>&1 | head -1
echo; echo "-- web /api/status (container-internal, no-auth health) --"
docker exec "$CT" curl -sS --max-time 3 http://127.0.0.1:8090/health 2>&1 | head -1
echo; echo "-- shared status files --"
docker exec "$CT" sh -c 'cat /run/pixoo/status/*.json 2>/dev/null' 2>&1 | head -20

echo; echo "-- DNS override check (does .231 point app.divoom-gz.com at us?) --"
dig @10.10.20.231 app.divoom-gz.com +short 2>&1
echo "  (expected: $ADV_IP once you set the record on 10.10.20.231)"

echo; echo "-- last 40 container log lines --"
docker logs --tail 40 "$CT" 2>&1
echo "==== end ===="
} | tee "$OUT"

echo
echo "saved: $OUT"
