"""Authenticated encryption (AES-256-GCM) for sensitive values.

Format: ``v1:<base64(nonce|ciphertext|tag)>``. The tenant id is bound as
associated data so a ciphertext cannot be moved between tenants.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_settings

_VERSION = "v1"


def _key() -> bytes:
    return get_settings().encryption_key_bytes()


def encrypt_str(plaintext: str, context: str = "") -> str:
    nonce = os.urandom(12)
    ct = AESGCM(_key()).encrypt(nonce, plaintext.encode("utf-8"), context.encode("utf-8"))
    return f"{_VERSION}:{base64.b64encode(nonce + ct).decode('ascii')}"


def decrypt_str(token: str, context: str = "") -> str:
    version, _, payload = token.partition(":")
    if version != _VERSION:
        raise ValueError("Unsupported ciphertext version")
    raw = base64.b64decode(payload)
    nonce, ct = raw[:12], raw[12:]
    return AESGCM(_key()).decrypt(nonce, ct, context.encode("utf-8")).decode("utf-8")


def encrypt_json(data: dict[str, Any], context: str = "") -> str:
    return encrypt_str(json.dumps(data, separators=(",", ":")), context)


def decrypt_json(token: str, context: str = "") -> dict[str, Any]:
    return json.loads(decrypt_str(token, context))


def keyed_hash(value: str) -> str:
    """Deterministic HMAC-SHA256 used for lookups/deduplication of secrets (e.g. account numbers)."""
    return hmac.new(_key(), value.encode("utf-8"), hashlib.sha256).hexdigest()


def mask_account_number(number: str) -> str:
    digits = "".join(ch for ch in number if ch.isalnum())
    if len(digits) <= 4:
        return "••••"
    return f"••••{digits[-4:]}"
