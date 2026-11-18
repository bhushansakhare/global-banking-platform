"""CSV and Excel parsers (pandas/openpyxl/xlrd)."""
from __future__ import annotations

import csv
import io
from typing import Any

import pandas as pd

from app.statements.normalize import NormalizedStatement, normalize_table
from app.statements.parsers.base import ParseInput, ParserUnavailable, StatementParser
from app.statements.parsers.profiles import profile_for


def _decode(content: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ParserUnavailable("Unable to decode text file", status="failed")


def _apply_profile(rows: list[list[Any]], data: ParseInput) -> NormalizedStatement:
    profile = profile_for(data.institution_external_id)
    if profile:
        return normalize_table(rows, account_currency=data.account_currency, column_map=profile.column_map,
                               header_index=profile.find_header(rows), date_format=profile.date_format)
    return normalize_table(rows, account_currency=data.account_currency)


class CsvParser(StatementParser):
    name = "generic_csv"
    formats = ("csv",)

    def parse(self, data: ParseInput) -> NormalizedStatement:
        text = _decode(data.content)
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
            delim = dialect.delimiter
        except csv.Error:
            delim = ","
        rows = [row for row in csv.reader(io.StringIO(text), delimiter=delim)]
        return _apply_profile(rows, data)


class ExcelParser(StatementParser):
    name = "generic_excel"
    formats = ("xlsx", "xls")

    def parse(self, data: ParseInput) -> NormalizedStatement:
        engine = "openpyxl" if data.detected_format == "xlsx" else "xlrd"
        try:
            sheets = pd.read_excel(io.BytesIO(data.content), sheet_name=None, header=None, engine=engine, dtype=object)
        except Exception as exc:  # corrupt or password-protected workbook
            raise ParserUnavailable(f"Workbook could not be read: {type(exc).__name__}", status="failed") from exc
        best: NormalizedStatement | None = None
        for _, df in sheets.items():
            rows = [[None if (isinstance(v, float) and pd.isna(v)) else v for v in r]
                    for r in df.itertuples(index=False, name=None)]
            result = _apply_profile(rows, data)
            if best is None or len(result.rows) > len(best.rows):
                best = result
        if best is None:
            raise ParserUnavailable("Workbook has no sheets", status="failed")
        return best
