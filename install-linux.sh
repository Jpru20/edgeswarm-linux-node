#!/usr/bin/env bash
set -euo pipefail

API_BASE="${EDGESWARM_API_BASE_URL:-https://api.edgeswarm.io}"

if [[ "$EUID" -ne 0 ]]; then
  echo "Run this installer with sudo:" >&2
  echo "  sudo bash install-linux.sh" >&2
  exit 1
fi

case "$(uname -m)" in
  x86_64|amd64)
    ARCHITECTURE="x64"
    ;;
  aarch64|arm64)
    ARCHITECTURE="arm64"
    ;;
  *)
    echo "Unsupported architecture: $(uname -m)" >&2
    exit 1
    ;;
esac

if [[ "${EDGESWARM_FORCE_TAR_INSTALL:-0}" = "1" ]]; then
  PACKAGE_TYPE="tar.gz"

  for tool in curl python3 tar sha256sum; do
    if ! command -v "$tool" >/dev/null 2>&1; then
      echo "Required command is missing: $tool" >&2
      exit 1
    fi
  done

elif command -v apt-get >/dev/null 2>&1; then
  PACKAGE_TYPE="deb"

  apt-get update

  DEBIAN_FRONTEND=noninteractive \
    apt-get install -y \
      ca-certificates \
      curl \
      python3

elif command -v dnf >/dev/null 2>&1; then
  PACKAGE_TYPE="rpm"

  dnf install -y \
    ca-certificates \
    curl \
    python3

elif command -v yum >/dev/null 2>&1; then
  PACKAGE_TYPE="rpm"

  yum install -y \
    ca-certificates \
    curl \
    python3

else
  PACKAGE_TYPE="tar.gz"

  for tool in curl python3 tar sha256sum; do
    if ! command -v "$tool" >/dev/null 2>&1; then
      echo "Required command is missing: $tool" >&2
      exit 1
    fi
  done
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

MANIFEST_FILE="$TMP/update-manifest.json"

QUERY="$(
  python3 - \
    "$ARCHITECTURE" \
    "$PACKAGE_TYPE" <<'PY'
import sys
import urllib.parse

print(
    urllib.parse.urlencode({
        "platform": "linux",
        "version": "0.0.0",
        "arch": sys.argv[1],
        "packageType": sys.argv[2],
    })
)
PY
)"

MANIFEST_URL="${API_BASE%/}/v1/node/update-manifest?${QUERY}"

echo "[EdgeSwarm] Architecture: $ARCHITECTURE"
echo "[EdgeSwarm] Package type: $PACKAGE_TYPE"
echo "[EdgeSwarm] Requesting current release manifest."

curl \
  --fail \
  --location \
  --retry 4 \
  --retry-delay 2 \
  --output "$MANIFEST_FILE" \
  "$MANIFEST_URL"

eval "$(
  python3 - \
    "$MANIFEST_FILE" \
    "$ARCHITECTURE" \
    "$PACKAGE_TYPE" <<'PY'
import json
import shlex
import sys
from pathlib import Path

manifest = json.loads(
    Path(sys.argv[1]).read_text()
)

expected_arch = sys.argv[2]
expected_type = sys.argv[3]

architecture = str(
    manifest.get("architecture")
    or manifest.get("arch")
    or ""
).strip()

package_type = str(
    manifest.get("packageType")
    or ""
).strip()

version = str(
    manifest.get("latestVersion")
    or manifest.get("version")
    or ""
).strip().lstrip("v")

download_url = str(
    manifest.get("downloadUrl")
    or ""
).strip()

sha256 = str(
    manifest.get("sha256")
    or manifest.get("packageSha256")
    or ""
).strip().lower()

if manifest.get("publicReleaseSafe") is not True:
    raise SystemExit(
        "Manifest is not publicReleaseSafe=true"
    )

if architecture != expected_arch:
    raise SystemExit(
        "Manifest architecture mismatch: "
        f"expected={expected_arch}, actual={architecture}"
    )

if package_type != expected_type:
    raise SystemExit(
        "Manifest package type mismatch: "
        f"expected={expected_type}, actual={package_type}"
    )

for name, value in {
    "version": version,
    "downloadUrl": download_url,
    "sha256": sha256,
}.items():
    if not value:
        raise SystemExit(
            f"Manifest missing {name}"
        )

values = {
    "RELEASE_VERSION": version,
    "DOWNLOAD_URL": download_url,
    "EXPECTED_SHA256": sha256,
}

for name, value in values.items():
    print(
        f"{name}={shlex.quote(value)}"
    )
PY
)"

case "$PACKAGE_TYPE" in
  deb)
    ARTIFACT="$TMP/edgeswarm-node.deb"
    ;;
  rpm)
    ARTIFACT="$TMP/edgeswarm-node.rpm"
    ;;
  tar.gz)
    ARTIFACT="$TMP/edgeswarm-node.tar.gz"
    ;;
esac

echo "[EdgeSwarm] Downloading v${RELEASE_VERSION}."

curl \
  --fail \
  --location \
  --retry 4 \
  --retry-delay 2 \
  --output "$ARTIFACT" \
  "$DOWNLOAD_URL"

ACTUAL_SHA256="$(
  sha256sum "$ARTIFACT" |
  awk '{print $1}'
)"

if [[ "$ACTUAL_SHA256" != "$EXPECTED_SHA256" ]]; then
  echo "Downloaded artifact SHA-256 mismatch." >&2
  echo "Expected: $EXPECTED_SHA256" >&2
  echo "Actual:   $ACTUAL_SHA256" >&2
  exit 1
fi

export EDGESWARM_INSTALL_PACKAGE_SHA256="$EXPECTED_SHA256"
export EDGESWARM_INSTALL_DOWNLOAD_URL="$DOWNLOAD_URL"
export EDGESWARM_API_BASE_URL="${API_BASE%/}"

case "$PACKAGE_TYPE" in
  deb)
    DEBIAN_FRONTEND=noninteractive \
      apt-get install -y "$ARTIFACT"
    ;;

  rpm)
    if command -v dnf >/dev/null 2>&1; then
      dnf install -y \
        --nogpgcheck \
        "$ARTIFACT"
    elif command -v yum >/dev/null 2>&1; then
      yum localinstall -y "$ARTIFACT"
    else
      rpm -Uvh "$ARTIFACT"
    fi
    ;;

  tar.gz)
    EXTRACT_DIR="$TMP/extracted"
    mkdir -p "$EXTRACT_DIR"

    tar -xzf "$ARTIFACT" \
      -C "$EXTRACT_DIR"

    INSTALL_SCRIPT="$(
      find "$EXTRACT_DIR" \
        -maxdepth 2 \
        -type f \
        -name install.sh \
        -print \
        -quit
    )"

    if [[ -z "$INSTALL_SCRIPT" ]]; then
      INSTALL_SCRIPT="$(
        find "$EXTRACT_DIR" \
          -maxdepth 2 \
          -type f \
          -name install-linux.sh \
          -print \
          -quit
      )"
    fi

    if [[ -z "$INSTALL_SCRIPT" ]]; then
      echo "Downloaded tarball has no install.sh or install-linux.sh." >&2
      exit 1
    fi

    bash "$INSTALL_SCRIPT"
    ;;
esac

python3 - \
  "$RELEASE_VERSION" \
  "$ARCHITECTURE" \
  "$PACKAGE_TYPE" <<'PY'
import json
import sys
from pathlib import Path

expected_version = sys.argv[1]
expected_arch = sys.argv[2]
expected_type = sys.argv[3]

root = Path("/opt/edgeswarm-node")
version_path = root / "VERSION"
manifest_path = root / "PACKAGE_MANIFEST.json"

if not version_path.is_file():
    raise SystemExit(
        "Installed VERSION file is missing."
    )

installed_version = (
    version_path.read_text()
    .strip()
    .lstrip("v")
)

if installed_version != expected_version:
    raise SystemExit(
        "Installed version mismatch: "
        f"expected={expected_version}, "
        f"actual={installed_version}"
    )

if not manifest_path.is_file():
    raise SystemExit(
        "Installed package manifest is missing."
    )

manifest = json.loads(
    manifest_path.read_text()
)

if manifest.get("architecture") != expected_arch:
    raise SystemExit(
        "Installed architecture mismatch."
    )

if manifest.get("packageType") != expected_type:
    raise SystemExit(
        "Installed package type mismatch."
    )

print("INSTALLED_VERSION=" + installed_version)
print("INSTALLED_ARCHITECTURE=" + expected_arch)
print("INSTALLED_PACKAGE_TYPE=" + expected_type)
print("EDGESWARM_UNIVERSAL_INSTALL_PASS=true")
PY
