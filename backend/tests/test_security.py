"""Encryption, hashing, tokens and log redaction."""
from __future__ import annotations

import time
import uuid

import jwt
import pytest
from cryptography.exceptions import InvalidTag

from app.core import crypto
from app.core.config import get_settings
from app.core.logging import redact, redact_text
from app.core.security import (create_access_token, decode_access_token, hash_password, validate_password_strength,
                               verify_password)


def test_aes_gcm_roundtrip_and_tamper_detection():
    tok = crypto.encrypt_json({"access_token": "s3cr3t"}, "tenant-a")
    assert "s3cr3t" not in tok and tok.startswith("v1:")
    assert crypto.decrypt_json(tok, "tenant-a") == {"access_token": "s3cr3t"}
    with pytest.raises(InvalidTag):
        crypto.decrypt_json(tok, "tenant-b")  # bound to tenant (AAD)
    raw = bytearray(tok.encode())
    raw[-3] = ord("A") if raw[-3] != ord("A") else ord("B")
    with pytest.raises(Exception):
        crypto.decrypt_json(raw.decode(), "tenant-a")


def test_nonce_uniqueness():
    assert crypto.encrypt_str("same") != crypto.encrypt_str("same")


def test_masking_and_keyed_hash():
    assert crypto.mask_account_number("GB29 NWBK 6016 1331 9268 19") == "••••6819"
    assert crypto.keyed_hash("123") == crypto.keyed_hash("123") != crypto.keyed_hash("124")


def test_password_hashing_and_policy():
    h = hash_password("CorrectHorse9Battery")
    assert h.startswith("$argon2id$") and verify_password("CorrectHorse9Battery", h)
    assert not verify_password("wrong", h)
    for weak in ("short1A", "alllowercase123", "NoDigitsHereAtAll"):
        with pytest.raises(ValueError):
            validate_password_strength(weak)


def test_access_token_validation(monkeypatch):
    tok, _ = create_access_token(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), session_id=uuid.uuid4(), role="viewer",
                                 permissions=[])
    assert decode_access_token(tok)["role"] == "viewer"
    forged = jwt.encode({"sub": "x", "tid": "y", "sid": "z", "exp": int(time.time()) + 60, "typ": "access",
                         "iss": "gbp"}, "not-the-secret", algorithm="HS256")
    with pytest.raises(jwt.InvalidSignatureError):
        decode_access_token(forged)
    expired = jwt.encode({"sub": "x", "tid": "y", "sid": "z", "exp": int(time.time()) - 5, "typ": "access",
                          "iss": "gbp"}, get_settings().jwt_secret, algorithm="HS256")
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(expired)
    none_alg = jwt.encode({"sub": "x", "tid": "y", "sid": "z", "exp": int(time.time()) + 60}, None, algorithm="none")
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(none_alg)


def test_redaction():
    out = redact({"password": "p", "otp": "123456", "nested": {"api_key": "k", "note": "acct 12345678901234"}})
    assert out["password"] == out["otp"] == "[REDACTED]" and out["nested"]["api_key"] == "[REDACTED]"
    assert "12345678901234" not in out["nested"]["note"]
    assert "abc.def" not in redact_text("Authorization: Bearer abc.def")
