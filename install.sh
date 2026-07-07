#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${EDGESWARM_INSTALL_DIR:-$HOME/edgeswarm-node}"

mkdir -p "$INSTALL_DIR"
cp edgeswarm_node.py "$INSTALL_DIR/"
cp edgeswarm_linux_neural.py "$INSTALL_DIR/"
cp edgeswarm_model_provisioner.py "$INSTALL_DIR/"
cp requirements.txt "$INSTALL_DIR/"
[ -d scripts ] && cp -R scripts "$INSTALL_DIR/"
[ -d systemd ] && cp -R systemd "$INSTALL_DIR/"

if [ ! -f "$INSTALL_DIR/edgeswarm-node.env" ]; then
  cp edgeswarm-node.env.example "$INSTALL_DIR/edgeswarm-node.env"
fi

mkdir -p "$HOME/.local/share/EdgeSwarm/models"
python3 -m pip install --user --upgrade -r "$INSTALL_DIR/requirements.txt"

echo "Installed EdgeSwarm Linux node to: $INSTALL_DIR"
echo "Edit: nano $INSTALL_DIR/edgeswarm-node.env"
echo "Then run:"
echo "cd $INSTALL_DIR"
echo "python3 edgeswarm_model_provisioner.py --recommend"
echo "python3 edgeswarm_model_provisioner.py --download-recommended"
echo "python3 edgeswarm_model_provisioner.py --smoke-recommended"
