"""audit log immutability trigger

Revision ID: a1b2c3d4e5f6
Revises: d0253a2ef9b5
Create Date: 2026-11-16
"""
from typing import Sequence, Union

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "d0253a2ef9b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION gbp_audit_logs_immutable() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_logs is append-only (% blocked)', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_logs_immutable
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION gbp_audit_logs_immutable();
        """
    )
    # Original bank transaction values are immutable once inserted (only workflow columns may change).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION gbp_transactions_preserve_original() RETURNS trigger AS $$
        BEGIN
            IF NEW.booking_date IS DISTINCT FROM OLD.booking_date
               OR NEW.amount IS DISTINCT FROM OLD.amount
               OR NEW.debit IS DISTINCT FROM OLD.debit
               OR NEW.credit IS DISTINCT FROM OLD.credit
               OR NEW.description IS DISTINCT FROM OLD.description
               OR NEW.reference IS DISTINCT FROM OLD.reference
               OR NEW.running_balance IS DISTINCT FROM OLD.running_balance
               OR NEW.raw_data IS DISTINCT FROM OLD.raw_data
               OR NEW.fingerprint IS DISTINCT FROM OLD.fingerprint THEN
                RAISE EXCEPTION 'original bank transaction values are immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_transactions_preserve_original
        BEFORE UPDATE ON transactions
        FOR EACH ROW EXECUTE FUNCTION gbp_transactions_preserve_original();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_transactions_preserve_original ON transactions")
    op.execute("DROP FUNCTION IF EXISTS gbp_transactions_preserve_original()")
    op.execute("DROP TRIGGER IF EXISTS trg_audit_logs_immutable ON audit_logs")
    op.execute("DROP FUNCTION IF EXISTS gbp_audit_logs_immutable()")
