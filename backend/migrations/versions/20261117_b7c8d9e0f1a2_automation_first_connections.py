"""automation-first connections: per-account automation state, connection methods, balance verification

* bank_accounts: per-account pause, optional own schedule, attempted/successful sync timestamps, last error
* bank_connections: connection_method + environment (sandbox/production/demo/local)
* account_balances: verification_status
* automation_jobs: at most ONE active collect job per account (database-enforced)
* Data correction: accounts were marked "healthy" at creation without any verified sync. Health is
  reset to "unknown" (or "not_applicable" for manual-import-only accounts) — no account is deleted.

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f6
Create Date: 2026-11-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("bank_accounts", sa.Column("automation_paused", sa.Boolean(), nullable=False,
                                             server_default=sa.false()))
    op.add_column("bank_accounts", sa.Column("sync_schedule", sa.String(100), nullable=True))
    op.add_column("bank_accounts", sa.Column("next_sync_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("bank_accounts", sa.Column("last_attempted_sync_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("bank_accounts", sa.Column("last_successful_sync_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("bank_accounts", sa.Column("last_sync_error", sa.Text(), nullable=True))
    op.add_column("bank_accounts", sa.Column("last_sync_result", postgresql.JSONB(), nullable=True))

    op.add_column("bank_connections", sa.Column("connection_method", sa.String(40), nullable=True))
    op.add_column("bank_connections", sa.Column("environment", sa.String(20), nullable=True))

    op.add_column("account_balances", sa.Column("verification_status", sa.String(30), nullable=False,
                                                server_default="unverified"))

    # ---- data corrections (non-destructive) ------------------------------------------------------
    op.execute("""
        UPDATE bank_connections c SET connection_method = CASE p.provider_type
                WHEN 'manual' THEN 'manual_statement_import'
                WHEN 'demo' THEN 'demo'
                WHEN 'browser' THEN 'permitted_browser_automation'
                WHEN 'bank_api' THEN 'official_bank_api'
                ELSE 'authorised_data_provider' END,
            environment = CASE WHEN p.provider_type = 'demo' THEN 'demo'
                               WHEN p.provider_type = 'manual' THEN 'local' ELSE NULL END
          FROM bank_providers p WHERE p.id = c.provider_id
    """)
    op.execute("""
        UPDATE account_balances SET verification_status = CASE source
            WHEN 'provider' THEN 'provider_reported' WHEN 'statement' THEN 'statement_derived'
            WHEN 'demo' THEN 'demo' WHEN 'manual' THEN 'user_entered' ELSE 'unverified' END
    """)
    op.execute("UPDATE bank_accounts SET last_successful_sync_at = last_sync_at, "
               "last_attempted_sync_at = last_sync_at WHERE last_sync_at IS NOT NULL")
    # "healthy" requires a verified sync; manual-import-only accounts have no live connection to be healthy
    op.execute("""
        UPDATE bank_accounts a SET connection_health = 'not_applicable'
          FROM bank_connections c WHERE c.id = a.connection_id AND c.connection_method = 'manual_statement_import'
    """)
    op.execute("UPDATE bank_accounts SET connection_health = 'unknown' "
               "WHERE connection_health = 'healthy' AND last_successful_sync_at IS NULL")

    # cancel duplicate active collect jobs (keep the newest) before adding the guard
    op.execute("""
        UPDATE automation_jobs j SET status = 'cancelled', finished_at = now()
         WHERE j.job_type = 'collect_account'
           AND j.status IN ('queued','running','retry_scheduled','awaiting_user')
           AND EXISTS (SELECT 1 FROM automation_jobs k WHERE k.account_id = j.account_id
                         AND k.job_type = 'collect_account'
                         AND k.status IN ('queued','running','retry_scheduled','awaiting_user')
                         AND k.created_at > j.created_at)
    """)
    op.create_index("uq_automation_jobs_active_collect", "automation_jobs", ["account_id"], unique=True,
                    postgresql_where=sa.text("job_type = 'collect_account' AND status IN "
                                             "('queued','running','retry_scheduled','awaiting_user')"))


def downgrade() -> None:
    op.drop_index("uq_automation_jobs_active_collect", table_name="automation_jobs")
    op.drop_column("account_balances", "verification_status")
    op.drop_column("bank_connections", "environment")
    op.drop_column("bank_connections", "connection_method")
    for col in ("last_sync_result", "last_sync_error", "last_successful_sync_at", "last_attempted_sync_at",
                "next_sync_at", "sync_schedule", "automation_paused"):
        op.drop_column("bank_accounts", col)
