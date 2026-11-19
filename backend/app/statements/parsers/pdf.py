"""Text-based PDF parser (PyMuPDF).

Strategy 1: PyMuPDF table detection → generic table normaliser.
Strategy 2: line-based extraction for statements drawn without table rulings.
Scanned PDFs are not guessed at: they are routed to OCR (optional) / manual review.
"""
from __future__ import annotations

import re
from typing import Any

import fitz
from app.core.config import get_settings

from app.statements.normalize import NormalizedStatement, detect_header, normalize_table
from app.statements.parsers.base import ParseInput, ParserUnavailable, StatementParser

_LINE_DATE = re.compile(r"^(\d{1,2}[/\-. ](\d{1,2}|[A-Za-z]{3})[/\-. ]\d{2,4}|\d{4}-\d{2}-\d{2})\s+(.*)$")
_AMT = r"\(?-?[\d,]+\.\d{2}\)?(?:\s?(?:Cr|Dr|CR|DR))?"
_TAIL = re.compile(rf"(.*?)\s+({_AMT})(?:\s+({_AMT}))?(?:\s+({_AMT}))?\s*$")


class PdfParser(StatementParser):
    name = "generic_pdf"
    formats = ("pdf",)

    def parse(self, data: ParseInput) -> NormalizedStatement:
        try:
            doc = fitz.open(stream=data.content, filetype="pdf")
        except Exception as exc:
            raise ParserUnavailable("PDF could not be opened", status="failed") from exc
        with doc:
            if doc.needs_pass:
                if not data.password or not doc.authenticate(data.password):
                    raise ParserUnavailable("PDF is password protected", status="needs_review")
            text_chars = sum(len(p.get_text("text").strip()) for p in doc)
            if text_chars < 30:
                # Optional local OCR. No document is sent to an external service.
                try:
                    texts = []
                    for page in doc:
                        textpage = page.get_textpage_ocr(language="eng", dpi=200, full=True,
                            tessdata=str(get_settings().ocr_tessdata_dir))
                        texts.append(page.get_text("text", textpage=textpage))
                    result = self._lines(doc, data, page_texts=texts)
                    if not result.rows:
                        raise ValueError("No reliable OCR transactions")
                    result.warn("local_ocr", "Extracted with local OCR; verify figures against the original statement")
                    return result
                except Exception:
                    raise ParserUnavailable("Scanned PDF needs local Tesseract OCR and manual verification",
                                            status="needs_ocr") from None
            table_rows = self._tables(doc)
            if table_rows and detect_header(table_rows):
                result = normalize_table(table_rows, account_currency=data.account_currency)
                if result.rows:
                    return result
            return self._lines(doc, data)

    @staticmethod
    def _tables(doc: fitz.Document) -> list[list[Any]]:
        rows: list[list[Any]] = []
        header: list[Any] | None = None
        for page in doc:
            try:
                tables = page.find_tables()
            except Exception:
                continue
            for t in tables.tables:
                extracted = t.extract()
                if not extracted:
                    continue
                if header is None:
                    header = extracted[0]
                    rows.extend(extracted)
                else:
                    start = 1 if [str(c).strip() for c in extracted[0]] == [str(c).strip() for c in header] else 0
                    rows.extend(extracted[start:])
        return rows

    def _lines(self, doc: fitz.Document, data: ParseInput, *, page_texts=None) -> NormalizedStatement:
        """Fallback: Date  Description  [Debit/Amount]  [Credit]  Balance — the last amount is the balance."""
        header = ["Date", "Description", "Amount", "Balance"]
        rows: list[list[Any]] = [header]
        pre: list[list[Any]] = []
        texts = page_texts if page_texts is not None else (page.get_text("text") for page in doc)
        for text in texts:
            for line in text.splitlines():
                line = line.strip()
                m = _LINE_DATE.match(line)
                if not m:
                    if re.search(r"opening balance|brought forward", line, re.I):
                        pre.append([line])
                    continue
                tail = _TAIL.match(m.group(3))
                if not tail:
                    continue
                amounts = [a for a in tail.groups()[1:] if a]
                desc = tail.group(1)
                if len(amounts) == 1:
                    rows.append([m.group(1), desc, "", amounts[0]])
                    continue
                bal = amounts[-1]
                if len(amounts) == 3:  # debit credit balance
                    debit, credit = amounts[0], amounts[1]
                    amt = f"-{debit}" if debit and debit not in ("0.00",) else credit
                else:
                    amt = amounts[0]
                rows.append([m.group(1), desc, amt, bal])
        result = normalize_table(pre + rows, account_currency=data.account_currency,
                                 column_map={"date": 0, "description": 1, "amount": 2, "balance": 3},
                                 header_index=len(pre))
        # sign inference: when amounts are unsigned use running balance movement
        _infer_signs(result)
        result.warn("pdf_line_mode", "Extracted using line-based PDF parsing; review recommended")
        return result


def _infer_signs(st: NormalizedStatement) -> None:
    from app.statements.normalize import TOLERANCE, finalize

    prev = st.opening_balance
    changed = False
    for r in st.rows:
        if prev is not None and r.running_balance is not None:
            amt = r.amount
            if abs(prev + amt - r.running_balance) > TOLERANCE and abs(prev - amt - r.running_balance) <= TOLERANCE:
                r.debit, r.credit = r.credit, r.debit
                changed = True
            prev = r.running_balance
    if changed:
        st.warnings = [w for w in st.warnings if w["code"] not in ("running_balance_break", "arithmetic_mismatch")]
        st.running_balance_breaks = 0
        st.opening_derived = st.closing_derived = False
        finalize(st)
