"""Bank-specific statement layout profiles.

Add a profile when a bank's export cannot be recognised generically. Each profile
must be built from a real sample statement of that bank (keep a redacted fixture
in tests/fixtures/statements/<bank>/).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.statements.normalize import detect_header


@dataclass(frozen=True)
class StatementProfile:
    institution_external_id: str
    column_map: dict[str, int]
    date_format: str | None
    header_contains: str

    def find_header(self, rows: list[list[Any]]) -> int:
        for i, row in enumerate(rows[:60]):
            if any(self.header_contains.lower() == str(c).strip().lower() for c in row):
                return i
        found = detect_header(rows)
        return found[0] if found else 0


PROFILES: dict[str, StatementProfile] = {
    # The demo bank export layout (Date, Value Date, Description, Reference, Debit, Credit, Balance)
    "DEMO_GB_NORTHWIND": StatementProfile("DEMO_GB_NORTHWIND",
                                          {"date": 0, "value_date": 1, "description": 2, "reference": 3,
                                           "debit": 4, "credit": 5, "balance": 6}, "%d/%m/%Y", "Value Date"),
}


def profile_for(institution_external_id: str | None) -> StatementProfile | None:
    return PROFILES.get(institution_external_id or "")
