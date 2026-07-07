#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "EdgeSwarm Linux neural runtime installer"

python3 -m pip install --upgrade pip wheel setuptools

if command -v nvidia-smi >/dev/null 2>&1; then
  echo "NVIDIA GPU detected. Installing llama-cpp-python with CUDA build flags."
  CMAKE_ARGS="-DGGML_CUDA=on" FORCE_CMAKE=1 \
    python3 -m pip install --upgrade --force-reinstall --no-cache-dir llama-cpp-python
else
  echo "No NVIDIA GPU detected. Installing CPU llama-cpp-python."
  python3 -m pip install --upgrade llama-cpp-python
fi

python3 - <<'PY'
from llama_cpp import Llama
print("llama_cpp import OK")
PY

python3 edgeswarm_linux_neural.py --list-models
