#!/usr/bin/env python3
import argparse
import json
import os
import platform
import time
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_API_BASE = "https://api.edgeswarm.io"
DEFAULT_INSTALL_DIR = "/opt/edgeswarm-node"


def current_arch():
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "x64"
    if machine in ("aarch64", "arm64"):
        return "arm64"
    return machine or "unknown"


def read_version(install_dir):
    version_file = Path(install_dir) / "VERSION"
    if version_file.exists():
        version = version_file.read_text(errors="ignore").strip().lstrip("v")
        if version:
            return version
    return os.environ.get("EDGESWARM_NODE_VERSION", "0.0.0").strip().lstrip("v")


def fetch_json(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "EdgeSwarm-Linux-Release-Metadata/1.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--install-dir", default=os.environ.get("EDGESWARM_INSTALL_DIR", DEFAULT_INSTALL_DIR))
    parser.add_argument("--api-base", default=os.environ.get("EDGESWARM_API_BASE_URL", DEFAULT_API_BASE))
    args = parser.parse_args()

    install_dir = Path(args.install_dir)
    current_version = read_version(install_dir)

    query = urllib.parse.urlencode({
        "platform": "linux",
        "version": current_version,
        "arch": current_arch(),
        "t": int(time.time()),
    })

    manifest_url = f"{args.api_base.rstrip('/')}/v1/node/update-manifest?{query}"
    manifest = fetch_json(manifest_url)

    manifest_version = str(manifest.get("version") or manifest.get("latestVersion") or "").strip().lstrip("v")
    manifest_sha = str(manifest.get("sha256") or manifest.get("packageSha256") or "").strip().lower()

    if manifest_version != current_version:
        raise RuntimeError(f"Manifest version {manifest_version} does not match installed version {current_version}")

    if manifest.get("publicReleaseSafe") is not True:
        raise RuntimeError("Manifest is not publicReleaseSafe=true")

    if not manifest_sha:
        raise RuntimeError("Manifest missing sha256/packageSha256")

    package_sha = os.environ.get("EDGESWARM_INSTALL_PACKAGE_SHA256", "").strip().lower() or manifest_sha
    download_url = os.environ.get("EDGESWARM_INSTALL_DOWNLOAD_URL", "").strip() or str(manifest.get("downloadUrl") or "")

    metadata = {
        "releaseMetadataVersion": "linux_release_metadata_v1",
        "platform": "linux",
        "version": current_version,
        "releaseChannel": manifest.get("releaseChannel") or "public_beta",
        "packageType": manifest.get("packageType") or "tar.gz",
        "packageSha256": package_sha,
        "downloadUrl": download_url,
        "manifestSha256": manifest_sha,
        "hashRecognized": bool(manifest.get("hashRecognized")) and package_sha == manifest_sha,
        "hashStatus": (manifest.get("hashStatus") or "recognized_public_beta_package") if package_sha == manifest_sha else "package_hash_pending_manifest_update",
        "publicReleaseSafe": bool(manifest.get("publicReleaseSafe")),
        "signatureType": manifest.get("signatureType") or "unsigned_public_beta",
        "signerStatus": manifest.get("signerStatus") or "unsigned_public_beta",
        "writtenAt": int(time.time()),
    }

    (install_dir / "RELEASE_METADATA.json").write_text(json.dumps(metadata, indent=2) + "\n")
    (install_dir / "RELEASE_SHA256").write_text(package_sha + "\n")

    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
