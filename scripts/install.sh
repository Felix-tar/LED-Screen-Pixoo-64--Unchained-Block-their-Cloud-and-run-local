#!/usr/bin/env bash
# =====================================================================
# pixoo-local installer (Docker macvlan deployment)
# - Never modifies Pi-hole, the shared Mosquitto, or eth0 config.
# - Idempotent: existing secrets are preserved (keeps the Pixoo pairing).
# - Backs up anything it would overwrite under backups/<timestamp>/.
# - Builds the image, runs the test suite, installs+enables the systemd unit.
# - Does NOT start the live stack unless --start is given (go live only after
#   you have reserved the dedicated IP on DHCP and set the DNS record).
# =====================================================================
set -euo pipefail

PROJ=/opt/pixoo-local
SECRETS_DIR=/etc/pixoo-local
TS="$(date +%Y%m%d-%H%M%S)"
BACKUP="$PROJ/backups/$TS"
DO_START=0
[ "${1:-}" = "--start" ] && DO_START=1

log() { printf '\033[36m[install]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[install:WARN]\033[0m %s\n' "$*"; }
die() { printf '\033[31m[install:FATAL]\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run as root (sudo $0)"

log "detecting system"
. /etc/os-release 2>/dev/null || true
ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m)"
log "OS=${PRETTY_NAME:-unknown} ARCH=$ARCH"
command -v docker >/dev/null || die "docker not found"
docker compose version >/dev/null 2>&1 || die "docker compose v2 not found"
command -v openssl >/dev/null || die "openssl not found"

# ---- detect LAN interface / subnet / gateway -----------------------
PARENT_IF="${PIXOO_PARENT_IF:-$(ip -4 route show default | awk '/default/{print $5; exit}')}"
GATEWAY="${PIXOO_GATEWAY:-$(ip -4 route show default | awk '/default/{print $3; exit}')}"
LAN_IP="$(ip -4 -o addr show dev "$PARENT_IF" scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)"
SUBNET="${PIXOO_SUBNET:-$(ip -4 route show dev "$PARENT_IF" scope link | awk '$1 ~ /\// {print $1; exit}')}"
ADVERTISE_IP="${PIXOO_ADVERTISE_IP:-10.10.20.160}"
[ -n "$PARENT_IF" ] || die "could not detect LAN interface"
log "LAN if=$PARENT_IF host=$LAN_IP subnet=$SUBNET gw=$GATEWAY advertise_ip=$ADVERTISE_IP"

# ---- check the dedicated IP is free --------------------------------
if ping -c1 -W1 "$ADVERTISE_IP" >/dev/null 2>&1; then
    warn "$ADVERTISE_IP currently RESPONDS to ping — it may be in use! Choose another PIXOO_ADVERTISE_IP."
else
    log "$ADVERTISE_IP is free (no ping)"
fi

# ---- backups -------------------------------------------------------
mkdir -p "$BACKUP"
if [ -d "$SECRETS_DIR" ]; then
    cp -a "$SECRETS_DIR" "$BACKUP/etc-pixoo-local" 2>/dev/null || true
    log "backed up existing $SECRETS_DIR"
fi
[ -f "$PROJ/.env" ] && cp -a "$PROJ/.env" "$BACKUP/compose.env.bak"

# ---- secrets (idempotent) ------------------------------------------
log "provisioning secrets in $SECRETS_DIR"
install -d -m 0700 -o root -g root "$SECRETS_DIR"
gen_secret() {
    local f="$SECRETS_DIR/$1" bytes="$2"
    if [ -s "$f" ]; then
        log "keeping existing secret $1"
    else
        openssl rand -hex "$bytes" > "$f"
        log "generated secret $1"
    fi
    chmod 600 "$f"; chown root:root "$f"
}
gen_secret device-token 16          # 32 hex chars = MQTT DeviceToken
gen_secret server-mqtt-password 24
gen_secret web-auth-password 12

# ---- unprivileged host user (native mode + ownership) --------------
if ! id pixoo-local >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin pixoo-local || true
    log "created system user pixoo-local"
fi

# ---- env files -----------------------------------------------------
cat > "$SECRETS_DIR/pixoo-local.env" <<EOF
# pixoo-local environment (managed by install.sh @ $TS)
PIXOO_CONFIG=$PROJ/config/config.yaml
PIXOO_SECRETS_DIR=$SECRETS_DIR
PIXOO_STATUS_DIR=/run/pixoo/status
PIXOO_LOG_LEVEL=INFO
CAPTURE_PROXY_ENABLED=false
EOF
chmod 600 "$SECRETS_DIR/pixoo-local.env"

cat > "$PROJ/.env" <<EOF
# compose overrides (managed by install.sh @ $TS)
PIXOO_PARENT_IF=$PARENT_IF
PIXOO_ADVERTISE_IP=$ADVERTISE_IP
PIXOO_SUBNET=$SUBNET
PIXOO_GATEWAY=$GATEWAY
PIXOO_LOG_LEVEL=INFO
EOF
log "wrote $PROJ/.env"

# keep advertise_ip in config.yaml in sync (non-destructive: only that line)
if grep -q 'advertise_ip:' "$PROJ/config/config.yaml"; then
    sed -i -E "s|(^\s*advertise_ip:\s*).*|\1\"$ADVERTISE_IP\"|" "$PROJ/config/config.yaml"
fi

# ---- build image ---------------------------------------------------
log "building container image (docker compose build)"
( cd "$PROJ" && docker compose build )

# ---- run tests -----------------------------------------------------
log "running test suite"
if ! "$PROJ/scripts/run_tests.sh"; then
    die "tests failed — not enabling services. Fix and re-run."
fi

# ---- host venv for the host-side helpers (editor, data + HA bridges) ----
if [ ! -x "$PROJ/.hostvenv/bin/python" ]; then
    log "creating host venv ($PROJ/.hostvenv)"
    python3 -m venv --system-site-packages "$PROJ/.hostvenv" || warn "venv creation failed"
fi
"$PROJ/.hostvenv/bin/pip" -q install --upgrade pip >/dev/null 2>&1 || true
"$PROJ/.hostvenv/bin/pip" -q install flask psutil paho-mqtt >/dev/null 2>&1 || warn "host venv deps failed"

# ---- systemd units -------------------------------------------------
log "installing systemd units"
# data/ is written by the container (uid 987) AND host services -> world-writable
# (runtime data only, no secrets ever go here).
install -d -m 0777 "$PROJ/data"
for unit in pixoo-local pixoo-databridge pixoo-editor-host pixoo-ha-bridge; do
    install -m 0644 "$PROJ/systemd/$unit.service" "/etc/systemd/system/$unit.service"
done
systemctl daemon-reload
for unit in pixoo-local pixoo-databridge pixoo-editor-host pixoo-ha-bridge; do
    systemctl enable "$unit.service" >/dev/null 2>&1 || true
done
# start the host-side helpers now (they don't touch the device directly)
systemctl restart pixoo-databridge.service pixoo-editor-host.service pixoo-ha-bridge.service >/dev/null 2>&1 || true

# ---- go live? ------------------------------------------------------
if [ "$DO_START" -eq 1 ]; then
    log "starting stack (--start)"
    ( cd "$PROJ" && docker compose up -d )
else
    log "not starting the live stack (no --start)."
fi

cat <<EOF

============================================================
 pixoo-local installed.
 Dedicated IP (bootstrap + MQTT): $ADVERTISE_IP  on $PARENT_IF
 Web UI (after start):            http://$ADVERTISE_IP:8090   user 'pixoo'
 Web password:                    $SECRETS_DIR/web-auth-password
 Secrets dir:                     $SECRETS_DIR (chmod 600)

 NEXT STEPS (manual, on your side):
 1) On the DHCP/DNS box 10.10.20.231: RESERVE/EXCLUDE $ADVERTISE_IP so DHCP
    never hands it to another device.
 2) On 10.10.20.231 DNS: add the record
        app.divoom-gz.com   A   $ADVERTISE_IP
    (no wildcard, no AAAA).
 3) Start the stack:   systemctl start pixoo-local
    or:                cd $PROJ && docker compose up -d
 4) Verify DNS from a client:  dig @10.10.20.231 app.divoom-gz.com +short
 5) Only then run the offline cold-boot test:  scripts/cold_boot_check.sh
============================================================
EOF
