#!/usr/bin/env bash
# Install + configure the Homebridge "Pixoo Screen" switch (homebridge-mqttthing)
# pointing at the ha-bridge MQTT topics. Idempotent; backs up config.json.
# Requires the standard hb-service Homebridge install on this host.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo $0)"; exit 1; }

PLUGIN=homebridge-mqttthing
HBDIR=/var/lib/homebridge
CFG="$HBDIR/config.json"
[ -f "$CFG" ] || { echo "[hb] Homebridge config not found ($CFG) — is Homebridge installed?"; exit 1; }

echo "[hb] ensuring plugin $PLUGIN"
if [ ! -d "$HBDIR/node_modules/$PLUGIN" ]; then
    if command -v hb-service >/dev/null 2>&1; then
        hb-service add "$PLUGIN"
    else
        ( cd "$HBDIR" && sudo -u homebridge /opt/homebridge/bin/npm install --prefix "$HBDIR" "$PLUGIN" )
    fi
else
    echo "[hb]   already installed"
fi

echo "[hb] adding/updating the Pixoo Screen accessory in config.json"
python3 - <<'PY'
import json, os, time, shutil, pwd, grp, sys
sys.path.insert(0, "/opt/pixoo-local")
try:
    from common.config import load
    c = load("/opt/pixoo-local/config/config.yaml")
    hc = c.get("homeassistant", default={}) or {}
    host, port = hc.get("mqtt_host", "10.10.20.50"), hc.get("mqtt_port", 1883)
    node, name = hc.get("node_id", "pixoo64"), hc.get("device_name", "Pixoo Screen")
except Exception:
    host, port, node, name = "10.10.20.50", 1883, "pixoo64", "Pixoo Screen"
CFG = "/var/lib/homebridge/config.json"
shutil.copy2(CFG, CFG + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
d = json.load(open(CFG))
acc = [a for a in d.get("accessories", []) if a.get("name") != name]
acc.append({
    "accessory": "mqttthing", "type": "switch", "name": name,
    "url": f"mqtt://{host}:{port}",
    "topics": {"getOn": f"pixoo/{node}/screen/state", "setOn": f"pixoo/{node}/screen/set",
               "getOnline": f"pixoo/{node}/availability"},
    "onValue": "ON", "offValue": "OFF", "onlineValue": "online", "offlineValue": "offline",
})
d["accessories"] = acc
tmp = CFG + ".tmp"
json.dump(d, open(tmp, "w"), indent=4)
os.replace(tmp, CFG)
os.chown(CFG, pwd.getpwnam("homebridge").pw_uid, grp.getgrnam("homebridge").gr_gid)
os.chmod(CFG, 0o644)
json.load(open(CFG))  # validate
print(f"[hb]   accessory '{name}' set")
PY

echo "[hb] restarting Homebridge"
systemctl restart homebridge
echo "[hb] done — 'Pixoo Screen' is now in Homebridge / Apple Home"
