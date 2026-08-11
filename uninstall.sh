#!/usr/bin/env bash
set -euo pipefail

PURGE=0

case "${1:-}" in
  ""|--remove)
    ;;
  --purge)
    PURGE=1
    ;;
  *)
    echo "Usage: $0 [--remove|--purge]" >&2
    exit 2
    ;;
esac

if [ "$EUID" -ne 0 ]; then
  exec sudo "$0" "$@"
fi

echo "[EdgeSwarm] Stopping node services."
systemctl disable --now edgeswarm-node.service 2>/dev/null || true
systemctl disable --now edgeswarm-node-updater.timer 2>/dev/null || true
systemctl disable --now edgeswarm-node-model-provisioner.timer 2>/dev/null || true
systemctl stop edgeswarm-node-model-provisioner.service 2>/dev/null || true

rm -f /etc/systemd/system/edgeswarm-node.service
rm -f /etc/systemd/system/edgeswarm-node-updater.service
rm -f /etc/systemd/system/edgeswarm-node-updater.timer
rm -f /etc/systemd/system/edgeswarm-node-model-provisioner.service
rm -f /etc/systemd/system/edgeswarm-node-model-provisioner.timer
rm -f /usr/local/bin/edgeswarm
rm -f /usr/share/applications/edgeswarm-node.desktop
rm -rf /opt/edgeswarm-node

if [ "$PURGE" -eq 1 ]; then
  echo "[EdgeSwarm] Purging persistent node state."
  rm -f /etc/edgeswarm-node-auth.json
  rm -f /etc/edgeswarm-node-wallet.key
  rm -f /etc/edgeswarm-node.env
  rm -rf /var/lib/edgeswarm-node
  rm -rf /var/log/edgeswarm-node
else
  echo "[EdgeSwarm] Persistent auth, wallet, and model state preserved."
fi

systemctl daemon-reload 2>/dev/null || true
systemctl reset-failed 2>/dev/null || true

echo "[EdgeSwarm] Uninstall complete."
