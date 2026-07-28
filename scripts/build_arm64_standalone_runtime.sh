#!/usr/bin/env bash
set -euo pipefail

ROOT="$(
  cd "$(dirname "${BASH_SOURCE[0]}")/.."
  pwd
)"

OUTPUT_DIR="${1:-$ROOT/.runtime-arm64}"
LOCK_FILE="$ROOT/requirements-linux-runtime.lock"

PYTHON_VERSION="3.10.20"
BUILD_DATE="20260718"

ASSET="cpython-${PYTHON_VERSION}+${BUILD_DATE}-aarch64-unknown-linux-gnu-install_only.tar.gz"

ASSET_SHA256="506d003732d99a1598b63a40b53fa9359460a3be7488b9a3123ea6cf8ed1627b"

URL="https://github.com/astral-sh/python-build-standalone/releases/download/${BUILD_DATE}/${ASSET}"

case "$(uname -m)" in
  aarch64|arm64)
    ;;
  *)
    echo "ERROR: ARM64 runtime requires a native ARM64 host." >&2
    echo "Detected architecture: $(uname -m)" >&2
    exit 1
    ;;
esac

if [[ ! -s "$LOCK_FILE" ]]; then
  echo "ERROR: Missing dependency lock: $LOCK_FILE" >&2
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

ARCHIVE="$TMP/$ASSET"

echo "[EdgeSwarm] Downloading CPython ${PYTHON_VERSION} ARM64."

curl \
  --fail \
  --location \
  --retry 4 \
  --retry-delay 2 \
  --output "$ARCHIVE" \
  "$URL"

echo "${ASSET_SHA256}  ${ARCHIVE}" |
  sha256sum -c -

tar -xzf "$ARCHIVE" -C "$TMP"

if [[ ! -x "$TMP/python/bin/python3" ]]; then
  echo "ERROR: Extracted Python runtime is invalid." >&2
  exit 1
fi

rm -rf "$OUTPUT_DIR"
mkdir -p "$(dirname "$OUTPUT_DIR")"
mv "$TMP/python" "$OUTPUT_DIR"

ln -sfn \
  python3 \
  "$OUTPUT_DIR/bin/edgeswarm-python"

PYTHON="$OUTPUT_DIR/bin/edgeswarm-python"

"$PYTHON" -m ensurepip --upgrade || true

"$PYTHON" -m pip install \
  --upgrade \
  pip \
  setuptools \
  wheel

export CMAKE_ARGS="-DGGML_NATIVE=OFF -DGGML_OPENMP=ON"
export CMAKE_BUILD_PARALLEL_LEVEL="${CMAKE_BUILD_PARALLEL_LEVEL:-2}"
export FORCE_CMAKE=1

"$PYTHON" -m pip install \
  --no-cache-dir \
  --no-binary=llama-cpp-python \
  --requirement "$LOCK_FILE"

"$PYTHON" - <<'PY'
import platform
import sys

import customtkinter
import numpy
import psutil
import requests
import supabase
from eth_account import Account
from llama_cpp import llama_cpp

machine = platform.machine().lower()

assert sys.version_info[:3] == (3, 10, 20)
assert machine in {"aarch64", "arm64"}
assert hasattr(llama_cpp, "llama_backend_init")
assert Account.create().address

print("PYTHON_VERSION=" + sys.version.split()[0])
print("MACHINE=" + machine)
print("NUMPY_VERSION=" + numpy.__version__)
print("ARM64_RUNTIME_IMPORT_PASS=true")
PY

file "$OUTPUT_DIR/bin/python3"

LLAMA_LIBRARY="$(
  find "$OUTPUT_DIR" \
    -type f \
    \( -name '*llama*.so' -o -name '*ggml*.so' \) \
    -print \
    -quit
)"

if [[ -z "$LLAMA_LIBRARY" ]]; then
  echo "ERROR: No llama.cpp native library found." >&2
  exit 1
fi

file "$LLAMA_LIBRARY" |
  grep -Ei 'ARM aarch64|aarch64'

echo "ARM64_RUNTIME_PATH=$OUTPUT_DIR"
echo "ARM64_STANDALONE_RUNTIME_PASS=true"
