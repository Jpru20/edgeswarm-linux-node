#!/usr/bin/env python3

import base64
import hashlib
import json
import os
import platform
from pathlib import Path
from typing import Dict, List

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import (
    PBKDF2HMAC,
)
from eth_account import Account


def derive_wallet_key(
    password: str,
    email: str,
) -> bytes:
    normalized_email = str(
        email or ""
    ).strip().lower()

    if not normalized_email:
        raise ValueError(
            "Provider email is required."
        )

    if not password:
        raise ValueError(
            "Password is required."
        )

    salt = (
        normalized_email
        .encode("utf-8")[:16]
        .ljust(16, b"\0")
    )

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )

    return kdf.derive(
        password.encode("utf-8")
    )


def decrypt_wallet_private_key(
    encrypted_payload: str,
    password: str,
    email: str,
) -> str:
    payload = str(
        encrypted_payload or ""
    ).strip()

    if not payload:
        raise ValueError(
            "Encrypted wallet payload is missing."
        )

    encrypted_data = base64.b64decode(
        payload,
        validate=True,
    )

    if len(encrypted_data) <= 28:
        raise ValueError(
            "Encrypted wallet payload is invalid."
        )

    nonce = encrypted_data[:12]
    ciphertext = encrypted_data[12:]

    plaintext = AESGCM(
        derive_wallet_key(
            password,
            email,
        )
    ).decrypt(
        nonce,
        ciphertext,
        None,
    )

    private_key = plaintext.decode(
        "utf-8"
    ).strip()

    if not private_key:
        raise ValueError(
            "Decrypted wallet key is empty."
        )

    return private_key


def validate_wallet_identity(
    private_key: str,
    expected_wallet: str = "",
) -> Dict[str, str]:
    account = Account.from_key(
        private_key
    )

    wallet_address = account.address
    normalized_private_key = account.key.hex()

    expected = str(
        expected_wallet or ""
    ).strip()

    if (
        expected
        and expected.lower()
        != wallet_address.lower()
    ):
        raise RuntimeError(
            "Recovered wallet does not match "
            "the existing Linux wallet identity."
        )

    return {
        "walletAddress": wallet_address,
        "privateKey": normalized_private_key,
    }


def encrypt_wallet_private_key(
    private_key: str,
    password: str,
    email: str,
) -> str:
    account = Account.from_key(private_key)
    normalized_private_key = account.key.hex()
    nonce = os.urandom(12)
    ciphertext = AESGCM(
        derive_wallet_key(password, email)
    ).encrypt(
        nonce,
        normalized_private_key.encode("utf-8"),
        None,
    )
    return base64.b64encode(
        nonce + ciphertext
    ).decode("ascii")


def _read_first_nonempty(paths: List[str]) -> str:
    for candidate in paths:
        try:
            value = Path(candidate).read_text(
                encoding="utf-8",
                errors="ignore",
            ).strip()
        except Exception:
            value = ""

        if value:
            return value

    return ""


def get_linux_wallet_hardware_id() -> str:
    machine_id = _read_first_nonempty([
        "/etc/machine-id",
        "/var/lib/dbus/machine-id",
    ])

    cpu_name = ""

    try:
        for line in Path("/proc/cpuinfo").read_text(
            encoding="utf-8",
            errors="ignore",
        ).splitlines():
            if "model name" in line.lower():
                cpu_name = line.split(":", 1)[-1].strip()
                break
    except Exception:
        pass

    if not cpu_name:
        cpu_name = platform.processor() or "Linux CPU"

    machine = (platform.machine() or "").lower()

    if machine in ("arm64", "aarch64"):
        architecture = "arm64"
    elif machine in ("x86_64", "amd64"):
        architecture = "x64"
    else:
        architecture = machine or "unknown"

    material = json.dumps(
        {
            "osType": "linux",
            "architecture": architecture,
            "rawStableLocalId": f"{machine_id}|{cpu_name}",
        },
        sort_keys=True,
    )

    return hashlib.sha256(
        material.encode("utf-8")
    ).hexdigest()


def _fetch_account_wallet_rows(
    supabase_client,
    provider_email: str,
    access_token: str,
) -> List[dict]:
    email = str(provider_email or "").strip().lower()
    token = str(access_token or "").strip()

    if not email:
        raise ValueError("Provider email is required.")

    if not token:
        raise ValueError(
            "Supabase access token is required."
        )

    supabase_client.postgrest.auth(token)

    response = (
        supabase_client
        .table("worker_wallets")
        .select("id,hardware_id,private_key")
        .eq("email", email)
        .execute()
    )

    return (
        list(response.data)
        if isinstance(response.data, list)
        else []
    )


def _find_exact_device_rows(
    rows: List[dict],
    hardware_id: str,
) -> List[dict]:
    expected = str(hardware_id or "").strip().lower()

    return [
        row
        for row in rows
        if isinstance(row, dict)
        and str(
            row.get("hardware_id") or ""
        ).strip().lower() == expected
    ]


def fetch_or_create_device_wallet(
    supabase_client,
    provider_email: str,
    access_token: str,
    password: str,
    hardware_id: str,
) -> dict:
    email = str(provider_email or "").strip().lower()
    hardware = str(hardware_id or "").strip().lower()

    if (
        len(hardware) != 64
        or any(ch not in "0123456789abcdef" for ch in hardware)
    ):
        raise ValueError(
            "Stable Linux hardware ID is invalid."
        )

    rows = _fetch_account_wallet_rows(
        supabase_client,
        email,
        access_token,
    )

    exact_rows = _find_exact_device_rows(
        rows,
        hardware,
    )

    if len(exact_rows) > 1:
        raise RuntimeError(
            "Multiple wallet rows exist for this "
            "email and hardware ID."
        )

    selected_row = (
        exact_rows[0]
        if exact_rows
        else None
    )

    if selected_row is None:
        legacy_row = next(
            (
                row
                for row in rows
                if isinstance(row, dict)
                and not str(
                    row.get("hardware_id") or ""
                ).strip()
            ),
            None,
        )

        if legacy_row is not None:
            legacy_id = legacy_row.get("id")

            if legacy_id is not None:
                (
                    supabase_client
                    .table("worker_wallets")
                    .update({"hardware_id": hardware})
                    .eq("id", legacy_id)
                    .is_("hardware_id", "null")
                    .execute()
                )

                claimed_rows = _fetch_account_wallet_rows(
                    supabase_client,
                    email,
                    access_token,
                )

                exact_rows = _find_exact_device_rows(
                    claimed_rows,
                    hardware,
                )

                if len(exact_rows) > 1:
                    raise RuntimeError(
                        "Multiple wallet rows exist for this "
                        "email and hardware ID."
                    )

                if exact_rows:
                    selected_row = exact_rows[0]

    if selected_row is None:
        account = Account.create()
        encrypted_key = encrypt_wallet_private_key(
            account.key.hex(),
            password,
            email,
        )

        try:
            insert_response = (
                supabase_client
                .table("worker_wallets")
                .insert({
                    "email": email,
                    "hardware_id": hardware,
                    "private_key": encrypted_key,
                })
                .execute()
            )

            inserted_rows = (
                list(insert_response.data)
                if isinstance(insert_response.data, list)
                else []
            )

            exact_inserted = _find_exact_device_rows(
                inserted_rows,
                hardware,
            )

            if exact_inserted:
                selected_row = exact_inserted[0]
        except Exception:
            concurrent_rows = _fetch_account_wallet_rows(
                supabase_client,
                email,
                access_token,
            )
            exact_rows = _find_exact_device_rows(
                concurrent_rows,
                hardware,
            )

            if len(exact_rows) != 1:
                raise

            selected_row = exact_rows[0]

        if selected_row is None:
            refreshed_rows = _fetch_account_wallet_rows(
                supabase_client,
                email,
                access_token,
            )
            exact_rows = _find_exact_device_rows(
                refreshed_rows,
                hardware,
            )

            if len(exact_rows) != 1:
                raise RuntimeError(
                    "Device wallet insert completed without "
                    "one readable device row."
                )

            selected_row = exact_rows[0]

    encrypted_payload = str(
        selected_row.get("private_key") or ""
    ).strip()

    if not encrypted_payload:
        raise RuntimeError(
            "Device wallet row has no encrypted private key."
        )

    return selected_row


def recover_wallet_identity(
    supabase_client,
    provider_email: str,
    access_token: str,
    password: str,
    expected_wallet: str = "",
) -> Dict[str, str]:
    hardware_id = get_linux_wallet_hardware_id()

    selected_row = fetch_or_create_device_wallet(
        supabase_client,
        provider_email,
        access_token,
        password,
        hardware_id,
    )

    private_key = decrypt_wallet_private_key(
        selected_row.get("private_key"),
        password,
        provider_email,
    )

    identity = validate_wallet_identity(
        private_key,
        expected_wallet,
    )

    identity["hardwareId"] = hardware_id
    return identity
