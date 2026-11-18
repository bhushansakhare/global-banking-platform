"""Deterministic reconciliation engine."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal as D

from app.reconciliation.engine import BankItem, BookItem, Rules, reconcile


def bank(d, amt, desc="x", ref=None, bal=None):
    return BankItem(uuid.uuid4(), date(2025, 3, d), D(amt), desc, ref, D(bal) if bal else None)


def book(d, amt, desc="x", ref=None):
    return BookItem(uuid.uuid4(), date(2025, 3, d), D(amt), desc, ref)


def test_reference_and_amount_confirmed():
    b, k = bank(2, "100", ref="INV-1"), book(3, "100", ref="inv-1")
    r = reconcile([b], [k], Rules())
    assert len(r.matches) == 1 and r.matches[0].status == "confirmed" and r.matches[0].match_type == "reference_amount"
    assert not r.exceptions


def test_unique_amount_date_match_confirmed_when_descriptions_agree():
    b, k = bank(2, "-250", "Office rent March"), book(2, "-250", "Office rent March")
    r = reconcile([b], [k], Rules())
    assert r.matches[0].status == "confirmed" and r.matches[0].match_type == "amount_date"


def test_ambiguous_amount_never_auto_confirmed():
    bs = [bank(2, "-50", "Coffee"), bank(3, "-50", "Coffee")]
    ks = [book(2, "-50", "Coffee"), book(3, "-50", "Coffee")]
    r = reconcile(bs, ks, Rules())
    assert len(r.matches) == 2
    assert all(m.status == "suggested" for m in r.matches)
    assert all(m.score < D("0.9") for m in r.matches)


def test_reference_amount_mismatch_suggested_with_exception():
    b, k = bank(2, "100.00", ref="ABC"), book(2, "99.50", ref="ABC")
    r = reconcile([b], [k], Rules())
    assert r.matches[0].status == "suggested" and r.matches[0].match_type == "reference_only"
    assert any(e.exception_type == "amount_mismatch" for e in r.exceptions)


def test_amount_tolerance_partial_match():
    b, k = bank(2, "100.00", "Fee"), book(3, "100.40", "Fee")
    assert not reconcile([b], [k], Rules()).matches
    r = reconcile([b], [k], Rules(amount_tolerance_abs=D("0.50")))
    assert r.matches[0].status == "suggested" and r.matches[0].match_type == "amount_tolerance"


def test_date_outside_tolerance_unmatched():
    b, k = bank(1, "10"), book(20, "10")
    r = reconcile([b], [k], Rules(date_tolerance_days=3))
    assert not r.matches
    types = sorted(e.exception_type for e in r.exceptions)
    assert types == ["unmatched_bank", "unmatched_book"]


def test_balance_verification():
    bs = [bank(2, "500", bal="1500"), bank(3, "-200", bal="1300")]
    ok = reconcile(bs, [], Rules(), opening=D("1000"), closing=D("1300"))
    assert ok.balance_difference == D("0") and not ok.has_critical
    bad = reconcile(bs, [], Rules(), opening=D("1000"), closing=D("1350"))
    assert bad.has_critical and bad.balance_difference == D("50")


def test_running_balance_break_and_duplicates():
    bs = [bank(2, "500", ref="R1", bal="1500"), bank(2, "500", ref="R1", bal="2100")]
    r = reconcile(bs, [], Rules(), opening=D("1000"))
    kinds = {e.exception_type for e in r.exceptions}
    assert "running_balance_break" in kinds and "duplicate_reference" in kinds


def test_count_mismatch_and_external_balance():
    bs = [bank(2, "1")]
    r = reconcile(bs, [], Rules(), opening=D("0"), closing=D("1"), expected_count=2,
                  external_closing=(D("5"), "provider ledger"))
    kinds = {e.exception_type for e in r.exceptions}
    assert {"count_mismatch", "balance_mismatch"} <= kinds


def test_deterministic_output():
    ids = [uuid.UUID(int=i) for i in range(6)]
    bs = [BankItem(ids[i], date(2025, 3, 1 + i), D("-10"), "Same") for i in range(3)]
    ks = [BookItem(ids[3 + i], date(2025, 3, 1 + i), D("-10"), "Same") for i in range(3)]
    a = reconcile(bs, ks, Rules())
    b = reconcile(list(reversed(bs)), list(reversed(ks)), Rules())
    assert [(m.bank_id, m.book_id, m.status) for m in a.matches] == [(m.bank_id, m.book_id, m.status) for m in b.matches]


def test_tolerance_padding_not_reported_as_unmatched():
    b = bank(2, "50", "Fee")
    inside_unmatched = book(10, "-7", "Cheque")
    padding = BookItem(uuid.uuid4(), date(2025, 2, 27), D("999"), "Belongs to February")
    edge_match = BookItem(uuid.uuid4(), date(2025, 2, 28), D("50"), "Fee")
    r = reconcile([b], [inside_unmatched, padding, edge_match], Rules(),
                  book_period=(date(2025, 3, 1), date(2025, 3, 31)))
    assert r.matches[0].book_id == edge_match.id  # padding still usable as a candidate
    assert r.unmatched_book == [inside_unmatched.id]


def test_pre_matched_manual_carried_forward():
    b, k = bank(2, "10"), book(25, "99")
    r = reconcile([b], [k], Rules(), pre_matched=[(b.id, k.id)])
    assert r.matches[0].status == "manual" and not r.unmatched_bank
