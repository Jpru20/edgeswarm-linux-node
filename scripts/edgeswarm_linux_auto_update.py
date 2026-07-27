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



def normalize_package_type(value):
    package_type = str(value or "").strip().lower()

    aliases = {
        "debian": "deb",
        "application/x-debian-package": "deb",
        "rpm": "rpm",
        "application/x-rpm": "rpm",
        "application/x-redhat-package-manager": "rpm",
        "tgz": "tar.gz",
        "source": "tar.gz",
        "tar": "tar.gz",
    }

    return aliases.get(package_type, package_type)


def read_installed_package_type(install_dir):
    install_dir = Path(install_dir)

    for filename in (
        "PACKAGE_MANIFEST.json",
        "RELEASE_METADATA.json",
    ):
        path = install_dir / filename

        if not path.exists():
            continue

        try:
            data = json.loads(
                path.read_text(errors="ignore")
            )
        except Exception:
            continue

        package_type = normalize_package_type(
            data.get("packageType")
        )

        if package_type:
            return package_type

    return ""

def version_key(value):
    parts = []

    for piece in str(value or "").strip().lstrip("v").split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits or 0))

    while len(parts) < 3:
        parts.append(0)

    return tuple(parts[:3])


def read_current_version(install_dir):
    candidates = []

    env_version = os.environ.get(
        "EDGESWARM_NODE_VERSION",
        "",
    ).strip().lstrip("v")

    if env_version:
        candidates.append(env_version)

    version_file = Path(install_dir) / "VERSION"

    if version_file.exists():
        version = (
            version_file.read_text(errors="ignore")
            .strip()
            .lstrip("v")
        )

        if version:
            candidates.append(version)

    runtime_file = Path(install_dir) / "edgeswarm_node.py"

    if runtime_file.exists():
        for line in runtime_file.read_text(
            errors="ignore"
        ).splitlines():
            stripped = line.strip()

            if stripped.startswith("APP_VERSION") and "=" in stripped:
                runtime_version = (
                    stripped.split("=", 1)[1]
                    .strip()
                    .strip("'\"")
                    .lstrip("v")
                )

                if runtime_version:
                    candidates.append(runtime_version)

                break

    if not candidates:
        return "0.0.0"

    return max(candidates, key=version_key)


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

    current_version = read_current_version(
        args.install_dir
    )

    installed_package_type = (
        read_installed_package_type(
            args.install_dir
        )
    )

    if installed_package_type not in (
        "deb",
        "rpm",
        "tar.gz",
    ):
        raise RuntimeError(
            "Unable to determine supported installed "
            f"package type: {installed_package_type or 'missing'}"
        )

    query = urllib.parse.urlencode({
        "platform": "linux",
        "version": current_version,
        "arch": current_arch(),
        "packageType": installed_package_type,
        "t": int(time.time()),
    })

    manifest_url = f"{args.api_base.rstrip('/')}/v1/node/update-manifest?{query}"
    print(f"[edgeswarm-updater] checking manifest: {manifest_url}")

    manifest = fetch_json(manifest_url)

    latest_version = str(manifest.get("latestVersion") or manifest.get("version") or "").strip().lstrip("v")
    update_available = bool(manifest.get("updateAvailable"))
    download_url = str(manifest.get("downloadUrl") or "").strip()
    expected_sha = str(
        manifest.get("sha256")
        or manifest.get("packageSha256")
        or ""
    ).strip().lower()

    package_type = normalize_package_type(
        manifest.get("packageType")
    )

    if not package_type:
        download_path = urllib.parse.urlparse(
            download_url
        ).path.lower()

        if download_path.endswith(".deb"):
            package_type = "deb"
        elif download_path.endswith(".rpm"):
            package_type = "rpm"
        elif (
            download_path.endswith(".tar.gz")
            or download_path.endswith(".tgz")
        ):
            package_type = "tar.gz"
        else:
            package_type = "unknown"

    print(json.dumps({
        "currentVersion": current_version,
        "installedPackageType": installed_package_type,
        "latestVersion": latest_version,
        "updateAvailable": update_available,
        "downloadUrlPresent": bool(download_url),
        "sha256Present": bool(expected_sha),
        "packageType": package_type,
        "publicReleaseSafe": manifest.get("publicReleaseSafe"),
        "releaseChannel": manifest.get("releaseChannel"),
    }, indent=2))

    if (
        latest_version
        and version_key(latest_version) <= version_key(current_version)
    ):
        print(
            "[edgeswarm-updater] refusing equal-version "
            "install or downgrade"
        )
        return 0

    if not update_available:
        print("[edgeswarm-updater] no update available")
        return 0

    if not download_url or not expected_sha:
        raise RuntimeError("Manifest says update is available but downloadUrl/sha256 is missing.")

    if manifest.get("publicReleaseSafe") is not True:
        raise RuntimeError("Refusing update because manifest publicReleaseSafe is not true.")

    if package_type != installed_package_type:
        raise RuntimeError(
            "Update package type mismatch: "
            f"installed={installed_package_type}, "
            f"manifest={package_type}"
        )

    if package_type not in (
        "deb",
        "rpm",
        "tar.gz",
    ):
        raise RuntimeError(
            f"Unsupported update package type: {package_type}"
        )

    if args.check_only:
        print("[edgeswarm-updater] check-only mode; update available but not installing")
        return 0

    with tempfile.TemporaryDirectory(prefix="edgeswarm-update-") as tmp:
        tmp_dir = Path(tmp)

        if package_type == "deb":
            package_path = (
                tmp_dir / "edgeswarm-node-update.deb"
            )
        elif package_type == "rpm":
            package_path = (
                tmp_dir / "edgeswarm-node-update.rpm"
            )
        else:
            package_path = (
                tmp_dir / "edgeswarm-node-update.tar.gz"
            )

        print(f"[edgeswarm-updater] downloading: {download_url}")
        download_file(download_url, package_path)

        actual_sha = sha256_file(package_path)
        print(f"[edgeswarm-updater] expected sha256: {expected_sha}")
        print(f"[edgeswarm-updater] actual sha256:   {actual_sha}")

        if actual_sha != expected_sha:
            raise RuntimeError("SHA256 mismatch. Update blocked.")

        install_env = {
            "EDGESWARM_INSTALL_PACKAGE_SHA256": expected_sha,
            "EDGESWARM_INSTALL_DOWNLOAD_URL": download_url,
            "EDGESWARM_API_BASE_URL": args.api_base.rstrip("/"),
        }

        if package_type == "deb":
            install_env[
                "DEBIAN_FRONTEND"
            ] = "noninteractive"

            run(
                [
                    "apt-get",
                    "install",
                    "-y",
                    str(package_path),
                ],
                extra_env=install_env,
            )

        elif package_type == "rpm":
            if shutil.which("dnf"):
                command = [
                    "dnf",
                    "install",
                    "-y",
                    "--nogpgcheck",
                    str(package_path),
                ]
            elif shutil.which("yum"):
                command = [
                    "yum",
                    "localinstall",
                    "-y",
                    str(package_path),
                ]
            elif shutil.which("rpm"):
                command = [
                    "rpm",
                    "-Uvh",
                    "--replacepkgs",
                    str(package_path),
                ]
            else:
                raise RuntimeError(
                    "No RPM package manager is available."
                )

            run(
                command,
                extra_env=install_env,
            )

        else:
            extract_dir = tmp_dir / "extract"
            extract_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            safe_extract_tar_gz(
                package_path,
                extract_dir,
            )

            package_root = find_package_root(
                extract_dir
            )

            run(
                [
                    "bash",
                    str(
                        package_root
                        / "install.sh"
                    ),
                    "--auto-update",
                ],
                cwd=str(package_root),
                extra_env=install_env,
            )

    print("[edgeswarm-updater] update installed successfully")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[edgeswarm-updater] ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
