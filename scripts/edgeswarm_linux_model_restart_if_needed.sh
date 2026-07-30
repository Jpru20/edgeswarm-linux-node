#!/usr/bin/env bash
set -euo pipefail

MARKER="/run/edgeswarm-node-model-provisioner/restart-required"

if [[ ! -f "$MARKER" ]]; then
  exit 0
fi

rm -f "$MARKER"
systemctl try-restart edgeswarm-node.service
