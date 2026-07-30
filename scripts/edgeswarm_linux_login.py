#!/usr/bin/env python3
import getpass
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from supabase import create_client

SCRIPT_DIR = Path(__file__).resolve().parent
INSTALL_ROOT = SCRIPT_DIR.parent
if str(INSTALL_ROOT) not in sys.path:
    sys.path.insert(0, str(INSTALL_ROOT))

from edgeswarm_wallet_vault import recover_wallet_identity

DEFAULT_AUTH_FILE = "/etc/edgeswarm-node-auth.json"
STATUS_DIR = Path("/var/lib/edgeswarm-node")
STATUS_PATH = STATUS_DIR / "ui_status.json"

SUPABASE_URL = os.getenv("SUPABASE_URL") or os.getenv("EDGESWARM_SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY") or os.getenv("EDGESWARM_SUPABASE_ANON_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("Missing SUPABASE_URL / SUPABASE_ANON_KEY. Add them to /etc/edgeswarm-node.env first.", file=sys.stderr)
    raise SystemExit(1)


def obj_get(obj, key, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def write_ui_status(provider_email: str, mfa_verified: bool = True) -> None:
    STATUS_DIR.mkdir(parents=True, exist_ok=True)

    status = {}

    if STATUS_PATH.exists():
        try:
            status = json.loads(
                STATUS_PATH.read_text(encoding="utf-8")
            )
        except Exception:
            status = {}

    status.update({
        "providerEmail": provider_email,
        "authInstalled": True,
        "mfaVerified": bool(mfa_verified),
        "lastAuthInstall": int(time.time()),
    })

    temp_status = STATUS_PATH.with_suffix(".json.tmp")
    temp_status.write_text(
        json.dumps(status, indent=2) + "\n",
        encoding="utf-8",
    )

    shutil.chown(
        temp_status,
        user="edgeswarm",
        group="edgeswarm",
    )
    os.chmod(temp_status, 0o644)
    temp_status.replace(STATUS_PATH)


def main():
    auth_file = Path(os.getenv("EDGESWARM_AUTH_FILE", DEFAULT_AUTH_FILE))

    email = input("EdgeSwarm email: ").strip().lower()
    password = getpass.getpass("Password: ")

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    print("Signing in...")
    auth_response = supabase.auth.sign_in_with_password({
        "email": email,
        "password": password
    })

    user = obj_get(auth_response, "user")
    session = obj_get(auth_response, "session")

    if not user:
        print("Login failed: no user returned.", file=sys.stderr)
        raise SystemExit(1)

    factors = supabase.auth.mfa.list_factors()
    totp_group = obj_get(factors, "totp", []) or []
    verified_totp = [
        factor for factor in totp_group
        if obj_get(factor, "status") == "verified"
    ]

    if not verified_totp:
        supabase.auth.sign_out()
        print("No verified TOTP factor found. Set up 2FA in the EdgeSwarm web console first.", file=sys.stderr)
        raise SystemExit(1)

    factor_id = obj_get(verified_totp[0], "id")

    code = input("2FA code: ").strip()

    challenge = supabase.auth.mfa.challenge({
        "factor_id": factor_id
    })

    challenge_id = obj_get(challenge, "id")

    verify_res = supabase.auth.mfa.verify({
        "factor_id": factor_id,
        "challenge_id": challenge_id,
        "code": code
    })

    verified_session = obj_get(verify_res, "session") or supabase.auth.get_session()

    access_token = obj_get(verified_session, "access_token")
    refresh_token = obj_get(verified_session, "refresh_token")
    expires_at = obj_get(verified_session, "expires_at")

    if not access_token or not refresh_token:
        print("2FA succeeded but session tokens were missing.", file=sys.stderr)
        raise SystemExit(1)

    existing_wallet = ""

    if auth_file.exists():
        try:
            existing_auth = json.loads(
                auth_file.read_text(encoding="utf-8")
            )
        except Exception:
            existing_auth = {}

        existing_provider = str(
            existing_auth.get("providerEmail") or ""
        ).strip().lower()

        if existing_provider == email:
            existing_wallet = str(
                existing_auth.get("walletAddress")
                or existing_auth.get("wallet_address")
                or existing_auth.get("worker")
                or ""
            ).strip()

    try:
        wallet_identity = recover_wallet_identity(
            supabase,
            email,
            access_token,
            password,
            existing_wallet,
        )
    except Exception as exc:
        print(
            f"Wallet recovery failed: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    data = {
        "authFileVersion": "edgeswarm_linux_auth_v1",
        "providerEmail": email,
        "accessToken": access_token,
        "refreshToken": refresh_token,
        "expiresAt": expires_at,
        "mfaVerified": True,
        "walletAddress": wallet_identity["walletAddress"],
        "worker": wallet_identity["walletAddress"],
        "nodeWalletPrivateKey": wallet_identity["privateKey"],
        "walletRecoveredAt": int(time.time())
    }

    if os.geteuid() != 0:
        print(
            "Headless login must be run with sudo so the node service "
            "can securely access the auth session.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    auth_file.parent.mkdir(parents=True, exist_ok=True)

    temp_auth = auth_file.with_suffix(".json.tmp")
    temp_auth.write_text(json.dumps(data, indent=2) + "\n")

    shutil.chown(
        temp_auth,
        user="root",
        group="edgeswarm",
    )
    os.chmod(temp_auth, 0o660)
    temp_auth.replace(auth_file)

    write_ui_status(
        provider_email=email,
        mfa_verified=True,
    )

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

        print(
            "Authentication succeeded, but the node service "
            f"could not be enabled and started: {error_text}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    print(f"Login complete. Auth saved to {auth_file}")
    print(f"Provider email: {email}")
    print("Node service enabled and started.")


if __name__ == "__main__":
    main()
