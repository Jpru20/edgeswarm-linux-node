#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

BUILD_LABEL="${1:-v0.1.1_Linux_NeuralReady_TestOnly}"
PACKAGE_NAME="EdgeSwarm_Linux_${BUILD_LABEL}.tar.gz"

echo "== Running Linux preflight =="
./scripts/preflight_linux_neural_ready.sh

echo ""
echo "== Creating Linux package =="
mkdir -p release
rm -f "release/$PACKAGE_NAME"

tar -czf "release/$PACKAGE_NAME" \
  edgeswarm_node.py \
  edgeswarm_linux_neural.py \
  scripts/preflight_linux_neural_ready.sh \
  scripts/install_linux_neural_runtime.sh

echo ""
echo "== Built =="
ls -lh "release/$PACKAGE_NAME"

echo ""
echo "== SHA256 =="
sha256sum "release/$PACKAGE_NAME"
