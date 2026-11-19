"""Turn raw tabular statement rows into validated, normalised transactions.

Nothing here assumes a particular bank layout: headers are recognised from a
multilingual alias table, dates are parsed by choosing the format that fits the
whole column, and every derived value is reported as a warning so it can be
reviewed. The original cell values are preserved on every row for audit.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

CENT = Decimal("0.01")
TOLERANCE = Decimal("0.005")

HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "date": ("date", "txn date", "transaction date", "tran date", "posting date", "post date", "booking date",
             "trans date", "buchungstag", "datum", "fecha", "date opération"),
    "value_date": ("value date", "value dt", "valuta", "wertstellung", "value"),
    "description": ("description", "narration", "particulars", "details", "transaction details", "remarks",
                    "memo", "transaction description", "verwendungszweck", "beschreibung", "concepto", "libellé"),
    "reference": ("reference", "ref", "ref no", "ref no.", "reference no", "chq/ref no", "chq./ref.no.",
                  "cheque no", "chq no", "transaction id", "utr", "utr no", "referenz"),
    "debit": ("debit", "debits", "withdrawal", "withdrawals", "withdrawal amt", "withdrawal amount", "dr",
              "paid out", "money out", "debit amount", "soll", "out"),
    "credit": ("credit", "credits", "deposit", "deposits", "deposit amt", "deposit amount", "cr", "paid in",
               "money in", "credit amount", "haben", "in"),
    "amount": ("amount", "transaction amount", "amt", "betrag", "importe", "montant"),
    "balance": ("balance", "running balance", "closing balance", "balance amount", "available balance",
                "saldo", "solde"),
    "drcr": ("dr/cr", "cr/dr", "type", "debit/credit", "transaction type"),
}

OPENING_PAT = re.compile(r"(opening\s+balance|balance\s+(brought|b/)\s*f(or)?w(ar)?d|^b/f$|balance b/f|"
                         r"anfangssaldo|saldo inicial)", re.I)
CLOSING_PAT = re.compile(r"(closing\s+balance|balance\s+carried\s+f(or)?w(ar)?d|^c/f$|balance c/f|"
                         r"endsaldo|saldo final)", re.I)

DATE_FORMATS_DAYFIRST = ["%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y", "%d.%m.%y", "%d-%b-%Y",
                         "%d %b %Y", "%d-%b-%y", "%d %b %y", "%d %B %Y", "%d-%B-%Y", "%Y-%m-%d", "%Y/%m/%d",
                         "%Y%m%d"]
DATE_FORMATS_MONTHFIRST = ["%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y", "%b %d, %Y", "%b %d %Y", "%B %d, %Y"]


@dataclass
class NormalizedRow:
    line_number: int
    booking_date: date
    value_date: date | None
    description: str
    reference: str | None
    debit: Decimal
    credit: Decimal
    running_balance: Decimal | None
    raw: dict[str, Any]

    @property
    def amount(self) -> Decimal:
        return self.credit - self.debit


@dataclass
class NormalizedStatement:
    rows: list[NormalizedRow] = field(default_factory=list)
    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None
    opening_derived: bool = False
    closing_derived: bool = False
    period_start: date | None = None
    period_end: date | None = None
    currency: str | None = None
    warnings: list[dict[str, Any]] = field(default_factory=list)
    unparsed_rows: int = 0
    column_map: dict[str, int] = field(default_factory=dict)
    date_format: str | None = None
    running_balance_breaks: int = 0
    arithmetic_valid: bool | None = None
    arithmetic_difference: Decimal | None = None

    @property
    def total_credits(self) -> Decimal:
        return sum((r.credit for r in self.rows), Decimal(0))

    @property
    def total_debits(self) -> Decimal:
        return sum((r.debit for r in self.rows), Decimal(0))

    def warn(self, code: str, message: str, **extra: Any) -> None:
        self.warnings.append({"code": code, "message": message, **extra})

    @property
    def confidence(self) -> Decimal:
        score = Decimal("1.0")
        if not self.rows:
            return Decimal("0")
        if self.opening_derived:
            score -= Decimal("0.05")
        if self.closing_derived:
            score -= Decimal("0.05")
        if self.arithmetic_valid is False:
            score -= Decimal("0.35")
        if self.arithmetic_valid is None:
            score -= Decimal("0.15")
        score -= min(Decimal("0.3"), Decimal(self.running_balance_breaks) * Decimal("0.05"))
        total = len(self.rows) + self.unparsed_rows
        if total:
            score -= Decimal("0.3") * Decimal(self.unparsed_rows) / Decimal(total)
        if any(w.get("code") == "local_ocr" for w in self.warnings):
            score = min(score, Decimal("0.79"))
        return max(Decimal("0"), score).quantize(Decimal("0.0001"))


# ---- primitive parsers -------------------------------------------------------
_CCY = re.compile(r"[£$€₹¥]|\b(INR|GBP|USD|EUR|AED|SGD|AUD|CAD|JPY|CHF|Rs\.?)\b", re.I)


def parse_amount(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            d = Decimal(str(value))
        except InvalidOperation:
            return None
        return None if d.is_nan() else d.quantize(Decimal("0.0001"))
    s = str(value).strip()
    if not s or s in {"-", "--", "—", "nan", "None"}:
        return None
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1]
    m = re.search(r"(?<![a-z])(cr|dr)\.?$", s, re.I)
    if m:
        negative = negative or m.group(1).lower() == "dr"
        s = s[: m.start()].strip()
    s = _CCY.sub("", s).replace(" ", "").replace(" ", "")
    if s.endswith("-"):
        negative, s = True, s[:-1]
    if s.startswith("-"):
        negative, s = not negative, s[1:]
    elif s.startswith("+"):
        s = s[1:]
    # European format 1.234,56
    if re.fullmatch(r"\d{1,3}(\.\d{3})+,\d{1,4}", s) or re.fullmatch(r"\d+,\d{1,2}", s):
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    if not re.fullmatch(r"\d+(\.\d+)?", s):
        return None
    d = Decimal(s)
    return (-d if negative else d).quantize(Decimal("0.0001"))


def _try_date(value: str, fmt: str) -> date | None:
    try:
        return datetime.strptime(value, fmt).date()
    except ValueError:
        return None


def coerce_date_cell(value: Any) -> str | date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s or s.lower() in {"nan", "nat", "none"}:
        return None
    # excel datetimes stringified as "2025-01-02 00:00:00"
    s = re.sub(r"\s+00:00(:00)?$", "", s)
    return s


def choose_date_format(samples: list[str], prefer_monthfirst: bool = False) -> str | None:
    if not samples:
        return None
    order = (DATE_FORMATS_MONTHFIRST + DATE_FORMATS_DAYFIRST) if prefer_monthfirst else (
        DATE_FORMATS_DAYFIRST + DATE_FORMATS_MONTHFIRST)
    best, best_hits = None, 0
    for fmt in order:
        hits = sum(1 for s in samples if _try_date(s, fmt))
        if hits > best_hits:
            best, best_hits = fmt, hits
    return best if best_hits >= max(1, int(len(samples) * 0.6)) else None


def parse_date(value: Any, fmt: str | None) -> date | None:
    v = coerce_date_cell(value)
    if v is None or isinstance(v, date):
        return v
    if fmt:
        d = _try_date(v, fmt)
        if d:
            return d
    for f in DATE_FORMATS_DAYFIRST:
        d = _try_date(v, f)
        if d:
            return d
    return None


def _clean(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s if s is not None else "")).strip()


def _norm_header(s: Any) -> str:
    return re.sub(r"[^a-z/ .]", "", _clean(s).lower()).strip(" .")


def detect_header(rows: list[list[Any]], max_scan: int = 40) -> tuple[int, dict[str, int]] | None:
    best: tuple[int, dict[str, int]] | None = None
    best_score = 0
    for idx, row in enumerate(rows[:max_scan]):
        mapping: dict[str, int] = {}
        for col, cell in enumerate(row):
            h = _norm_header(cell)
            if not h:
                continue
            for key, aliases in HEADER_ALIASES.items():
                if key in mapping:
                    continue
                if h in aliases:
                    mapping[key] = col
                    break
        has_amount = "amount" in mapping or ("debit" in mapping or "credit" in mapping)
        if "date" in mapping and has_amount:
            score = len(mapping)
            if score > best_score:
                best, best_score = (idx, mapping), score
    return best


META_CURRENCY = re.compile(r"\b(currency|ccy)\b", re.I)


def normalize_table(rows: list[list[Any]], *, account_currency: str | None = None,
                    column_map: dict[str, int] | None = None, header_index: int | None = None,
                    date_format: str | None = None) -> NormalizedStatement:
    """Normalise a statement table. ``column_map``/``date_format`` may come from a bank profile."""
    out = NormalizedStatement(currency=account_currency)
    if column_map is None or header_index is None:
        found = detect_header(rows)
        if not found:
            out.warn("header_not_found", "Could not identify a transaction table header")
            return out
        header_index, column_map = found
    out.column_map = column_map

    # metadata above the header (currency / opening balance lines)
    for row in rows[:header_index]:
        cells = [_clean(c) for c in row if _clean(c)]
        if len(cells) >= 2 and META_CURRENCY.search(cells[0]) and re.fullmatch(r"[A-Z]{3}", cells[1]):
            out.currency = cells[1]
        text = " ".join(cells)
        if OPENING_PAT.search(text):
            amt = next((parse_amount(c) for c in reversed(cells) if parse_amount(c) is not None), None)
            if amt is not None:
                out.opening_balance = amt

    body = rows[header_index + 1:]

    def cell(row: list[Any], key: str) -> Any:
        i = column_map.get(key)
        return row[i] if i is not None and i < len(row) else None

    prefer_mf = (account_currency or out.currency) == "USD"
    samples = [str(v) for v in (coerce_date_cell(cell(r, "date")) for r in body) if isinstance(v, str)][:200]
    fmt = date_format or choose_date_format(samples, prefer_mf)
    out.date_format = fmt

    for n, row in enumerate(body, start=header_index + 2):
        if not any(_clean(c) for c in row):
            continue
        desc = _clean(cell(row, "description"))
        bal = parse_amount(cell(row, "balance"))
        row_text = " ".join(_clean(c) for c in row)
        if OPENING_PAT.search(desc or row_text):
            if bal is not None:
                out.opening_balance = bal
            continue
        if CLOSING_PAT.search(desc or row_text):
            if bal is not None:
                out.closing_balance = bal
            continue
        d = parse_date(cell(row, "date"), fmt)
        debit = credit = Decimal(0)
        if "debit" in column_map or "credit" in column_map:
            dv, cv = parse_amount(cell(row, "debit")), parse_amount(cell(row, "credit"))
            debit, credit = abs(dv) if dv else Decimal(0), abs(cv) if cv else Decimal(0)
            if dv is None and cv is None and "amount" in column_map:
                amt = parse_amount(cell(row, "amount"))
                if amt is not None:
                    debit, credit = (-amt, Decimal(0)) if amt < 0 else (Decimal(0), amt)
        else:
            amt = parse_amount(cell(row, "amount"))
            if amt is not None:
                flag = _clean(cell(row, "drcr")).lower()
                if flag in ("dr", "debit", "d"):
                    amt = -abs(amt)
                elif flag in ("cr", "credit", "c"):
                    amt = abs(amt)
                debit, credit = (-amt, Decimal(0)) if amt < 0 else (Decimal(0), amt)
        if d is None or (debit == 0 and credit == 0):
            if d is None and not desc and bal is None:
                continue
            # continuation line of a multi-line description
            if d is None and desc and out.rows and debit == 0 and credit == 0:
                out.rows[-1].description = f"{out.rows[-1].description} {desc}".strip()
                continue
            out.unparsed_rows += 1
            continue
        raw = {str(k): _clean(cell(row, k)) for k in column_map}
        out.rows.append(NormalizedRow(
            line_number=n, booking_date=d, value_date=parse_date(cell(row, "value_date"), fmt),
            description=desc or "(no description)", reference=_clean(cell(row, "reference")) or None,
            debit=debit, credit=credit, running_balance=bal, raw=raw,
        ))

    finalize(out)
    return out


def finalize(out: NormalizedStatement) -> None:
    rows = out.rows
    if not rows:
        out.warn("no_transactions", "No transaction rows were extracted")
        return
    # newest-first statements → chronological order
    if len(rows) > 1 and rows[0].booking_date > rows[-1].booking_date:
        rows.reverse()
        out.warn("reversed_order", "Statement was in reverse chronological order and has been re-ordered")

    out.period_start = out.period_start or min(r.booking_date for r in rows)
    out.period_end = out.period_end or max(r.booking_date for r in rows)

    has_bal = [r for r in rows if r.running_balance is not None]
    if out.opening_balance is None and rows[0].running_balance is not None:
        out.opening_balance = rows[0].running_balance - rows[0].amount
        out.opening_derived = True
        out.warn("opening_derived", "Opening balance derived from the first running balance")
    if out.closing_balance is None and has_bal:
        out.closing_balance = has_bal[-1].running_balance
        out.closing_derived = True
        out.warn("closing_derived", "Closing balance taken from the last running balance")

    # running balance consistency
    prev = out.opening_balance
    for r in rows:
        if r.running_balance is not None and prev is not None:
            if abs(prev + r.amount - r.running_balance) > TOLERANCE:
                out.running_balance_breaks += 1
                if out.running_balance_breaks <= 20:
                    out.warn("running_balance_break", "Running balance does not follow from previous row",
                             line=r.line_number, expected=str((prev + r.amount).quantize(CENT)),
                             actual=str(r.running_balance))
            prev = r.running_balance
        elif prev is not None:
            prev = prev + r.amount

    if out.opening_balance is not None and out.closing_balance is not None:
        expected = out.opening_balance + out.total_credits - out.total_debits
        out.arithmetic_difference = (out.closing_balance - expected).quantize(Decimal("0.0001"))
        out.arithmetic_valid = abs(out.arithmetic_difference) <= TOLERANCE
        if not out.arithmetic_valid:
            out.warn("arithmetic_mismatch", "Opening + credits − debits does not equal closing balance",
                     difference=str(out.arithmetic_difference))
    else:
        out.warn("balances_missing", "Opening/closing balance not available — arithmetic check skipped")
