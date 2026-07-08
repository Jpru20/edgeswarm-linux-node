#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_API_BASE = "https://api.edgeswarm.io"
DEFAULT_INSTALL_DIR = "/opt/edgeswarm-node"
DEFAULT_ENV_FILE = "/etc/edgeswarm-node.env"


def load_env_file(path):
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def current_arch():
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "x64"
    if machine in ("aarch64", "arm64"):
        return "arm64"
    return machine or "unknown"


def read_current_version(install_dir):
    env_version = os.environ.get("EDGESWARM_NODE_VERSION", "").strip().lstrip("v")
    if env_version:
        return env_version

    version_file = Path(install_dir) / "VERSION"
    if version_file.exists():
        version = version_file.read_text(errors="ignore").strip().lstrip("v")
        if version:
            return version

    return "0.0.0"


def fetch_json(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "EdgeSwarm-Linux-Updater/1.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.loads(resp.read().decode("utf-8"))


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download_file(url, dest):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "EdgeSwarm-Linux-Updater/1.0"},
    )
    with urllib.request.urlopen(req, timeout=180) as resp, Path(dest).open("wb") as out:
        shutil.copyfileobj(resp, out)


def safe_extract_tar_gz(tar_path, dest):
    dest = Path(dest)
    dest_resolved = dest.resolve()

    with tarfile.open(tar_path, "r:gz") as tar:
        for member in tar.getmembers():
            target = (dest / member.name).resolve()
            if not str(target).startswith(str(dest_resolved)):
                raise RuntimeError(f"Unsafe tar path blocked: {member.name}")
        tar.extractall(dest)


def find_package_root(extract_dir):
    extract_dir = Path(extract_dir)

    if (extract_dir / "install.sh").exists():
        return extract_dir

    for p in extract_dir.iterdir():
        if p.is_dir() and (p / "install.sh").exists():
            return p

    raise RuntimeError("Downloaded package does not contain install.sh")


def run(cmd, cwd=None, extra_env=None):
    print("[edgeswarm-updater] running:", " ".join(cmd))
    env = os.environ.copy()
    if extra_env:
        env.update({k: str(v) for k, v in extra_env.items() if v is not None})
    subprocess.run(cmd, cwd=cwd, check=True, env=env)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--install-dir", default=os.environ.get("EDGESWARM_INSTALL_DIR", DEFAULT_INSTALL_DIR))
    parser.add_argument("--api-base", default=os.environ.get("EDGESWARM_API_BASE_URL", DEFAULT_API_BASE))
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    args = parser.parse_args()

    load_env_file(args.env_file)

    current_version = read_current_version(args.install_dir)
    query = urllib.parse.urlencode({
        "platform": "linux",
        "version": current_version,
        "arch": current_arch(),
        "t": int(time.time()),
    })

    manifest_url = f"{args.api_base.rstrip('/')}/v1/node/update-manifest?{query}"
    print(f"[edgeswarm-updater] checking manifest: {manifest_url}")

    manifest = fetch_json(manifest_url)

    latest_version = str(manifest.get("latestVersion") or manifest.get("version") or "").strip().lstrip("v")
    update_available = bool(manifest.get("updateAvailable"))
    download_url = str(manifest.get("downloadUrl") or "").strip()
    expected_sha = str(manifest.get("sha256") or "").strip().lower()

    print(json.dumps({
        "currentVersion": current_version,
        "latestVersion": latest_version,
        "updateAvailable": update_available,
        "downloadUrlPresent": bool(download_url),
        "sha256Present": bool(expected_sha),
        "publicReleaseSafe": manifest.get("publicReleaseSafe"),
        "releaseChannel": manifest.get("releaseChannel"),
    }, indent=2))

    if not update_available:
        print("[edgeswarm-updater] no update available")
        return 0

    if not download_url or not expected_sha:
        raise RuntimeError("Manifest says update is available but downloadUrl/sha256 is missing.")

    if manifest.get("publicReleaseSafe") is not True:
        raise RuntimeError("Refusing update because manifest publicReleaseSafe is not true.")

    if args.check_only:
        print("[edgeswarm-updater] check-only mode; update available but not installing")
        return 0

    with tempfile.TemporaryDirectory(prefix="edgeswarm-update-") as tmp:
        tmp_dir = Path(tmp)
        tar_path = tmp_dir / "edgeswarm-node-update.tar.gz"
        extract_dir = tmp_dir / "extract"

        print(f"[edgeswarm-updater] downloading: {download_url}")
        download_file(download_url, tar_path)

        actual_sha = sha256_file(tar_path)
        print(f"[edgeswarm-updater] expected sha256: {expected_sha}")
        print(f"[edgeswarm-updater] actual sha256:   {actual_sha}")

        if actual_sha != expected_sha:
            raise RuntimeError("SHA256 mismatch. Update blocked.")

        extract_dir.mkdir(parents=True, exist_ok=True)
        safe_extract_tar_gz(tar_path, extract_dir)

        package_root = find_package_root(extract_dir)
        run(
            ["bash", str(package_root / "install.sh"), "--auto-update"],
            cwd=str(package_root),
            extra_env={
                "EDGESWARM_INSTALL_PACKAGE_SHA256": expected_sha,
                "EDGESWARM_INSTALL_DOWNLOAD_URL": download_url,
            },
        )

    print("[edgeswarm-updater] update installed successfully")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[edgeswarm-updater] ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
