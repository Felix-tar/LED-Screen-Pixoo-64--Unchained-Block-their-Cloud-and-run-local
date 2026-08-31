#!/usr/bin/env bash
# =====================================================================
# pixoo-local uninstaller / rollback.
# Removes ONLY what this project added. Never touches Pi-hole, the shared
# Mosquitto, or eth0. Project files + secrets are kept unless you pass --purge.
# =====================================================================
set -euo pipefail
PROJ=/opt/pixoo-local
SECRETS_DIR=/etc/pixoo-local
PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

log() { printf '\033[36m[uninstall]\033[0m %s\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo $0)"; exit 1; }

log "stopping + disabling systemd unit"
systemctl stop pixoo-local.service 2>/dev/null || true
systemctl disable pixoo-local.service 2>/dev/null || true
rm -f /etc/systemd/system/pixoo-local.service
systemctl daemon-reload || true

log "stopping container + removing macvlan network"
( cd "$PROJ" && docker compose down 2>/dev/null ) || true
docker rm -f pixoo-local 2>/dev/null || true
docker network rm pixoo_macvlan 2>/dev/null || true

log "removing optional host-access shim (if present)"
"$PROJ/scripts/disable_host_access.sh" 2>/dev/null || true

# leave the built image unless purging
if [ "$PURGE" -eq 1 ]; then
    log "PURGE: removing image, secrets and env"
    docker image rm pixoo-local:latest 2>/dev/null || true
    read -r -p "Delete secrets in $SECRETS_DIR ? [y/N] " a
    [ "${a:-N}" = "y" ] && rm -rf "$SECRETS_DIR" && log "removed $SECRETS_DIR"
    rm -f "$PROJ/.env"
    read -r -p "Delete project files in $PROJ (keeps reports/backups)? [y/N] " b
    if [ "${b:-N}" = "y" ]; then
        find "$PROJ" -mindepth 1 -maxdepth 1 ! -name reports ! -name backups -exec rm -rf {} +
        log "removed project files (kept reports/ and backups/)"
    fi
else
    log "kept image, secrets ($SECRETS_DIR) and project files."
    log "diagnostics/reports retained under $PROJ/reports"
fi

log "done. Pi-hole, shared Mosquitto and eth0 were not touched."
