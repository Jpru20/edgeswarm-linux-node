#!/usr/bin/env python3
import argparse
import sys
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

    def load_local_json(path):
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}

    packaged_metadata = load_local_json(
        install_dir / "RELEASE_METADATA.json"
    )
    packaged_manifest = load_local_json(
        install_dir / "PACKAGE_MANIFEST.json"
    )

    packaged_package_type = str(
        packaged_manifest.get("packageType")
        or packaged_metadata.get("packageType")
        or "tar.gz"
    ).strip()

    packaged_public_release_safe = (
        packaged_manifest.get("publicReleaseSafe") is True
        and packaged_metadata.get("publicReleaseSafe") is True
    )

    query = urllib.parse.urlencode({
        "platform": "linux",
        "version": current_version,
        "arch": current_arch(),
        "packageType": packaged_package_type,
        "t": int(time.time()),
    })

    manifest_url = (
        f"{args.api_base.rstrip('/')}"
        f"/v1/node/update-manifest?{query}"
    )
    manifest_error = None

    try:
        manifest = fetch_json(manifest_url)
    except Exception as exc:
        manifest = {}
        manifest_error = (
            f"{type(exc).__name__}: {exc}"
        )

        print(
            "[EdgeSwarm] Matching remote manifest "
            "is not available; installation will "
            "continue with automatic updates disabled. "
            f"{manifest_error}",
            file=sys.stderr,
        )

    manifest_version = str(
        manifest.get("version")
        or manifest.get("latestVersion")
        or ""
    ).strip().lstrip("v")

    manifest_sha = str(
        manifest.get("sha256")
        or manifest.get("packageSha256")
        or ""
    ).strip().lower()

    manifest_package_type = str(
        manifest.get("packageType")
        or ""
    ).strip()

    version_matches = (
        manifest_version == current_version
    )

    if not version_matches:
        print(
            "[EdgeSwarm] Remote manifest version "
            f"{manifest_version or 'missing'} does not match "
            f"installed version {current_version}; "
            "automatic updates will remain disabled.",
            file=sys.stderr,
        )

    if (
        manifest
        and manifest.get("publicReleaseSafe") is not True
    ):
        raise RuntimeError(
            "Manifest is not publicReleaseSafe=true"
        )

    if manifest and not manifest_sha:
        raise RuntimeError(
            "Manifest missing sha256/packageSha256"
        )

    package_sha = (
        os.environ.get(
            "EDGESWARM_INSTALL_PACKAGE_SHA256",
            "",
        ).strip().lower()
        or str(
            packaged_metadata.get("packageSha256")
            or ""
        ).strip().lower()
    )

    download_url = (
        os.environ.get(
            "EDGESWARM_INSTALL_DOWNLOAD_URL",
            "",
        ).strip()
        or str(
            packaged_metadata.get("downloadUrl")
            or ""
        ).strip()
    )

    package_type_matches = (
        manifest_package_type == packaged_package_type
    )

    hash_recognized = bool(
        manifest.get("hashRecognized")
        and version_matches
        and package_type_matches
        and package_sha
        and package_sha == manifest_sha
    )

    if not manifest:
        hash_status = "manifest_unavailable"
    elif hash_recognized:
        hash_status = (
            manifest.get("hashStatus")
            or "recognized_package"
        )
    elif not version_matches:
        hash_status = (
            "manifest_version_mismatch"
        )
    elif not package_type_matches:
        hash_status = (
            "manifest_package_type_mismatch"
        )
    elif not package_sha:
        hash_status = (
            "installed_package_hash_unavailable"
        )
    else:
        hash_status = (
            "package_hash_pending_manifest_update"
        )

    metadata = {
        "releaseMetadataVersion":
            "linux_release_metadata_v1",
        "platform": "linux",
        "version": current_version,
        "appVersion": current_version,
        "releaseChannel": (
            packaged_metadata.get("releaseChannel")
            or manifest.get("releaseChannel")
            or "private_candidate"
        ),
        "packageType": packaged_package_type,
        "packageSha256": package_sha,
        "downloadUrl": download_url,
        "manifestVersion": manifest_version,
        "manifestPackageType": manifest_package_type,
        "manifestSha256": manifest_sha,
        "manifestError": manifest_error,
        "hashRecognized": hash_recognized,
        "hashStatus": hash_status,
        "publicReleaseSafe": bool(
            packaged_public_release_safe
            and manifest.get("publicReleaseSafe") is True
            and version_matches
            and package_type_matches
        ),
        "signatureType": (
            packaged_metadata.get("signatureType")
            or manifest.get("signatureType")
            or "unsigned_private_candidate"
        ),
        "signerStatus": (
            packaged_metadata.get("signerStatus")
            or manifest.get("signerStatus")
            or "unsigned_private_candidate"
        ),
        "notes": (
            packaged_metadata.get("notes")
            or (
                f"Linux v{current_version} "
                f"{packaged_package_type} package."
            )
        ),
        "writtenAt": int(time.time()),
    }

    (install_dir / "RELEASE_METADATA.json").write_text(
        json.dumps(metadata, indent=2) + "\n"
    )

    release_sha_path = install_dir / "RELEASE_SHA256"

    if package_sha:
        release_sha_path.write_text(package_sha + "\n")
    else:
        release_sha_path.unlink(missing_ok=True)

    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
