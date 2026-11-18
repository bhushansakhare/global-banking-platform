"""Deterministic transaction fingerprints used for idempotent ingestion and duplicate detection."""
from __future__ import annotations

import hashlib
import re
import uuid
from collections import Counter
from datetime import date
from decimal import Decimal


def normalise_description(text: str) -> str:
    t = re.sub(r"[^a-z0-9]", "", (text or "").lower())
    return t[:80]


def base_key(account_id: uuid.UUID, booking_date: date, amount: Decimal, description: str,
             reference: str | None) -> str:
    return "|".join([str(account_id), booking_date.isoformat(), f"{Decimal(amount).quantize(Decimal('0.01'))}",
                     normalise_description(description), (reference or "").strip().upper()])


class FingerprintBuilder:
    """Adds an occurrence index so two genuinely identical lines in one batch stay distinct,
    while re-importing the same batch reproduces the same fingerprints."""

    def __init__(self) -> None:
        self._seen: Counter[str] = Counter()

    def make(self, account_id: uuid.UUID, booking_date: date, amount: Decimal, description: str,
             reference: str | None) -> str:
        key = base_key(account_id, booking_date, amount, description, reference)
        self._seen[key] += 1
        return hashlib.sha256(f"{key}|{self._seen[key]}".encode()).hexdigest()
