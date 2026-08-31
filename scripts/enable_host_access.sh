#!/usr/bin/env bash
# OPTIONAL: let the HOST (10.10.20.50) reach the macvlan container directly.
#
# By macvlan design the parent host cannot talk to its own macvlan containers.
# The Pixoo (a separate LAN device) and other LAN clients (your laptop -> web UI)
# reach 10.10.20.160 fine WITHOUT this. You only need this shim if you want to
# curl the container's bootstrap/web from the Pi itself (e.g. host-side
# cold_boot_check.sh). It adds a separate 'pixoo-shim' interface; it does NOT
# modify eth0. Reverse with disable_host_access.sh.
#
# Requires a FREE host IP for the shim (must be reserved/excluded on the DHCP
# server 10.10.20.231 just like the container IP).
set -euo pipefail

SHIM_IF="${PIXOO_SHIM_IF:-pixoo-shim}"
PARENT_IF="${PIXOO_PARENT_IF:-eth0}"
SHIM_IP="${PIXOO_SHIM_IP:-10.10.20.167}"      # free host IP for the shim
CONTAINER_IP="${PIXOO_ADVERTISE_IP:-10.10.20.160}"

if ip link show "$SHIM_IF" >/dev/null 2>&1; then
  echo "shim $SHIM_IF already exists"
else
  echo "creating macvlan shim $SHIM_IF on $PARENT_IF (host IP $SHIM_IP)"
  sudo ip link add "$SHIM_IF" link "$PARENT_IF" type macvlan mode bridge
  sudo ip addr add "$SHIM_IP/32" dev "$SHIM_IF"
  sudo ip link set "$SHIM_IF" up
fi
sudo ip route replace "$CONTAINER_IP/32" dev "$SHIM_IF"
echo "host can now reach the container at $CONTAINER_IP via $SHIM_IF"
echo "test: curl -fsS http://$CONTAINER_IP/health"
