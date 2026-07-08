#!/usr/bin/env python3
import getpass
import json
import os
import sys
from pathlib import Path

from supabase import create_client

DEFAULT_AUTH_FILE = "/etc/edgeswarm-node-auth.json"

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

    data = {
        "authFileVersion": "edgeswarm_linux_auth_v1",
        "providerEmail": email,
        "accessToken": access_token,
        "refreshToken": refresh_token,
        "expiresAt": expires_at,
        "mfaVerified": True
    }

    auth_file.parent.mkdir(parents=True, exist_ok=True)
    auth_file.write_text(json.dumps(data, indent=2) + "\n")
    os.chmod(auth_file, 0o600)

    print(f"Login complete. Auth saved to {auth_file}")
    print(f"Provider email: {email}")


if __name__ == "__main__":
    main()
