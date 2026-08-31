#!/bin/sh
# Container entrypoint: prepare runtime dirs + broker auth, then run supervisord.
set -eu

SECRETS_DIR="${PIXOO_SECRETS_DIR:-/etc/pixoo-local}"
RUN_DIR="${PIXOO_RUN_DIR:-/run/pixoo}"
STATUS_DIR="${PIXOO_STATUS_DIR:-/run/pixoo/status}"

echo "[entrypoint] preparing runtime dirs"
mkdir -p "$RUN_DIR" "$STATUS_DIR" /var/lib/mosquitto
# status dir: root (bootstrap) + pixoo (web) both write here
chown pixoo:pixoo "$STATUS_DIR" || true
chmod 0775 "$STATUS_DIR" || true
chown mosquitto:mosquitto /var/lib/mosquitto || true

# fail early with a clear message if secrets are missing
for f in device-token server-mqtt-password; do
    if [ ! -s "$SECRETS_DIR/$f" ]; then
        echo "[entrypoint] FATAL: missing secret $SECRETS_DIR/$f (run scripts/install.sh)" >&2
        exit 1
    fi
done

# Re-materialize secrets from the read-only mount into a tmpfs dir with correct
# per-file ownership so non-root services (heartbeat/web as 'pixoo') can read
# only what they need. device-token stays root-only (bootstrap + broker gen).
LOCAL_SECRETS=/run/pixoo/secrets
echo "[entrypoint] materializing secrets into $LOCAL_SECRETS"
mkdir -p "$LOCAL_SECRETS"
chmod 0750 "$LOCAL_SECRETS"; chown root:pixoo "$LOCAL_SECRETS" || true
install -m 0600 -o root -g root "$SECRETS_DIR/device-token" "$LOCAL_SECRETS/device-token"
install -m 0640 -o root -g pixoo "$SECRETS_DIR/server-mqtt-password" "$LOCAL_SECRETS/server-mqtt-password"
if [ -s "$SECRETS_DIR/web-auth-password" ]; then
    install -m 0640 -o root -g pixoo "$SECRETS_DIR/web-auth-password" "$LOCAL_SECRETS/web-auth-password"
fi
# point all services at the re-materialized copies
export PIXOO_SECRETS_DIR="$LOCAL_SECRETS"

echo "[entrypoint] generating mosquitto passwd + acl"
PIXOO_SECRETS_DIR="$LOCAL_SECRETS" python3 /opt/pixoo-local/scripts/gen_mosquitto_auth.py

echo "[entrypoint] starting supervisord"
exec /usr/bin/supervisord -c /etc/supervisor/pixoo-supervisord.conf
