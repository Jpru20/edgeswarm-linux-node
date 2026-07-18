#!/usr/bin/env python3
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

AUTH_PATH = Path("/etc/edgeswarm-node-auth.json")
STATUS_DIR = Path("/var/lib/edgeswarm-node")
STATUS_PATH = STATUS_DIR / "ui_status.json"


def fail(message: str, code: int = 1):
    print(message, file=sys.stderr)
    raise SystemExit(code)


def main():
    if os.geteuid() != 0:
        fail("This helper must run as root via pkexec.")

    if len(sys.argv) != 2:
        fail("Usage: edgeswarm_linux_install_auth.py /path/to/session.json")

    src = Path(sys.argv[1])
    if not src.exists():
        fail(f"Session file not found: {src}")

    with src.open("r") as f:
        data = json.load(f)

    provider = str(data.get("providerEmail") or "").strip().lower()
    access = str(data.get("accessToken") or "").strip()
    refresh = str(data.get("refreshToken") or "").strip()

    if not provider:
        fail("Invalid auth session: providerEmail missing.")

    if not access:
        fail("Invalid auth session: accessToken missing.")

    if not refresh:
        fail("Invalid auth session: refreshToken missing.")

    data["authFileVersion"] = "edgeswarm_linux_auth_v1"
    data["providerEmail"] = provider
    data["mfaVerified"] = bool(data.get("mfaVerified", True))
    data["installedAt"] = int(time.time())

    tmp = AUTH_PATH.with_suffix(".json.tmp")

    with tmp.open("w") as f:
        json.dump(data, f, indent=2)

    shutil.chown(tmp, user="root", group="edgeswarm")
    os.chmod(tmp, 0o660)
    tmp.replace(AUTH_PATH)

    STATUS_DIR.mkdir(parents=True, exist_ok=True)

    status = {}
    if STATUS_PATH.exists():
        try:
            status = json.loads(STATUS_PATH.read_text())
        except Exception:
            status = {}

    status.update({
        "providerEmail": provider,
        "authInstalled": True,
        "mfaVerified": data["mfaVerified"],
        "lastAuthInstall": int(time.time())
    })

    with STATUS_PATH.open("w") as f:
        json.dump(status, f, indent=2)

    shutil.chown(STATUS_PATH, user="edgeswarm", group="edgeswarm")
    os.chmod(STATUS_PATH, 0o644)

    service_result = subprocess.run(
        [
            "systemctl",
            "enable",
            "--now",
            "edgeswarm-node.service",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    if service_result.returncode != 0:
        error_text = (
            service_result.stderr
            or service_result.stdout
            or "unknown systemctl error"
        ).strip()

        print(json.dumps({
            "ok": False,
            "error": "service_enable_start_failed",
            "detail": error_text,
        }))
        raise SystemExit(1)

    print(json.dumps({
        "ok": True,
        "providerEmail": provider,
        "authPath": str(AUTH_PATH),
        "statusPath": str(STATUS_PATH)
    }))


if __name__ == "__main__":
    main()
