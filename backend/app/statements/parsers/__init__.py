from app.statements.parsers.base import ParseInput, ParserUnavailable, StatementParser
from app.statements.parsers.pdf import PdfParser
from app.statements.parsers.tabular import CsvParser, ExcelParser

_PARSERS: list[StatementParser] = [CsvParser(), ExcelParser(), PdfParser()]


def parser_for(fmt: str) -> StatementParser:
    for p in _PARSERS:
        if fmt in p.formats:
            return p
    raise ParserUnavailable(f"No reliable parser is available for format '{fmt}'", status="failed")


__all__ = ["ParseInput", "ParserUnavailable", "StatementParser", "parser_for"]
