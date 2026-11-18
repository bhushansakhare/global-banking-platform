"""Rule-based transaction categorisation (keyword rules per tenant, deterministic)."""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.transactions import TransactionCategory

DEFAULT_CATEGORIES: list[dict] = [
    {"name": "Customer receipts", "colour": "#97AC3B", "direction": "credit", "priority": 10,
     "keywords": ["customer", "invoice", "receipt", "payment received", "neft cr", "imps cr", "upi cr"]},
    {"name": "Card settlements", "colour": "#0EA5E9", "direction": "credit", "priority": 20,
     "keywords": ["card settlement", "merchant settlement", "pos settlement"]},
    {"name": "Interest", "colour": "#14B8A6", "direction": "credit", "priority": 30, "keywords": ["interest"]},
    {"name": "Refunds", "colour": "#22C55E", "direction": "credit", "priority": 40, "keywords": ["refund", "reversal"]},
    {"name": "Payroll", "colour": "#2B296E", "direction": "debit", "priority": 10,
     "keywords": ["payroll", "salary", "wages"]},
    {"name": "Suppliers", "colour": "#6366F1", "direction": "debit", "priority": 20,
     "keywords": ["supplier", "vendor", "purchase order"]},
    {"name": "Rent & premises", "colour": "#8B5CF6", "direction": "debit", "priority": 30,
     "keywords": ["rent", "lease", "premises"]},
    {"name": "Utilities", "colour": "#F59E0B", "direction": "debit", "priority": 40,
     "keywords": ["utilities", "electric", "water", "gas", "broadband", "telecom"]},
    {"name": "Bank charges", "colour": "#64748B", "direction": "debit", "priority": 5,
     "keywords": ["bank charge", "charges", "fee", "commission", "gst on charges"]},
    {"name": "Taxes", "colour": "#EC4899", "direction": "debit", "priority": 15,
     "keywords": ["tax", "hmrc", "irs", "gst", "vat", "tds"]},
    {"name": "Software & subscriptions", "colour": "#06B6D4", "direction": "debit", "priority": 50,
     "keywords": ["subscription", "software", "saas", "licence", "license"]},
    {"name": "Travel", "colour": "#F97316", "direction": "debit", "priority": 60,
     "keywords": ["travel", "airline", "hotel", "uber", "taxi", "rail"]},
    {"name": "Insurance", "colour": "#A855F7", "direction": "debit", "priority": 60, "keywords": ["insurance"]},
    {"name": "Card purchases", "colour": "#0284C7", "direction": "debit", "priority": 90,
     "keywords": ["card purchase", "pos ", "debit card"]},
    {"name": "Transfers", "colour": "#94A3B8", "direction": "any", "priority": 95,
     "keywords": ["transfer", "trf", "sweep"]},
]


def ensure_default_categories(db: Session, tenant_id: uuid.UUID) -> None:
    existing = set(db.scalars(select(TransactionCategory.name).where(TransactionCategory.tenant_id == tenant_id)))
    for c in DEFAULT_CATEGORIES:
        if c["name"] not in existing:
            db.add(TransactionCategory(tenant_id=tenant_id, **c))
    db.flush()


class Categorizer:
    def __init__(self, db: Session, tenant_id: uuid.UUID) -> None:
        self.rules = sorted(
            db.scalars(select(TransactionCategory).where(TransactionCategory.tenant_id == tenant_id)).all(),
            key=lambda c: c.priority,
        )

    def categorise(self, description: str, amount: Decimal) -> uuid.UUID | None:
        text = (description or "").lower()
        direction = "credit" if amount > 0 else "debit"
        for rule in self.rules:
            if rule.direction not in ("any", direction):
                continue
            if any(k.lower() in text for k in rule.keywords):
                return rule.id
        return None
