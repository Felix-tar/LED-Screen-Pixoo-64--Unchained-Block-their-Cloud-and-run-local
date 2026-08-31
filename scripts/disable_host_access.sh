#!/usr/bin/env bash
# Reverse enable_host_access.sh — remove the optional macvlan shim.
set -euo pipefail
SHIM_IF="${PIXOO_SHIM_IF:-pixoo-shim}"
if ip link show "$SHIM_IF" >/dev/null 2>&1; then
  sudo ip link set "$SHIM_IF" down || true
  sudo ip link delete "$SHIM_IF" || true
  echo "removed shim $SHIM_IF"
else
  echo "shim $SHIM_IF not present"
fi
