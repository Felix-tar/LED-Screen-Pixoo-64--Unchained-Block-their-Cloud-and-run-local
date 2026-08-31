#!/usr/bin/env bash
# Securely store the Claude Code OAuth token for the usage data-bridge.
# Reads the token from STDIN (never argv/history), writes it to the chmod-600
# secret file, and restarts the data bridge. The token is NEVER printed.
#
#   sudo scripts/set_claude_token.sh          # then paste token + Enter
#   printf '%s' "$TOKEN" | sudo scripts/set_claude_token.sh
set -euo pipefail
SECRET=/etc/pixoo-local/claude-oauth-token
[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo $0)"; exit 1; }

if [ -t 0 ]; then
  printf 'Paste Claude Code OAuth token (input hidden): ' >&2
  read -rs TOKEN; echo >&2
else
  read -r TOKEN
fi
[ -n "${TOKEN:-}" ] || { echo "no token given"; exit 1; }

install -d -m 0700 -o root -g root /etc/pixoo-local
umask 077
printf '%s' "$TOKEN" > "$SECRET"
chmod 600 "$SECRET"; chown root:root "$SECRET"
unset TOKEN
echo "token stored in $SECRET (chmod 600)"

systemctl restart pixoo-databridge.service 2>/dev/null && echo "data bridge restarted" || \
  echo "note: restart the data bridge to apply (systemctl restart pixoo-databridge)"
