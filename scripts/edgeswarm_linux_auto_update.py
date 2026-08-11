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


def normalize_arch(value):
    machine = str(value or "").strip().lower()

    if machine in ("x86_64", "amd64", "x64"):
        return "x64"

    if machine in ("aarch64", "arm64"):
        return "arm64"

    return machine or "unknown"


def current_arch():
    return normalize_arch(platform.machine())



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
    install_dir = Path(install_dir)

    env_version = os.environ.get(
        "EDGESWARM_NODE_VERSION",
        "",
    ).strip().lstrip("v")

    version_file_value = ""
    version_file = install_dir / "VERSION"

    if version_file.exists():
        version_file_value = (
            version_file.read_text(errors="ignore")
            .strip()
            .lstrip("v")
        )

    runtime_version = ""
    runtime_file = install_dir / "edgeswarm_node.py"

    if runtime_file.exists():
        for line in runtime_file.read_text(
            errors="ignore"
        ).splitlines():
            stripped = line.strip()

            if (
                stripped.startswith("APP_VERSION")
                and "=" in stripped
            ):
                runtime_version = (
                    stripped.split("=", 1)[1]
                    .strip()
                    .strip("'\"")
                    .lstrip("v")
                )
                break

    if (
        version_file_value
        and runtime_version
        and version_file_value != runtime_version
    ):
        raise RuntimeError(
            "Installed version mismatch: "
            f"VERSION={version_file_value}, "
            f"APP_VERSION={runtime_version}"
        )

    installed_version = (
        runtime_version
        or version_file_value
        or env_version
        or "0.0.0"
    )

    if (
        env_version
        and installed_version != env_version
    ):
        print(
            "[edgeswarm-updater] ignoring stale "
            "EDGESWARM_NODE_VERSION="
            f"{env_version}; installed files report "
            f"{installed_version}"
        )

    return installed_version


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


ROLLBACK_INTEGRATION_PATHS = (
    "/etc/edgeswarm-node.env",
    "/usr/lib/edgeswarm-node-package",
    "/etc/systemd/system/edgeswarm-node.service",
    "/etc/systemd/system/edgeswarm-node-updater.service",
    "/etc/systemd/system/edgeswarm-node-updater.timer",
    "/etc/systemd/system/edgeswarm-node-model-provisioner.service",
    "/etc/systemd/system/edgeswarm-node-model-provisioner.timer",
    "/usr/local/bin/edgeswarm",
    "/usr/share/applications/edgeswarm-node.desktop",
)


def _systemctl_state_v1(action, unit):
    try:
        return subprocess.run(
            ["systemctl", action, unit],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode == 0
    except Exception:
        return False


def create_update_rollback_snapshot_v1(install_dir):
    root = Path(tempfile.mkdtemp(prefix="edgeswarm-rollback-"))
    install = Path(install_dir)
    backup_install = root / "install"

    if install.is_dir():
        shutil.copytree(install, backup_install, symlinks=True)

    files_root = root / "files"
    for value in ROLLBACK_INTEGRATION_PATHS:
        src = Path(value)
        if not src.exists() and not src.is_symlink():
            continue
        dst = files_root / value.lstrip("/")
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_symlink():
            dst.symlink_to(os.readlink(src))
        elif src.is_dir():
            shutil.copytree(src, dst, symlinks=True)
        elif src.is_file():
            shutil.copy2(src, dst)

    return {
        "armed": True,
        "root": str(root),
        "installDir": str(install),
        "serviceActive": _systemctl_state_v1("is-active", "edgeswarm-node.service"),
        "serviceEnabled": _systemctl_state_v1("is-enabled", "edgeswarm-node.service"),
    }


def restore_update_rollback_snapshot_v1(state):
    if not isinstance(state, dict) or not state.get("armed"):
        return

    state["armed"] = False
    root = Path(state["root"])
    install = Path(state["installDir"])
    backup_install = root / "install"

    subprocess.run(["systemctl", "stop", "edgeswarm-node.service"], check=False)

    if install.exists():
        shutil.rmtree(install)
    if backup_install.is_dir():
        shutil.copytree(backup_install, install, symlinks=True)

    files_root = root / "files"
    for value in ROLLBACK_INTEGRATION_PATHS:
        dst = Path(value)
        backup = files_root / value.lstrip("/")
        if dst.exists() or dst.is_symlink():
            if dst.is_dir() and not dst.is_symlink():
                shutil.rmtree(dst)
            else:
                dst.unlink()
        if backup.is_symlink():
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.symlink_to(os.readlink(backup))
        elif backup.is_dir():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(backup, dst, symlinks=True)
        elif backup.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, dst)

    subprocess.run(["systemctl", "daemon-reload"], check=False)
    if state.get("serviceEnabled"):
        subprocess.run(["systemctl", "enable", "edgeswarm-node.service"], check=False)
    else:
        subprocess.run(["systemctl", "disable", "edgeswarm-node.service"], check=False)
    if state.get("serviceActive"):
        subprocess.run(["systemctl", "restart", "edgeswarm-node.service"], check=False)
    else:
        subprocess.run(["systemctl", "stop", "edgeswarm-node.service"], check=False)

    print("[edgeswarm-updater] previous runnable application restored")


def discard_update_rollback_snapshot_v1(state):
    if not isinstance(state, dict):
        return
    state["armed"] = False
    root_value = str(state.get("root") or "").strip()
    if not root_value:
        return
    root = Path(root_value).resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    if root.parent != temp_root or not root.name.startswith("edgeswarm-rollback-"):
        raise RuntimeError("unsafe_rollback_snapshot_path")
    if root.is_dir():
        shutil.rmtree(root)

_ACTIVE_UPDATE_ROLLBACK_STATE_V1 = None


def arm_update_rollback_v1(install_dir):
    global _ACTIVE_UPDATE_ROLLBACK_STATE_V1
    if _ACTIVE_UPDATE_ROLLBACK_STATE_V1 is not None:
        raise RuntimeError("update_rollback_already_armed")
    _ACTIVE_UPDATE_ROLLBACK_STATE_V1 = create_update_rollback_snapshot_v1(install_dir)
    print("[edgeswarm-updater] rollback snapshot armed")


def validate_post_update_service_v1():
    state = _ACTIVE_UPDATE_ROLLBACK_STATE_V1
    if not isinstance(state, dict) or not state.get("armed"):
        raise RuntimeError("update_rollback_not_armed")

    unit = Path("/etc/systemd/system/edgeswarm-node.service")
    if not unit.is_file():
        raise RuntimeError("Post-install service unit is missing.")

    if state.get("serviceActive"):
        if not _systemctl_state_v1("is-active", "edgeswarm-node.service"):
            raise RuntimeError("Post-install node service is not active.")


def commit_update_rollback_v1():
    global _ACTIVE_UPDATE_ROLLBACK_STATE_V1
    state = _ACTIVE_UPDATE_ROLLBACK_STATE_V1
    if state is None:
        return
    discard_update_rollback_snapshot_v1(state)
    _ACTIVE_UPDATE_ROLLBACK_STATE_V1 = None
    print("[edgeswarm-updater] rollback snapshot committed")


def rollback_active_update_v1():
    global _ACTIVE_UPDATE_ROLLBACK_STATE_V1
    state = _ACTIVE_UPDATE_ROLLBACK_STATE_V1
    if not isinstance(state, dict) or not state.get("armed"):
        return False

    try:
        restore_update_rollback_snapshot_v1(state)
        discard_update_rollback_snapshot_v1(state)
        _ACTIVE_UPDATE_ROLLBACK_STATE_V1 = None
        print("[edgeswarm-updater] automatic rollback completed", file=sys.stderr)
        return True
    except Exception as rollback_exc:
        print(
            f"[edgeswarm-updater] ROLLBACK ERROR: {rollback_exc}",
            file=sys.stderr,
        )
        print(
            f"[edgeswarm-updater] rollback snapshot retained at: {state.get('root')}",
            file=sys.stderr,
        )
        return False


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

    manifest_arch = normalize_arch(
        manifest.get("architecture")
        or manifest.get("arch")
    )

    installed_arch = current_arch()

    if manifest_arch != installed_arch:
        raise RuntimeError(
            "Update architecture mismatch: "
            f"installed={installed_arch}, "
            f"manifest={manifest_arch}"
        )

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

    arm_update_rollback_v1(args.install_dir)

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

    installed_version = read_current_version(
        args.install_dir
    )

    if installed_version != latest_version:
        raise RuntimeError(
            "Post-install version verification failed: "
            f"installed={installed_version}, "
            f"expected={latest_version}"
        )

    installed_package_type_after = (
        read_installed_package_type(
            args.install_dir
        )
    )

    if (
        installed_package_type_after
        != installed_package_type
    ):
        raise RuntimeError(
            "Post-install package type changed: "
            f"before={installed_package_type}, "
            f"after={installed_package_type_after}"
        )

    expected_runtime_sha = str(
        manifest.get("runtimeSha256")
        or ""
    ).strip().lower()

    installed_runtime_path = (
        Path(args.install_dir)
        / "edgeswarm_node.py"
    )

    actual_runtime_sha = ""

    if expected_runtime_sha:
        if not installed_runtime_path.is_file():
            raise RuntimeError(
                "Installed runtime file is missing."
            )

        actual_runtime_sha = sha256_file(
            installed_runtime_path
        )

        if actual_runtime_sha != expected_runtime_sha:
            raise RuntimeError(
                "Post-install runtime SHA256 mismatch: "
                f"expected={expected_runtime_sha}, "
                f"actual={actual_runtime_sha}"
            )

    post_query = urllib.parse.urlencode({
        "platform": "linux",
        "version": installed_version,
        "arch": current_arch(),
        "packageType":
            installed_package_type_after,
        "t": int(time.time()),
    })

    post_manifest_url = (
        f"{args.api_base.rstrip('/')}"
        f"/v1/node/update-manifest?"
        f"{post_query}"
    )

    post_manifest = fetch_json(
        post_manifest_url
    )

    post_latest_version = str(
        post_manifest.get("latestVersion")
        or post_manifest.get("version")
        or ""
    ).strip().lstrip("v")

    if post_latest_version != installed_version:
        raise RuntimeError(
            "Post-install manifest version mismatch: "
            f"installed={installed_version}, "
            f"manifest={post_latest_version}"
        )

    if post_manifest.get("updateAvailable") is True:
        raise RuntimeError(
            "Post-install manifest still reports an "
            "available update. Update loop blocked."
        )

    validate_post_update_service_v1()
    commit_update_rollback_v1()

    print(json.dumps({
        "postInstallVersion":
            installed_version,
        "postInstallArchitecture":
            current_arch(),
        "postInstallPackageType":
            installed_package_type_after,
        "postInstallRuntimeSha256":
            actual_runtime_sha or None,
        "postInstallUpdateAvailable":
            post_manifest.get("updateAvailable"),
        "postInstallVerification":
            "passed",
    }, indent=2))

    print(
        "[edgeswarm-updater] update installed "
        "and verified successfully"
    )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[edgeswarm-updater] ERROR: {exc}", file=sys.stderr)
        rollback_active_update_v1()
        raise SystemExit(1)
