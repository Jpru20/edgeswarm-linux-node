#!/usr/bin/env python3
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from eth_account import Account

AUTH_PATH = Path("/etc/edgeswarm-node-auth.json")
WALLET_KEY_PATH = Path(os.getenv("EDGESWARM_WALLET_KEY_FILE", "/etc/edgeswarm-node-wallet.key"))
STATUS_DIR = Path("/var/lib/edgeswarm-node")
STATUS_PATH = STATUS_DIR / "ui_status.json"


def fail(message: str, code: int = 1):
    print(message, file=sys.stderr)
    raise SystemExit(code)


def validate_wallet_identity(
    private_key: str,
    wallet_address: str = "",
    expected_wallet: str = "",
):
    private_key = str(private_key or "").strip()
    wallet_address = str(wallet_address or "").strip()
    expected_wallet = str(expected_wallet or "").strip()

    if not private_key:
        fail("Invalid auth session: recovered wallet private key missing.")

    try:
        account = Account.from_key(private_key)
    except Exception as exc:
        fail(
            "Invalid auth session: recovered wallet private key "
            f"is invalid: {exc}"
        )

    derived_wallet = account.address

    if wallet_address and wallet_address.lower() != derived_wallet.lower():
        fail(
            "Invalid auth session: wallet address does not match "
            "the recovered private key."
        )

    if expected_wallet and expected_wallet.lower() != derived_wallet.lower():
        fail(
            "Wallet recovery mismatch: recovered wallet does not "
            "match the existing provider wallet."
        )

    return derived_wallet, account.key.hex()



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

    existing_auth = {}

    if AUTH_PATH.exists():
        try:
            existing_auth = json.loads(
                AUTH_PATH.read_text(encoding="utf-8")
            )
        except Exception:
            existing_auth = {}

    provider = str(data.get("providerEmail") or "").strip().lower()
    access = str(data.get("accessToken") or "").strip()
    refresh = str(data.get("refreshToken") or "").strip()

    if not provider:
        fail("Invalid auth session: providerEmail missing.")

    if not access:
        fail("Invalid auth session: accessToken missing.")

    if not refresh:
        fail("Invalid auth session: refreshToken missing.")

    existing_provider = str(
        existing_auth.get("providerEmail") or ""
    ).strip().lower()

    existing_wallet = str(
        existing_auth.get("walletAddress")
        or existing_auth.get("wallet_address")
        or existing_auth.get("worker")
        or ""
    ).strip()

    if existing_provider == provider:
        wallet_fields = (
            "walletAddress",
            "wallet_address",
            "worker",
            "nodeWalletPrivateKey",
            "walletPrivateKey",
            "privateKey",
            "nodeWalletCreatedAt",
            "walletRecoveredAt",
        )

        for key in wallet_fields:
            if not data.get(key) and existing_auth.get(key):
                data[key] = existing_auth[key]

    wallet_address = str(
        data.get("walletAddress")
        or data.get("wallet_address")
        or data.get("worker")
        or ""
    ).strip()

    private_key = str(
        data.get("nodeWalletPrivateKey")
        or data.get("walletPrivateKey")
        or data.get("privateKey")
        or ""
    ).strip()

    expected_wallet = (
        existing_wallet
        if existing_provider == provider
        else ""
    )

    wallet_address, private_key = validate_wallet_identity(
        private_key,
        wallet_address,
        expected_wallet,
    )

    wallet_tmp = Path(str(WALLET_KEY_PATH) + ".tmp")
    wallet_tmp.write_text(private_key.strip() + "\n", encoding="utf-8")
    shutil.chown(wallet_tmp, user="root", group="root")
    os.chmod(wallet_tmp, 0o600)
    wallet_tmp.replace(WALLET_KEY_PATH)

    for secret_key in (
        "nodeWalletPrivateKey",
        "walletPrivateKey",
        "privateKey",
    ):
        data.pop(secret_key, None)

    data["walletAddress"] = wallet_address
    data["worker"] = wallet_address
    data["nodeWalletPrivateKey"] = private_key

    data["authFileVersion"] = "edgeswarm_linux_auth_v2"
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
