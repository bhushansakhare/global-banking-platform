"""Deterministic reconciliation engine (pure functions, no I/O).

Given the same inputs and rules it always produces the same output. Uncertain
matches are emitted as ``suggested`` — never silently ``confirmed``.

Passes:
  A  reference + exact amount                      → confirmed (score 1.0) if date within tolerance
  B  exact amount + date within tolerance          → confirmed only if the pairing is unique on both sides
                                                     and the score ≥ auto-confirm threshold, else suggested
  C  reference match with amount difference        → suggested + amount_mismatch exception
  D  amount within tolerance + date within window  → suggested (partial match)
Remaining items become unmatched exceptions.
"""
from __future__ import annotations

import difflib
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

ENGINE_VERSION = "1.2.0"
EPS = Decimal("0.005")


@dataclass(frozen=True)
class Rules:
    date_tolerance_days: int = 3
    amount_tolerance_abs: Decimal = Decimal("0.00")
    amount_tolerance_pct: Decimal = Decimal("0.0")
    auto_confirm_threshold: Decimal = Decimal("0.90")
    balance_tolerance: Decimal = Decimal("0.01")
    match_references: bool = True

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> Rules:
        d = d or {}
        return cls(
            date_tolerance_days=int(d.get("date_tolerance_days", 3)),
            amount_tolerance_abs=Decimal(str(d.get("amount_tolerance_abs", "0"))),
            amount_tolerance_pct=Decimal(str(d.get("amount_tolerance_pct", "0"))),
            auto_confirm_threshold=Decimal(str(d.get("auto_confirm_threshold", "0.90"))),
            balance_tolerance=Decimal(str(d.get("balance_tolerance", "0.01"))),
            match_references=bool(d.get("match_references", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"date_tolerance_days": self.date_tolerance_days, "amount_tolerance_abs": str(self.amount_tolerance_abs),
                "amount_tolerance_pct": str(self.amount_tolerance_pct),
                "auto_confirm_threshold": str(self.auto_confirm_threshold),
                "balance_tolerance": str(self.balance_tolerance), "match_references": self.match_references}


@dataclass(frozen=True)
class BankItem:
    id: uuid.UUID
    date: date
    amount: Decimal
    description: str
    reference: str | None = None
    running_balance: Decimal | None = None
    line: int | None = None


@dataclass(frozen=True)
class BookItem:
    id: uuid.UUID
    date: date
    amount: Decimal
    description: str
    reference: str | None = None


@dataclass
class MatchOut:
    bank_id: uuid.UUID
    book_id: uuid.UUID
    match_type: str
    status: str
    score: Decimal
    amount_difference: Decimal
    date_difference_days: int
    reasons: list[str]


@dataclass
class ExceptionOut:
    exception_type: str
    severity: str
    message: str
    bank_id: uuid.UUID | None = None
    book_id: uuid.UUID | None = None
    amount: Decimal | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReconResult:
    matches: list[MatchOut] = field(default_factory=list)
    exceptions: list[ExceptionOut] = field(default_factory=list)
    expected_closing: Decimal | None = None
    balance_difference: Decimal | None = None
    unmatched_bank: list[uuid.UUID] = field(default_factory=list)
    unmatched_book: list[uuid.UUID] = field(default_factory=list)

    @property
    def confirmed(self) -> int:
        return sum(1 for m in self.matches if m.status in ("confirmed", "manual"))

    @property
    def suggested(self) -> int:
        return sum(1 for m in self.matches if m.status == "suggested")

    @property
    def has_critical(self) -> bool:
        return any(e.severity == "critical" for e in self.exceptions)


def _ref(r: str | None) -> str:
    return re.sub(r"\s+", "", (r or "").upper())


def _sim(a: str, b: str) -> Decimal:
    na, nb = re.sub(r"[^a-z0-9 ]", "", a.lower()), re.sub(r"[^a-z0-9 ]", "", b.lower())
    if not na or not nb:
        return Decimal("0")
    return Decimal(str(round(difflib.SequenceMatcher(None, na, nb).ratio(), 4)))


def _amount_ok(a: Decimal, b: Decimal, rules: Rules) -> bool:
    diff = abs(a - b)
    if diff <= EPS:
        return True
    if (a > 0) != (b > 0):
        return False
    limit = max(rules.amount_tolerance_abs, abs(b) * rules.amount_tolerance_pct / Decimal(100))
    return diff <= limit + EPS


def reconcile(bank: list[BankItem], book: list[BookItem], rules: Rules, *, opening: Decimal | None = None,
              closing: Decimal | None = None, expected_count: int | None = None,
              pre_matched: list[tuple[uuid.UUID, uuid.UUID]] | None = None,
              external_closing: tuple[Decimal, str] | None = None,
              book_period: tuple[date, date] | None = None) -> ReconResult:
    """``book_period``: book entries dated outside it are tolerance padding — usable as match
    candidates but never reported as unmatched (they belong to a neighbouring period)."""
    res = ReconResult()
    bank = sorted(bank, key=lambda b: (b.date, b.line or 0, str(b.id)))
    book = sorted(book, key=lambda b: (b.date, str(b.id)))

    # ---- balance verification -------------------------------------------------
    total = sum((b.amount for b in bank), Decimal(0))
    if opening is not None:
        res.expected_closing = opening + total
        if closing is not None:
            res.balance_difference = (closing - res.expected_closing).quantize(Decimal("0.0001"))
            if abs(res.balance_difference) > rules.balance_tolerance:
                res.exceptions.append(ExceptionOut(
                    "balance_mismatch", "critical",
                    f"Opening {opening:,.2f} + movements {total:,.2f} = {res.expected_closing:,.2f}, "
                    f"but closing balance is {closing:,.2f}", amount=res.balance_difference,
                    details={"opening": str(opening), "movements": str(total), "closing": str(closing)}))
    if closing is not None and external_closing is not None:
        ext_amount, ext_label = external_closing
        if abs(ext_amount - closing) > rules.balance_tolerance:
            res.exceptions.append(ExceptionOut(
                "balance_mismatch", "warning",
                f"Statement closing {closing:,.2f} differs from {ext_label} {ext_amount:,.2f}",
                amount=(closing - ext_amount), details={"source": ext_label}))

    if expected_count is not None and expected_count != len(bank):
        res.exceptions.append(ExceptionOut("count_mismatch", "warning",
                                           f"Statement lists {expected_count} transactions but {len(bank)} are stored",
                                           details={"expected": expected_count, "actual": len(bank)}))

    # running balance consistency
    prev = opening
    for b in bank:
        if b.running_balance is not None:
            if prev is not None and abs(prev + b.amount - b.running_balance) > EPS:
                res.exceptions.append(ExceptionOut(
                    "running_balance_break", "warning",
                    f"Running balance {b.running_balance:,.2f} does not follow from previous balance "
                    f"{prev:,.2f} and amount {b.amount:,.2f}", bank_id=b.id, amount=b.amount))
            prev = b.running_balance
        elif prev is not None:
            prev = prev + b.amount

    # duplicate references within the bank side
    groups: dict[tuple, list[BankItem]] = defaultdict(list)
    for b in bank:
        if _ref(b.reference):
            groups[(b.date, b.amount, _ref(b.reference))].append(b)
    for (d, amt, ref), items in groups.items():
        if len(items) > 1:
            for dup in items[1:]:
                res.exceptions.append(ExceptionOut("duplicate_reference", "warning",
                                                   f"Reference {ref} appears {len(items)} times on {d} for {amt:,.2f}",
                                                   bank_id=dup.id, amount=amt))

    # ---- matching -------------------------------------------------------------
    free_bank: dict[uuid.UUID, BankItem] = {b.id: b for b in bank}
    free_book: dict[uuid.UUID, BookItem] = {b.id: b for b in book}
    tol = rules.date_tolerance_days

    for bank_id, book_id in pre_matched or []:
        if bank_id in free_bank and book_id in free_book:
            bi, ki = free_bank.pop(bank_id), free_book.pop(book_id)
            res.matches.append(MatchOut(bank_id, book_id, "manual", "manual", Decimal("1"),
                                        bi.amount - ki.amount, abs((bi.date - ki.date).days),
                                        ["carried forward from a previous manual decision"]))

    def take(bi: BankItem, ki: BookItem, mtype: str, status: str, score: Decimal, reasons: list[str]) -> None:
        free_bank.pop(bi.id, None)
        free_book.pop(ki.id, None)
        res.matches.append(MatchOut(bi.id, ki.id, mtype, status, score.quantize(Decimal("0.0001")),
                                    (bi.amount - ki.amount).quantize(Decimal("0.0001")), abs((bi.date - ki.date).days),
                                    reasons))

    # Pass A — reference + exact amount
    if rules.match_references:
        by_ref: dict[str, list[BookItem]] = defaultdict(list)
        for k in book:
            if _ref(k.reference):
                by_ref[_ref(k.reference)].append(k)
        for bi in list(free_bank.values()):
            r = _ref(bi.reference)
            if not r:
                continue
            cands = [k for k in by_ref.get(r, []) if k.id in free_book and abs(k.amount - bi.amount) <= EPS]
            if not cands:
                continue
            k = min(cands, key=lambda c: (abs((c.date - bi.date).days), str(c.id)))
            dd = abs((k.date - bi.date).days)
            if dd <= tol:
                take(bi, k, "reference_amount", "confirmed", Decimal("1"), ["reference and amount identical"])
            else:
                take(bi, k, "reference_amount", "suggested", Decimal("0.75"),
                     ["reference and amount identical", f"dates differ by {dd} days (tolerance {tol})"])
                res.exceptions.append(ExceptionOut("date_discrepancy", "info",
                                                   f"Matched by reference but dates differ by {dd} days",
                                                   bank_id=bi.id, book_id=k.id))

    # Pass B — exact amount within date window
    def window(bi: BankItem) -> list[BookItem]:
        return [k for k in free_book.values() if abs(k.amount - bi.amount) <= EPS and abs((k.date - bi.date).days) <= tol]

    # Ambiguity is judged against the pool *before* this pass, so a pairing that is only
    # "unique" because an uncertain pairing removed its rival is never auto-confirmed.
    pool_book = list(free_book.values())
    pool_bank = list(free_bank.values())

    def initial_counts(bi: BankItem, k: BookItem) -> tuple[int, int]:
        nb = sum(1 for c in pool_book if abs(c.amount - bi.amount) <= EPS and abs((c.date - bi.date).days) <= tol)
        nr = sum(1 for b in pool_bank if abs(b.amount - k.amount) <= EPS and abs((b.date - k.date).days) <= tol)
        return nb, nr

    for bi in list(free_bank.values()):
        if bi.id not in free_bank:
            continue
        cands = window(bi)
        if not cands:
            continue
        scored = sorted(((Decimal("0.75") + Decimal("0.15") * _sim(bi.description, k.description)
                          + Decimal("0.10") * (Decimal(1) - Decimal(abs((k.date - bi.date).days)) / Decimal(tol + 1)),
                          k) for k in cands), key=lambda x: (-x[0], str(x[1].id)))
        score, k = scored[0]
        n_book, n_bank = initial_counts(bi, k)
        unique = len(cands) == 1 and n_book == 1 and n_bank == 1
        reasons = ["amount identical", f"date difference {abs((k.date - bi.date).days)} day(s)",
                   f"description similarity {_sim(bi.description, k.description):.2f}"]
        if unique and score >= rules.auto_confirm_threshold:
            take(bi, k, "amount_date", "confirmed", score, reasons)
        else:
            if not unique:
                reasons.append(f"ambiguous: {n_book} book / {n_bank} bank candidates")
            take(bi, k, "amount_date", "suggested", min(score, Decimal("0.89")), reasons)

    # Pass C — reference matches, amount differs
    if rules.match_references:
        for bi in list(free_bank.values()):
            r = _ref(bi.reference)
            if not r:
                continue
            cands = [k for k in free_book.values() if _ref(k.reference) == r and (k.amount > 0) == (bi.amount > 0)]
            if not cands:
                continue
            k = min(cands, key=lambda c: (abs(c.amount - bi.amount), str(c.id)))
            diff = bi.amount - k.amount
            take(bi, k, "reference_only", "suggested", Decimal("0.6"),
                 ["reference identical", f"amount differs by {diff:,.2f}"])
            res.exceptions.append(ExceptionOut("amount_mismatch", "warning",
                                               f"Reference {r} matches but amounts differ by {diff:,.2f}",
                                               bank_id=bi.id, book_id=k.id, amount=diff))

    # Pass D — amount within tolerance
    if rules.amount_tolerance_abs > 0 or rules.amount_tolerance_pct > 0:
        for bi in list(free_bank.values()):
            cands = [k for k in free_book.values()
                     if _amount_ok(bi.amount, k.amount, rules) and abs((k.date - bi.date).days) <= tol]
            if not cands:
                continue
            k = min(cands, key=lambda c: (abs(c.amount - bi.amount), abs((c.date - bi.date).days), str(c.id)))
            take(bi, k, "amount_tolerance", "suggested", Decimal("0.5"),
                 [f"amount within tolerance (difference {bi.amount - k.amount:,.2f})"])

    # ---- leftovers ----------------------------------------------------------
    if book:  # only meaningful when a book side exists
        for bi in free_bank.values():
            res.unmatched_bank.append(bi.id)
            res.exceptions.append(ExceptionOut("unmatched_bank", "warning",
                                               f"Bank transaction {bi.amount:,.2f} on {bi.date} has no book entry",
                                               bank_id=bi.id, amount=bi.amount))
    for ki in free_book.values():
        if book_period and not (book_period[0] <= ki.date <= book_period[1]):
            continue
        res.unmatched_book.append(ki.id)
        res.exceptions.append(ExceptionOut("unmatched_book", "warning",
                                           f"Book entry {ki.amount:,.2f} on {ki.date} not found on the bank side",
                                           book_id=ki.id, amount=ki.amount))
    return res
