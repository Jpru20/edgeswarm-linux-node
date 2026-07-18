#!/usr/bin/env python3

import base64
from typing import Dict

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


def fetch_encrypted_wallet(
    supabase_client,
    provider_email: str,
    access_token: str,
) -> str:
    email = str(
        provider_email or ""
    ).strip().lower()

    token = str(
        access_token or ""
    ).strip()

    if not email:
        raise ValueError(
            "Provider email is required."
        )

    if not token:
        raise ValueError(
            "Supabase access token is required."
        )

    supabase_client.postgrest.auth(token)

    response = (
        supabase_client
        .table("worker_wallets")
        .select("private_key")
        .eq("email", email)
        .limit(2)
        .execute()
    )

    rows = (
        response.data
        if isinstance(response.data, list)
        else []
    )

    if len(rows) != 1:
        raise RuntimeError(
            "Expected exactly one existing "
            "unified wallet row; "
            f"found {len(rows)}."
        )

    encrypted_payload = str(
        rows[0].get("private_key")
        or ""
    ).strip()

    if not encrypted_payload:
        raise RuntimeError(
            "Unified wallet row has no "
            "encrypted private key."
        )

    return encrypted_payload


def recover_wallet_identity(
    supabase_client,
    provider_email: str,
    access_token: str,
    password: str,
    expected_wallet: str = "",
) -> Dict[str, str]:
    encrypted_payload = fetch_encrypted_wallet(
        supabase_client,
        provider_email,
        access_token,
    )

    private_key = decrypt_wallet_private_key(
        encrypted_payload,
        password,
        provider_email,
    )

    return validate_wallet_identity(
        private_key,
        expected_wallet,
    )
