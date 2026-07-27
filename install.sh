#!/usr/bin/env bash
set -euo pipefail

AUTO_UPDATE="0"
if [[ "${1:-}" == "--auto-update" ]]; then
  AUTO_UPDATE="1"
fi

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${EDGESWARM_INSTALL_DIR:-/opt/edgeswarm-node}"
ENV_FILE="${EDGESWARM_ENV_FILE:-/etc/edgeswarm-node.env}"
SERVICE_NAME="edgeswarm-node"
UPDATER_NAME="edgeswarm-node-updater"
SERVICE_USER="${EDGESWARM_SERVICE_USER:-edgeswarm}"
SOURCE_PYTHON="$SRC_DIR/runtime/bin/edgeswarm-python"

systemd_is_running() {
  command -v systemctl >/dev/null 2>&1     && [[ -d /run/systemd/system ]]     && [[ "$(cat /proc/1/comm 2>/dev/null || true)" == "systemd" ]]
}

SUPABASE_URL_DEFAULT=https://xrmwmoqgukjztboemvgi.supabase.co
SUPABASE_ANON_KEY_DEFAULT=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InhybXdtb3FndWtqenRib2VtdmdpIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzk3MzgzNDcsImV4cCI6MjA5NTMxNDM0N30.3kP1uRFgRAgr2L2eh3Su36icRUHMEsfYIJc1RBV1jjM

echo "[EdgeSwarm] Installing Linux node to $INSTALL_DIR"

if [[ "$EUID" -ne 0 ]]; then
  echo "Please run with sudo:"
  echo "  sudo bash install.sh"
  exit 1
fi

if [[ ! -x "$SOURCE_PYTHON" ]]; then
  echo "[EdgeSwarm] Bundled runtime is missing:" >&2
  echo "  $SOURCE_PYTHON" >&2
  exit 1
fi

echo "[EdgeSwarm] Using bundled Python runtime."

"$SOURCE_PYTHON" - <<'PY_RUNTIME'
import sys

assert sys.version_info[:3] == (3, 10, 20)

print(
    "[EdgeSwarm] Bundled Python:",
    sys.version.split()[0],
)
PY_RUNTIME


EXISTING_AUTH_PRESENT="0"

if [[ -s /etc/edgeswarm-node-auth.json ]]; then
  if "$SOURCE_PYTHON" - /etc/edgeswarm-node-auth.json <<'PY_EXISTING_AUTH'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])

try:
    auth = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(1)

access_token = (
    auth.get("accessToken")
    or auth.get("access_token")
)
refresh_token = (
    auth.get("refreshToken")
    or auth.get("refresh_token")
)
provider = (
    auth.get("providerEmail")
    or auth.get("provider_email")
    or auth.get("email")
)

raise SystemExit(
    0
    if access_token and refresh_token and provider
    else 1
)
PY_EXISTING_AUTH
  then
    EXISTING_AUTH_PRESENT="1"
  fi
fi

START_NODE_AFTER_INSTALL="$AUTO_UPDATE"

if [[ "$EXISTING_AUTH_PRESENT" == "1" ]]; then
  START_NODE_AFTER_INSTALL="1"
  echo "[EdgeSwarm] Existing authenticated node detected; service will resume after installation."
fi

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi

mkdir -p "$INSTALL_DIR"
mkdir -p /var/lib/edgeswarm-node/models
mkdir -p /var/log/edgeswarm-node

if [[ "$SRC_DIR" != "$INSTALL_DIR" ]]; then
  rm -rf "$INSTALL_DIR/runtime"
fi

echo "[EdgeSwarm] Copying release files..."
tar \
  --exclude=".git" \
  --exclude=".venv" \
  --exclude="__pycache__" \
  --exclude="*.pyc" \
  --exclude="*.gguf" \
  -C "$SRC_DIR" \
  -cf - . | tar -xf - -C "$INSTALL_DIR"

RUNTIME_PYTHON="$INSTALL_DIR/runtime/bin/edgeswarm-python"

if [[ ! -x "$RUNTIME_PYTHON" ]]; then
  echo "[EdgeSwarm] Installed bundled runtime is missing." >&2
  exit 1
fi

"$RUNTIME_PYTHON" - <<'PY_IMPORTS'
import customtkinter
import psutil
import requests
import supabase
from eth_account import Account
from llama_cpp import llama_cpp

assert hasattr(llama_cpp, "llama_backend_init")
assert Account.create().address

print(
    "[EdgeSwarm] Bundled dependency validation passed."
)
PY_IMPORTS

rm -rf "$INSTALL_DIR/.venv"


PACKAGED_PUBLIC_RELEASE_SAFE="false"

if [[ -f "$INSTALL_DIR/scripts/edgeswarm_linux_release_metadata.py" ]]; then
  PACKAGED_PUBLIC_RELEASE_SAFE="$(
    "$INSTALL_DIR/runtime/bin/edgeswarm-python" - "$INSTALL_DIR/RELEASE_METADATA.json" <<'PY_METADATA'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])

try:
    data = json.loads(path.read_text())
    print("true" if data.get("publicReleaseSafe") is True else "false")
except Exception:
    print("false")
PY_METADATA
  )"

  if [[ "$PACKAGED_PUBLIC_RELEASE_SAFE" == "true" ]]; then
    echo "[EdgeSwarm] Validating public release metadata."

    "$INSTALL_DIR/runtime/bin/edgeswarm-python"       "$INSTALL_DIR/scripts/edgeswarm_linux_release_metadata.py"       --install-dir "$INSTALL_DIR"       --api-base "${EDGESWARM_API_BASE_URL:-https://api.edgeswarm.io}"

    PACKAGED_PUBLIC_RELEASE_SAFE="$(
      "$INSTALL_DIR/runtime/bin/edgeswarm-python" - "$INSTALL_DIR/RELEASE_METADATA.json" <<'PY_RECHECK_RELEASE_SAFETY'
import json
import sys
from pathlib import Path

try:
    data = json.loads(
        Path(sys.argv[1]).read_text()
    )
    print(
        "true"
        if data.get("publicReleaseSafe") is True
        else "false"
    )
except Exception:
    print("false")
PY_RECHECK_RELEASE_SAFETY
    )"

    if [[ "$PACKAGED_PUBLIC_RELEASE_SAFE" != "true" ]]; then
      echo "[EdgeSwarm] No matching public update manifest exists for this package type."
      echo "[EdgeSwarm] Automatic updates will remain disabled."
    fi
  else
    echo "[EdgeSwarm] Preserving packaged private-candidate release metadata."
  fi
fi

touch "$ENV_FILE"

upsert_env() {
  local key="$1"
  local value="$2"

  if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

upsert_env "EDGESWARM_API_BASE_URL" "https://api.edgeswarm.io"
upsert_env "EDGESWARM_API_BASE" "https://api.edgeswarm.io"
upsert_env "GCP_BASE_URL" "https://api.edgeswarm.io"
upsert_env "SUPABASE_URL" "$SUPABASE_URL_DEFAULT"
upsert_env "SUPABASE_ANON_KEY" "$SUPABASE_ANON_KEY_DEFAULT"
upsert_env "EDGESWARM_INSTALL_DIR" "$INSTALL_DIR"
upsert_env "EDGESWARM_MODEL_DIR" "/var/lib/edgeswarm-node/models"
upsert_env "EDGESWARM_AUTH_FILE" "/etc/edgeswarm-node-auth.json"
upsert_env "EDGESWARM_ENABLE_AUTO_UPDATE" "$PACKAGED_PUBLIC_RELEASE_SAFE"

touch /etc/edgeswarm-node-auth.json
chown root:"$SERVICE_USER" /etc/edgeswarm-node-auth.json
chmod 660 /etc/edgeswarm-node-auth.json

chown root:"$SERVICE_USER" "$ENV_FILE"
chmod 640 "$ENV_FILE"

if [[ -f "$INSTALL_DIR/systemd/edgeswarm-node.service.example" ]]; then
  cp "$INSTALL_DIR/systemd/edgeswarm-node.service.example" "/etc/systemd/system/${SERVICE_NAME}.service"
else
  cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=EdgeSwarm Linux Node
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_USER}
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=-${ENV_FILE}
ExecStart=${INSTALL_DIR}/runtime/bin/edgeswarm-python ${INSTALL_DIR}/edgeswarm_node.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
fi

if [[ -f "$INSTALL_DIR/systemd/edgeswarm-node-updater.service.example" ]]; then
  cp "$INSTALL_DIR/systemd/edgeswarm-node-updater.service.example" "/etc/systemd/system/${UPDATER_NAME}.service"
fi

if [[ -f "$INSTALL_DIR/systemd/edgeswarm-node-updater.timer.example" ]]; then
  cp "$INSTALL_DIR/systemd/edgeswarm-node-updater.timer.example" "/etc/systemd/system/${UPDATER_NAME}.timer"
fi

chown -R root:root "$INSTALL_DIR"
chmod -R go-w "$INSTALL_DIR"

chown -R   "$SERVICE_USER:$SERVICE_USER"   /var/lib/edgeswarm-node   /var/log/edgeswarm-node

chmod +x "$INSTALL_DIR/edgeswarm_node.py" 2>/dev/null || true
chmod +x "$INSTALL_DIR/edgeswarm_linux_ui.py" 2>/dev/null || true
chmod +x "$INSTALL_DIR/scripts/"*.py 2>/dev/null || true
chmod +x "$INSTALL_DIR/scripts/"*.sh 2>/dev/null || true
chmod 755 "$INSTALL_DIR/runtime/bin/edgeswarm-python"

echo "[EdgeSwarm] Installing CLI and desktop launcher."

install -d /usr/local/bin
ln -sfn   "$INSTALL_DIR/scripts/edgeswarm_cli.sh"   /usr/local/bin/edgeswarm

if [[ -f "$INSTALL_DIR/desktop/edgeswarm-node.desktop" ]]; then
  install -Dm644     "$INSTALL_DIR/desktop/edgeswarm-node.desktop"     /usr/share/applications/edgeswarm-node.desktop

  if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database /usr/share/applications || true
  fi
fi

if systemd_is_running; then
  systemctl daemon-reload

  if [[ "$START_NODE_AFTER_INSTALL" == "1" ]]; then
    systemctl enable "${SERVICE_NAME}.service"
  else
    echo "[EdgeSwarm] Node service remains disabled until authentication succeeds."
    systemctl disable --now "${SERVICE_NAME}.service" 2>/dev/null || true
  fi

  if [[ -f "/etc/systemd/system/${UPDATER_NAME}.timer" ]]; then
    if [[ "$PACKAGED_PUBLIC_RELEASE_SAFE" == "true" ]]; then
      systemctl enable "${UPDATER_NAME}.timer"
    else
      echo "[EdgeSwarm] Private candidate: updater timer remains disabled."
      systemctl disable --now "${UPDATER_NAME}.timer" 2>/dev/null || true
    fi
  fi

  if [[ "$START_NODE_AFTER_INSTALL" == "1" ]]; then
    echo "[EdgeSwarm] Enabling and restarting authenticated node service."
    systemctl restart "${SERVICE_NAME}.service" || true
  else
    echo "[EdgeSwarm] Node service installed but not started yet."
    echo "[EdgeSwarm] Authenticate with email/password/2FA first, then start the node."
    systemctl stop "${SERVICE_NAME}.service" || true

    if [[       -f "/etc/systemd/system/${UPDATER_NAME}.timer"       && "$PACKAGED_PUBLIC_RELEASE_SAFE" == "true"     ]]; then
      systemctl restart "${UPDATER_NAME}.timer"
    fi
  fi
else
  echo "[EdgeSwarm] systemd is not running as PID 1."
  echo "[EdgeSwarm] Unit files were installed, but service activation was skipped."
fi

echo "[EdgeSwarm] Install complete."
echo ""
echo "Authenticate and start the Linux node:"
echo "  edgeswarm login"
echo ""
echo "Node controls:"
echo "  edgeswarm start"
echo "  edgeswarm stop"
echo "  edgeswarm status"
echo "  edgeswarm logs --follow"
echo ""
echo "Desktop users can launch:"
echo "  EdgeSwarm Node"
