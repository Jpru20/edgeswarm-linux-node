#!/usr/bin/env python3
import json
import os
import tempfile
import time
from pathlib import Path

import requests

from edgeswarm_ui_common import load_env_file, pkexec

AUTH_INSTALLER = Path("/opt/edgeswarm-node/scripts/edgeswarm_linux_install_auth.py")


class EdgeSwarmAuthError(Exception):
    pass


def _supabase_config():
    env = load_env_file()

    url = (
        env.get("SUPABASE_URL")
        or os.getenv("SUPABASE_URL")
        or ""
    ).strip()

    anon_key = (
        env.get("SUPABASE_ANON_KEY")
        or env.get("SUPABASE_KEY")
        or os.getenv("SUPABASE_ANON_KEY")
        or os.getenv("SUPABASE_KEY")
        or ""
    ).strip()

    if not url:
        raise EdgeSwarmAuthError("SUPABASE_URL missing from /etc/edgeswarm-node.env")

    if not anon_key:
        raise EdgeSwarmAuthError("SUPABASE_ANON_KEY missing from /etc/edgeswarm-node.env")

    return url.rstrip("/"), anon_key


def password_login(email: str, password: str) -> dict:
    url, anon_key = _supabase_config()

    res = requests.post(
        f"{url}/auth/v1/token?grant_type=password",
        headers={
            "apikey": anon_key,
            "Content-Type": "application/json",
        },
        json={
            "email": email,
            "password": password,
        },
        timeout=25,
    )

    if res.status_code >= 300:
        raise EdgeSwarmAuthError(f"Password login failed: {res.text[:300]}")

    data = res.json()

    if not data.get("access_token"):
        raise EdgeSwarmAuthError("Password login did not return access_token.")

    if not data.get("refresh_token"):
        raise EdgeSwarmAuthError("Password login did not return refresh_token.")

    return data


def _auth_headers(access_token: str) -> dict:
    _, anon_key = _supabase_config()

    return {
        "apikey": anon_key,
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }


def list_verified_totp_factors(access_token: str) -> list:
    url, _ = _supabase_config()

    res = requests.get(
        f"{url}/auth/v1/factors",
        headers=_auth_headers(access_token),
        timeout=25,
    )

    if res.status_code >= 300:
        raise EdgeSwarmAuthError(f"Could not load 2FA factors: {res.text[:300]}")

    data = res.json()

    candidates = []

    if isinstance(data, dict):
        if isinstance(data.get("totp"), list):
            candidates.extend(data.get("totp"))

        if isinstance(data.get("factors"), list):
            candidates.extend(data.get("factors"))

    elif isinstance(data, list):
        candidates.extend(data)

    verified = []

    for factor in candidates:
        factor_type = str(factor.get("factor_type") or factor.get("type") or "totp").lower()
        status = str(factor.get("status") or "").lower()

        if factor_type != "totp":
            continue

        if status in ("verified", "enabled"):
            verified.append(factor)

    return verified


def verify_totp(access_token: str, code: str) -> dict:
    url, _ = _supabase_config()

    factors = list_verified_totp_factors(access_token)

    if not factors:
        raise EdgeSwarmAuthError("No verified 2FA/TOTP factor found for this account.")

    factor_id = factors[0].get("id")

    if not factor_id:
        raise EdgeSwarmAuthError("2FA factor ID missing.")

    challenge_res = requests.post(
        f"{url}/auth/v1/factors/{factor_id}/challenge",
        headers=_auth_headers(access_token),
        json={},
        timeout=25,
    )

    if challenge_res.status_code >= 300:
        raise EdgeSwarmAuthError(f"2FA challenge failed: {challenge_res.text[:300]}")

    challenge = challenge_res.json()
    challenge_id = challenge.get("id") or challenge.get("challenge_id")

    if not challenge_id:
        raise EdgeSwarmAuthError("2FA challenge ID missing.")

    verify_res = requests.post(
        f"{url}/auth/v1/factors/{factor_id}/verify",
        headers=_auth_headers(access_token),
        json={
            "challenge_id": challenge_id,
            "code": code,
        },
        timeout=25,
    )

    if verify_res.status_code >= 300:
        raise EdgeSwarmAuthError(f"2FA verification failed: {verify_res.text[:300]}")

    data = verify_res.json()

    if not isinstance(data, dict):
        raise EdgeSwarmAuthError("2FA verification returned invalid response.")

    return data


def login_with_password_and_2fa(email: str, password: str, code: str) -> dict:
    email = str(email or "").strip().lower()
    password = str(password or "").strip()
    code = str(code or "").strip().replace(" ", "")

    if not email:
        raise EdgeSwarmAuthError("Email is required.")

    if not password:
        raise EdgeSwarmAuthError("Password is required.")

    if not code:
        raise EdgeSwarmAuthError("2FA code is required.")

    session = password_login(email, password)

    access_token = session.get("access_token")
    refresh_token = session.get("refresh_token")
    expires_in = int(session.get("expires_in") or 3600)

    verified = verify_totp(access_token, code)

    final_access = verified.get("access_token") or access_token
    final_refresh = verified.get("refresh_token") or refresh_token
    final_expires_in = int(verified.get("expires_in") or expires_in or 3600)

    return {
        "authFileVersion": "edgeswarm_linux_auth_v1",
        "providerEmail": email,
        "accessToken": final_access,
        "refreshToken": final_refresh,
        "expiresAt": int(time.time()) + final_expires_in,
        "mfaVerified": True,
    }


def install_auth_session(auth_session: dict) -> dict:
    if not AUTH_INSTALLER.exists():
        raise EdgeSwarmAuthError(f"Auth installer missing: {AUTH_INSTALLER}")

    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(
            "w",
            delete=False,
            prefix="edgeswarm-auth-",
            suffix=".json",
        ) as f:
            json.dump(auth_session, f)
            temp_path = f.name

        os.chmod(temp_path, 0o600)

        code, out, err = pkexec([str(AUTH_INSTALLER), temp_path], timeout=90)

        if code != 0:
            raise EdgeSwarmAuthError(err or out or "Auth installer failed.")

        try:
            return json.loads(out)
        except Exception:
            return {
                "ok": True,
                "raw": out,
            }

    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except Exception:
                pass


def login_install_and_restart(email: str, password: str, code: str) -> dict:
    auth_session = login_with_password_and_2fa(email, password, code)
    install_result = install_auth_session(auth_session)

    return {
        "ok": True,
        "providerEmail": auth_session["providerEmail"],
        "install": install_result,
    }
