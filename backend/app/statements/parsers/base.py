"""Parser interface + registry.

Generic parsers work from header detection. Bank-specific *profiles* can pin the
column map, header row and date format for a known layout (see profiles.py) so
banks with unusual layouts are handled explicitly instead of by guesswork.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass

from app.statements.normalize import NormalizedStatement


@dataclass(frozen=True)
class ParseInput:
    content: bytes
    filename: str
    detected_format: str
    account_currency: str | None
    institution_external_id: str | None = None
    password: str | None = None


class StatementParser(abc.ABC):
    name: str
    formats: tuple[str, ...]

    @abc.abstractmethod
    def parse(self, data: ParseInput) -> NormalizedStatement: ...


class ParserUnavailable(Exception):
    """Raised when no reliable parser exists for the input (e.g. scanned PDF without OCR)."""

    def __init__(self, message: str, status: str = "needs_review") -> None:
        super().__init__(message)
        self.status = status
