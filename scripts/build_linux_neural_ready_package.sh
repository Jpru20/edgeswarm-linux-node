#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALLED_ROOT="${EDGESWARM_INSTALLED_ROOT:-/opt/edgeswarm-node}"
STANDALONE_RUNTIME="${EDGESWARM_STANDALONE_RUNTIME:-}"

if [[ -x "$ROOT/runtime/bin/edgeswarm-python" ]]; then
  PYTHON="$ROOT/runtime/bin/edgeswarm-python"
elif [[ -x "$INSTALLED_ROOT/.venv/bin/python" ]]; then
  PYTHON="$INSTALLED_ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="$(command -v python3)"
else
  echo "No usable Python interpreter found for package validation." >&2
  exit 1
fi

VERSION="$(tr -d "[:space:]" < "$ROOT/VERSION")"
RELEASE_MODE="${1:-}"

if [[ -z "$RELEASE_MODE" ]]; then
  RELEASE_MODE="--private-candidate"
fi

RELEASE_DIR="$ROOT/release"
TMP="$(mktemp -d)"

case "$(uname -m)" in
  x86_64|amd64)
    PACKAGE_ARCH="x64"
    DEB_ARCH="amd64"
    RPM_ARCH="x86_64"
    ;;
  aarch64|arm64)
    PACKAGE_ARCH="arm64"
    DEB_ARCH="arm64"
    RPM_ARCH="aarch64"
    ;;
  *)
    echo "Unsupported architecture: $(uname -m)" >&2
    exit 1
    ;;
esac

if [[ ! -x "$STANDALONE_RUNTIME/bin/edgeswarm-python" ]]; then
  echo "Missing standalone runtime: $STANDALONE_RUNTIME" >&2
  exit 1
fi

case "$RELEASE_MODE" in
  --public-beta|public_beta)
    RELEASE_CHANNEL="public_beta"
    RELEASE_LABEL="PublicBeta"
    PUBLIC_RELEASE_SAFE="true"
    HASH_STATUS="public_beta_package_pending_publish"
    SIGNATURE_TYPE="unsigned_public_beta"
    SIGNER_STATUS="unsigned_public_beta"
    RELEASE_KIND="public beta"
    ;;
  --private-candidate|private_candidate)
    RELEASE_CHANNEL="private_candidate"
    RELEASE_LABEL="PrivateCandidate"
    PUBLIC_RELEASE_SAFE="false"
    HASH_STATUS="private_candidate_package_unpublished"
    SIGNATURE_TYPE="unsigned_private_candidate"
    SIGNER_STATUS="unsigned_private_candidate"
    RELEASE_KIND="private candidate"
    ;;
  *)
    echo "Usage: $0 [--private-candidate|--public-beta]" >&2
    exit 1
    ;;
esac

LABEL="EdgeSwarm_Linux_${PACKAGE_ARCH}_v${VERSION}_${RELEASE_LABEL}"
TAR_PATH="$RELEASE_DIR/${LABEL}.tar.gz"
DEB_PATH="$RELEASE_DIR/${LABEL}.deb"
RPM_PATH="$RELEASE_DIR/${LABEL}.rpm"

cleanup() {
  rm -rf "$TMP"
}
trap cleanup EXIT

copy_release_source() {
  local destination="$1"
  local package_type="$2"

  mkdir -p "$destination"

  tar \
    --exclude=".git" \
    --exclude="./.git" \
    --exclude=".venv" \
    --exclude="./.venv" \
    --exclude="release" \
    --exclude="./release" \
    --exclude="__pycache__" \
    --exclude="*.pyc" \
    --exclude="*.gguf" \
    --exclude="*.before_*" \
    --exclude="*.backup_*" \
    --exclude="*.incorrect_*" \
    --exclude="RELEASE_SHA256" \
    -C "$ROOT" \
    -cf - . \
  | tar -xf - -C "$destination"

  case "$package_type" in
    deb|rpm|tar.gz)
      echo "[EdgeSwarm] Embedding shared standalone runtime for $package_type."
      mkdir -p "$destination/runtime"
      tar -C "$STANDALONE_RUNTIME" -cf - . | tar -xf - -C "$destination/runtime"
      test -x "$destination/runtime/bin/edgeswarm-python"
      rm -f "$destination/runtime/lib/edgeswarm-native/libgcc_s.so.1"
      test ! -e "$destination/runtime/lib/edgeswarm-native/libgcc_s.so.1"
      echo "[EdgeSwarm] Using distribution-provided libgcc_s.so.1."
      ;;
  esac

  "$PYTHON" -     "$destination"     "$VERSION"     "$package_type"     "$RELEASE_CHANNEL"     "$PUBLIC_RELEASE_SAFE"     "$HASH_STATUS"     "$SIGNATURE_TYPE"     "$SIGNER_STATUS"     "$RELEASE_KIND" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
version = sys.argv[2]
package_type = sys.argv[3]
release_channel = sys.argv[4]
public_release_safe = sys.argv[5].lower() == "true"
hash_status = sys.argv[6]
signature_type = sys.argv[7]
signer_status = sys.argv[8]
release_kind = sys.argv[9]
metadata_path = root / "RELEASE_METADATA.json"

metadata = {}
if metadata_path.exists():
    try:
        metadata = json.loads(metadata_path.read_text())
    except Exception:
        metadata = {}

metadata.update({
    "releaseMetadataVersion": "linux_release_metadata_v1",
    "platform": "linux",
    "version": version,
    "appVersion": version,
    "releaseChannel": release_channel,
    "packageType": package_type,
    "packageSha256": "",
    "downloadUrl": "",
    "manifestSha256": "",
    "hashRecognized": False,
    "hashStatus": hash_status,
    "publicReleaseSafe": public_release_safe,
    "signatureType": signature_type,
    "signerStatus": signer_status,
    "notes": (
        f"Linux v{version} {release_kind} "
        f"{package_type} package."
    ),
})

metadata_path.write_text(
    json.dumps(metadata, indent=2) + "\n"
)
PY

  "$PYTHON" -     "$destination"     "$VERSION"     "$PACKAGE_ARCH"     "$package_type"     "$RELEASE_CHANNEL"     "$PUBLIC_RELEASE_SAFE" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
version = sys.argv[2]
architecture = sys.argv[3]
package_type = sys.argv[4]
release_channel = sys.argv[5]
public_release_safe = sys.argv[6].lower() == "true"

files = {}

for path in sorted(root.rglob("*")):
    if not path.is_file():
        continue

    relative = path.relative_to(root).as_posix()

    if relative == "PACKAGE_MANIFEST.json":
        continue

    digest = hashlib.sha256(path.read_bytes()).hexdigest()

    files[relative] = {
        "sha256": digest,
        "sizeBytes": path.stat().st_size,
    }

manifest = {
    "manifestVersion": "edgeswarm_linux_package_manifest_v1",
    "platform": "linux",
    "architecture": architecture,
    "version": version,
    "releaseChannel": release_channel,
    "packageType": package_type,
    "publicReleaseSafe": public_release_safe,
    "runtimeSha256": files["edgeswarm_node.py"]["sha256"],
    "neuralModuleSha256": files["edgeswarm_linux_neural.py"]["sha256"],
    "files": files,
}

(root / "PACKAGE_MANIFEST.json").write_text(
    json.dumps(manifest, indent=2) + "\n"
)
PY

  find "$destination" \
    -path "$destination/runtime" -prune -o \
    -type f \
    -name '*.py' \
    -print0 \
  | xargs -0 "$PYTHON" -m py_compile

  find "$destination" \
    -path "$destination/runtime" -prune -o \
    -type f \
    -name '*.sh' \
    -print0 \
  | xargs -0 -n1 bash -n
}

echo "=== PREFLIGHT ==="

PREFLIGHT_ROOT="$TMP/preflight-source"
mkdir -p "$PREFLIGHT_ROOT"

tar \
  --exclude=".git" \
  --exclude=".venv" \
  --exclude="runtime" \
  --exclude="release" \
  --exclude="__pycache__" \
  --exclude="*.pyc" \
  --exclude="*.gguf" \
  --exclude="*.before_*" \
  --exclude="*.backup_*" \
  -C "$ROOT" \
  -cf - . \
| tar -xf - -C "$PREFLIGHT_ROOT"

chmod a+rx "$TMP"
chmod -R a+rX "$PREFLIGHT_ROOT"

PREFLIGHT_PYCACHE="$TMP/preflight-pycache"
mkdir -p "$PREFLIGHT_PYCACHE"
chmod 1777 "$PREFLIGHT_PYCACHE"

sudo -u edgeswarm env \
  HOME=/home/edgeswarm \
  PYTHONPYCACHEPREFIX="$PREFLIGHT_PYCACHE" \
  XDG_DATA_HOME=/home/edgeswarm/.local/share \
  EDGESWARM_MODEL_DIR=/var/lib/edgeswarm-node/models \
  EDGESWARM_PYTHON="$PYTHON" \
  bash "$PREFLIGHT_ROOT/scripts/preflight_linux_neural_ready.sh"

sudo chown -R "$(id -u):$(id -g)" "$PREFLIGHT_PYCACHE"

mkdir -p "$RELEASE_DIR"
rm -f "$TAR_PATH" "$DEB_PATH" "$RPM_PATH"

echo
echo "=== BUILD TAR.GZ ==="

TAR_ROOT="$TMP/EdgeSwarm_Linux_v${VERSION}"
copy_release_source "$TAR_ROOT" "tar.gz"

tar \
  -C "$TMP" \
  -czf "$TAR_PATH" \
  "EdgeSwarm_Linux_v${VERSION}"

echo
echo "=== BUILD DEB ==="

DEB_ROOT="$TMP/deb"
DEB_SOURCE="$DEB_ROOT/usr/lib/edgeswarm-node-package"

mkdir -p "$DEB_ROOT/DEBIAN"
copy_release_source "$DEB_SOURCE" "deb"

cat > "$DEB_ROOT/DEBIAN/control" <<EOF
Package: edgeswarm-node
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: ${DEB_ARCH}
Maintainer: EdgeSwarm <support@edgeswarm.io>
Depends: libc6 (>= 2.34), libgcc-s1, libgomp1, systemd, libx11-6, libxext6, libxrender1, libxft2, fontconfig
Description: EdgeSwarm Linux compute node
 Self-contained desktop and headless EdgeSwarm node with deterministic and
 local neural inference support.
EOF

cat > "$DEB_ROOT/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e

export EDGESWARM_SKIP_SYSTEM_PACKAGES=1
bash /usr/lib/edgeswarm-node-package/install.sh

exit 0
EOF

cat > "$DEB_ROOT/DEBIAN/prerm" <<'EOF'
#!/bin/sh
set -e

systemctl stop edgeswarm-node.service 2>/dev/null || true
systemctl stop edgeswarm-node-updater.timer 2>/dev/null || true

exit 0
EOF

cat > "$DEB_ROOT/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e

case "${1:-}" in
  remove|purge)
    systemctl disable edgeswarm-node.service 2>/dev/null || true
    systemctl disable edgeswarm-node-updater.timer 2>/dev/null || true

    rm -f /usr/local/bin/edgeswarm
    rm -f /usr/share/applications/edgeswarm-node.desktop
    rm -rf /opt/edgeswarm-node

    systemctl daemon-reload 2>/dev/null || true
    ;;
esac

exit 0
EOF

chmod 755 \
  "$DEB_ROOT/DEBIAN/postinst" \
  "$DEB_ROOT/DEBIAN/prerm" \
  "$DEB_ROOT/DEBIAN/postrm"

dpkg-deb \
  --build \
  --root-owner-group \
    -Zgzip \
    -z1 \
  "$DEB_ROOT" \
  "$DEB_PATH"

echo
echo "=== BUILD RPM ==="

RPM_TOPDIR="$TMP/rpmbuild"
RPM_SOURCE_ROOT="$TMP/edgeswarm-node-package"
RPM_SPEC="$RPM_TOPDIR/SPECS/edgeswarm-node.spec"

mkdir -p "$RPM_TOPDIR"/BUILD
mkdir -p "$RPM_TOPDIR"/BUILDROOT
mkdir -p "$RPM_TOPDIR"/RPMS
mkdir -p "$RPM_TOPDIR"/SOURCES
mkdir -p "$RPM_TOPDIR"/SPECS
mkdir -p "$RPM_TOPDIR"/SRPMS

copy_release_source "$RPM_SOURCE_ROOT" "rpm"

tar   -C "$TMP"   -czf "$RPM_TOPDIR/SOURCES/edgeswarm-node-package.tar.gz"   "edgeswarm-node-package"

sed   -e "s/@VERSION@/${VERSION}/g"   -e "s/@RPM_ARCH@/${RPM_ARCH}/g"   "$ROOT/packaging/rpm/edgeswarm-node.spec.in"   > "$RPM_SPEC"

rpmbuild   --define "_topdir $RPM_TOPDIR"   -bb "$RPM_SPEC"

BUILT_RPM="$(
  find "$RPM_TOPDIR/RPMS"     -type f     -name "edgeswarm-node-*.rpm"     -print     -quit
)"

if [[ -z "$BUILT_RPM" ]]; then
  echo "RPM build completed without an artifact." >&2
  exit 1
fi

cp "$BUILT_RPM" "$RPM_PATH"

echo
echo "=== PACKAGE RESULTS ==="

ls -lh "$TAR_PATH" "$DEB_PATH" "$RPM_PATH"
sha256sum "$TAR_PATH" "$DEB_PATH" "$RPM_PATH"

echo
echo "=== EMBEDDED HASHES ==="

"$PYTHON" - "$TAR_ROOT/PACKAGE_MANIFEST.json" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text())

print("VERSION=" + manifest["version"])
print("ARCHITECTURE=" + manifest["architecture"])
print("RUNTIME_SHA256=" + manifest["runtimeSha256"])
print("NEURAL_SHA256=" + manifest["neuralModuleSha256"])
print("FILE_COUNT=" + str(len(manifest["files"])))
PY

echo
echo "TAR_PATH=$TAR_PATH"
echo "DEB_PATH=$DEB_PATH"
echo "RPM_PATH=$RPM_PATH"
echo "PACKAGE_BUILD_PASS=true"
