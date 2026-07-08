#!/usr/bin/env bash
set -euo pipefail

# EDGESWARM_LINUX_TKINTER_DEP_V1
echo "[EdgeSwarm] Checking Linux UI tkinter dependency..."

if ! python3 - <<'PYTK' >/dev/null 2>&1
import tkinter
PYTK
then
  if command -v apt-get >/dev/null 2>&1; then
    echo "[EdgeSwarm] Installing python3-tk for Linux UI..."
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y python3-tk
  else
    echo "[EdgeSwarm] WARNING: tkinter missing and apt-get not found. Install python3-tk manually for the UI."
  fi
fi


AUTO_UPDATE="0"
if [[ "${1:-}" == "--auto-update" ]]; then
  AUTO_UPDATE="1"
fi

INSTALL_DIR="${EDGESWARM_INSTALL_DIR:-/opt/edgeswarm-node}"
ENV_FILE="${EDGESWARM_ENV_FILE:-/etc/edgeswarm-node.env}"
SERVICE_NAME="edgeswarm-node"
UPDATER_NAME="edgeswarm-node-updater"
SERVICE_USER="${EDGESWARM_SERVICE_USER:-edgeswarm}"

echo "[EdgeSwarm] Installing Linux node to $INSTALL_DIR"

if [[ "$EUID" -ne 0 ]]; then
  echo "Please run with sudo:"
  echo "  sudo bash install.sh"
  exit 1
fi

apt-get update
apt-get install -y python3 python3-venv python3-pip ca-certificates curl tar

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi

mkdir -p "$INSTALL_DIR"
mkdir -p /var/lib/edgeswarm-node/models
mkdir -p /var/log/edgeswarm-node

tar \
  --exclude=".git" \
  --exclude=".venv" \
  --exclude="__pycache__" \
  -cf - . | tar -xf - -C "$INSTALL_DIR"

python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/python" -m pip install --upgrade pip wheel setuptools

if [[ -f "$INSTALL_DIR/requirements.txt" ]]; then
  "$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"
fi

if [[ -f "$INSTALL_DIR/scripts/edgeswarm_linux_release_metadata.py" ]]; then
  echo "[EdgeSwarm] Writing Linux release metadata."
  "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/scripts/edgeswarm_linux_release_metadata.py" \
    --install-dir "$INSTALL_DIR" \
    --api-base "${EDGESWARM_API_BASE_URL:-https://api.edgeswarm.io}" || true
fi

if [[ ! -f "$ENV_FILE" ]]; then
  if [[ -f "$INSTALL_DIR/edgeswarm-node.env.example" ]]; then
    cp "$INSTALL_DIR/edgeswarm-node.env.example" "$ENV_FILE"
  else
    cat > "$ENV_FILE" <<'EOF'
EDGESWARM_API_BASE_URL=https://api.edgeswarm.io
EDGESWARM_PROVIDER_EMAIL=
EDGESWARM_WALLET_ADDRESS=
EDGESWARM_INSTALL_DIR=/opt/edgeswarm-node
EDGESWARM_MODEL_DIR=/var/lib/edgeswarm-node/models
EDGESWARM_ENABLE_AUTO_UPDATE=1
EOF
  fi
fi

cp "$INSTALL_DIR/systemd/edgeswarm-node.service.example" "/etc/systemd/system/${SERVICE_NAME}.service"
cp "$INSTALL_DIR/systemd/edgeswarm-node-updater.service.example" "/etc/systemd/system/${UPDATER_NAME}.service"
cp "$INSTALL_DIR/systemd/edgeswarm-node-updater.timer.example" "/etc/systemd/system/${UPDATER_NAME}.timer"

chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR" /var/lib/edgeswarm-node /var/log/edgeswarm-node
chmod +x "$INSTALL_DIR/scripts/edgeswarm_linux_auto_update.py" || true

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service"
systemctl enable "${UPDATER_NAME}.timer"

if [[ "$AUTO_UPDATE" == "1" ]]; then
  echo "[EdgeSwarm] Auto-update mode. Restarting node service."
  systemctl restart "${SERVICE_NAME}.service" || true
else
  echo "[EdgeSwarm] Starting node service and updater timer."
  systemctl restart "${SERVICE_NAME}.service" || true
  systemctl restart "${UPDATER_NAME}.timer" || true
fi

echo "[EdgeSwarm] Install complete."
echo ""
echo "Next step: authenticate this Linux node:"
echo "  sudo -E /opt/edgeswarm-node/.venv/bin/python /opt/edgeswarm-node/scripts/edgeswarm_linux_login.py"
echo ""
echo "Then start the node:"
echo "  sudo systemctl restart edgeswarm-node"

echo "Node service:"
echo "  sudo systemctl status ${SERVICE_NAME} --no-pager -l"
echo "Updater timer:"
echo "  sudo systemctl status ${UPDATER_NAME}.timer --no-pager -l"


# EDGESWARM_LINUX_UI_INSTALL_V1
echo "[EdgeSwarm] Installing Linux UI files..."

if [ -f "$SRC_DIR/edgeswarm_linux_ui.py" ]; then
  cp "$SRC_DIR/edgeswarm_linux_ui.py" "$INSTALL_DIR/edgeswarm_linux_ui.py"
fi

if [ -f "$SRC_DIR/edgeswarm_ui_common.py" ]; then
  cp "$SRC_DIR/edgeswarm_ui_common.py" "$INSTALL_DIR/edgeswarm_ui_common.py"
fi

if [ -f "$SRC_DIR/edgeswarm_ui_auth.py" ]; then
  cp "$SRC_DIR/edgeswarm_ui_auth.py" "$INSTALL_DIR/edgeswarm_ui_auth.py"
fi

if [ -f "$SRC_DIR/edgeswarm_ui_login.py" ]; then
  cp "$SRC_DIR/edgeswarm_ui_login.py" "$INSTALL_DIR/edgeswarm_ui_login.py"
fi

if [ -f "$SRC_DIR/edgeswarm_ui_dashboard.py" ]; then
  cp "$SRC_DIR/edgeswarm_ui_dashboard.py" "$INSTALL_DIR/edgeswarm_ui_dashboard.py"
fi

if [ -f "$SRC_DIR/scripts/edgeswarm_linux_install_auth.py" ]; then
  mkdir -p "$INSTALL_DIR/scripts"
  cp "$SRC_DIR/scripts/edgeswarm_linux_install_auth.py" "$INSTALL_DIR/scripts/edgeswarm_linux_install_auth.py"
  chmod +x "$INSTALL_DIR/scripts/edgeswarm_linux_install_auth.py"
fi

if [ -f "$SRC_DIR/desktop/edgeswarm-node.desktop" ]; then
  mkdir -p /usr/share/applications
  cp "$SRC_DIR/desktop/edgeswarm-node.desktop" /usr/share/applications/edgeswarm-node.desktop
  chmod 644 /usr/share/applications/edgeswarm-node.desktop
fi

chmod +x "$INSTALL_DIR/edgeswarm_linux_ui.py" 2>/dev/null || true

