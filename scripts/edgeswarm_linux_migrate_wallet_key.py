#!/usr/bin/env python3
import json
import os
import stat
import sys
from pathlib import Path

from eth_account import Account


def read_key(path):
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def wallet_from_key(value):
    value = str(value or "").strip()
    if not value:
        return None, None
    account = Account.from_key(value)
    return account.address, account.key.hex()


def write_atomic(path, text, mode, preserve_stat=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(str(path) + ".tmp")
    temp.write_text(text, encoding="utf-8")
    os.chmod(temp, mode)
    if preserve_stat is not None and os.geteuid() == 0:
        os.chown(temp, preserve_stat.st_uid, preserve_stat.st_gid)
    elif os.geteuid() == 0:
        os.chown(temp, 0, 0)
    temp.replace(path)


def main():
    auth_path = Path(sys.argv[1] if len(sys.argv) > 1 else "/etc/edgeswarm-node-auth.json")
    key_path = Path(sys.argv[2] if len(sys.argv) > 2 else "/etc/edgeswarm-node-wallet.key")

    if not auth_path.exists() or auth_path.stat().st_size == 0:
        print(json.dumps({"ok": True, "migrated": False, "reason": "no_existing_auth"}))
        return 0

    auth_stat = auth_path.stat()
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    if not isinstance(auth, dict):
        raise RuntimeError("existing_auth_not_object")

    legacy_key = str(
        auth.get("nodeWalletPrivateKey")
        or auth.get("walletPrivateKey")
        or auth.get("privateKey")
        or ""
    ).strip()
    credential_key = read_key(key_path)

    legacy_wallet, normalized_legacy = wallet_from_key(legacy_key)
    credential_wallet, normalized_credential = wallet_from_key(credential_key)

    if legacy_wallet and credential_wallet and legacy_wallet.lower() != credential_wallet.lower():
        raise RuntimeError("legacy_and_credential_wallet_mismatch")

    effective_wallet = credential_wallet or legacy_wallet
    effective_key = normalized_credential or normalized_legacy

    expected_wallet = str(
        auth.get("walletAddress")
        or auth.get("wallet_address")
        or auth.get("worker")
        or ""
    ).strip()

    if not effective_key:
        print(json.dumps({"ok": True, "migrated": False, "reason": "wallet_key_not_present"}))
        return 0

    if expected_wallet and expected_wallet.lower() != effective_wallet.lower():
        raise RuntimeError("existing_wallet_address_mismatch")

    write_atomic(key_path, effective_key + "\n", 0o600)

    for field in ("nodeWalletPrivateKey", "walletPrivateKey", "privateKey"):
        auth.pop(field, None)

    auth["authFileVersion"] = "edgeswarm_linux_auth_v2"
    auth["walletAddress"] = effective_wallet
    auth["worker"] = effective_wallet

    original_mode = stat.S_IMODE(auth_stat.st_mode)
    write_atomic(
        auth_path,
        json.dumps(auth, indent=2) + "\n",
        original_mode or 0o660,
        preserve_stat=auth_stat,
    )

    print(json.dumps({"ok": True, "migrated": True, "walletPreserved": True}))
    return 0


raise SystemExit(main())
