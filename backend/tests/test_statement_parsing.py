"""Statement extraction engine: primitives, CSV, XLSX, PDF (text and scanned), validation."""
from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal

import fitz
import pytest
from openpyxl import Workbook

from app.statements.detect import detect_format
from app.statements.normalize import choose_date_format, normalize_table, parse_amount
from app.statements.parsers import ParseInput, ParserUnavailable, parser_for


@pytest.mark.parametrize("raw,expected", [
    ("1,234.56", "1234.56"), ("(250.00)", "-250.00"), ("-99.10", "-99.10"), ("£1,000", "1000.00"),
    ("₹ 12,34,567.89", "1234567.89"), ("1.234,56", "1234.56"), ("500.00 Dr", "-500.00"), ("75.25Cr", "75.25"),
    ("", None), ("—", None), ("abc", None), ("120-", "-120.00"),
])
def test_parse_amount(raw, expected):
    v = parse_amount(raw)
    assert (None if v is None else f"{v:.2f}") == expected


def test_date_format_prefers_column_fit():
    assert choose_date_format(["13/01/2025", "02/02/2025"]) == "%d/%m/%Y"
    assert choose_date_format(["01/13/2025", "02/28/2025"], prefer_monthfirst=True) == "%m/%d/%Y"
    assert choose_date_format(["2025-03-01", "2025-03-02"]) == "%Y-%m-%d"
    assert choose_date_format(["05-Mar-2025", "06-Mar-2025"]) == "%d-%b-%Y"


ROWS = [
    ["Bank of Example plc"], ["Currency", "GBP"], [],
    ["Date", "Description", "Reference", "Paid out", "Paid in", "Balance"],
    ["01/03/2025", "Opening balance", "", "", "", "1,000.00"],
    ["02/03/2025", "Customer receipt", "R1", "", "500.00", "1,500.00"],
    ["03/03/2025", "Supplier payment", "P1", "200.00", "", "1,300.00"],
    ["", "continued narrative", "", "", "", ""],
    ["04/03/2025", "Bank charges", "", "5.50", "", "1,294.50"],
    ["31/03/2025", "Closing balance", "", "", "", "1,294.50"],
]


def test_normalize_table_extracts_and_validates():
    st = normalize_table(ROWS, account_currency="GBP")
    assert len(st.rows) == 3
    assert st.currency == "GBP"
    assert st.opening_balance == Decimal("1000.00") and st.closing_balance == Decimal("1294.50")
    assert not st.opening_derived and not st.closing_derived
    assert st.total_credits == Decimal("500") and st.total_debits == Decimal("205.5")
    assert st.arithmetic_valid is True
    assert st.rows[1].description.endswith("continued narrative")
    assert st.rows[0].raw["description"] == "Customer receipt"  # original preserved
    assert st.confidence >= Decimal("0.95")


def test_arithmetic_mismatch_and_running_balance_break():
    rows = [r[:] for r in ROWS]
    rows[6][5] = "1,350.00"  # wrong running balance
    rows[9][5] = "1,400.00"  # wrong closing
    st = normalize_table(rows)
    assert st.arithmetic_valid is False
    assert st.running_balance_breaks >= 1
    assert any(w["code"] == "arithmetic_mismatch" for w in st.warnings)
    assert st.confidence < Decimal("0.8")


def test_reverse_chronological_and_derived_balances():
    rows = [["Txn Date", "Narration", "Withdrawal Amt", "Deposit Amt", "Closing Balance"],
            ["05/01/2025", "UPI CR", "", "100.00", "1,100.00"],
            ["04/01/2025", "NEFT DR", "50.00", "", "1,000.00"],
            ["03/01/2025", "Interest", "", "50.00", "1,050.00"]]
    st = normalize_table(rows, account_currency="INR")
    assert [r.booking_date for r in st.rows] == [date(2025, 1, 3), date(2025, 1, 4), date(2025, 1, 5)]
    assert st.opening_derived and st.opening_balance == Decimal("1000.00")
    assert st.closing_balance == Decimal("1100.00")
    assert st.arithmetic_valid is True


def test_single_amount_column_with_drcr_flag():
    rows = [["Date", "Details", "Amount", "Dr/Cr", "Balance"],
            ["2025-02-01", "Salary", "1000", "CR", "1000"], ["2025-02-02", "Rent", "400", "DR", "600"]]
    st = normalize_table(rows)
    assert [r.amount for r in st.rows] == [Decimal("1000"), Decimal("-400")]


def test_csv_parser_and_detection():
    buf = io.StringIO()
    csv.writer(buf).writerows(ROWS)
    content = buf.getvalue().encode()
    det = detect_format(content, "x.csv")
    assert det.format == "csv"
    st = parser_for("csv").parse(ParseInput(content, "x.csv", "csv", "GBP"))
    assert len(st.rows) == 3 and st.arithmetic_valid


def test_xlsx_parser():
    wb = Workbook()
    ws = wb.active
    for r in ROWS:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    content = buf.getvalue()
    assert detect_format(content).format == "xlsx"
    st = parser_for("xlsx").parse(ParseInput(content, "x.xlsx", "xlsx", "GBP"))
    assert len(st.rows) == 3 and st.arithmetic_valid


def _pdf_with_lines(lines: list[str]) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    y = 72
    for line in lines:
        page.insert_text((40, y), line, fontsize=9, fontname="helv")
        y += 14
    return doc.tobytes()


def test_text_pdf_line_mode():
    content = _pdf_with_lines([
        "Example Bank Statement", "Opening balance 1,000.00",
        "02/03/2025 Customer receipt 500.00 1,500.00",
        "03/03/2025 Supplier payment 200.00 1,300.00",
        "04/03/2025 Bank charges 5.50 1,294.50",
    ])
    det = detect_format(content)
    assert det.format == "pdf" and det.scanned_pdf is False
    st = parser_for("pdf").parse(ParseInput(content, "s.pdf", "pdf", "GBP"))
    assert len(st.rows) == 3
    assert [r.amount for r in st.rows] == [Decimal("500"), Decimal("-200"), Decimal("-5.5")]
    assert st.arithmetic_valid is True


def test_scanned_pdf_requires_ocr():
    doc = fitz.open()
    page = doc.new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 50, 50), False)
    pix.clear_with(200)
    page.insert_image(fitz.Rect(50, 50, 300, 300), pixmap=pix)
    content = doc.tobytes()
    assert detect_format(content).scanned_pdf is True
    with pytest.raises(ParserUnavailable) as exc:
        parser_for("pdf").parse(ParseInput(content, "scan.pdf", "pdf", "GBP"))
    assert exc.value.status == "needs_ocr"


def test_unknown_format_rejected():
    assert detect_format(b"\x89PNG\r\n\x1a\n....").format == "unknown"
    with pytest.raises(ParserUnavailable):
        parser_for("docx")
