"""Finzola snapshots, expectations, organization destinations and durable delivery history."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "d9e0f1a2b3c4"
down_revision = "c8d9e0f1a2b3"
branch_labels = None
depends_on = None


def common():
    return [sa.Column("id", UUID, primary_key=True),
            sa.Column("tenant_id", UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)]


def fk(name, table, nullable=True):
    return sa.Column(name, UUID, sa.ForeignKey(f"{table}.id"), nullable=nullable)


def upgrade():
    op.create_table("finzola_records", *common(), sa.Column("domain", sa.String(30), nullable=False),
        sa.Column("external_id", sa.String(200), nullable=False), sa.Column("generation", sa.String(36), nullable=False),
        sa.Column("data", JSONB, nullable=False), sa.UniqueConstraint("tenant_id", "domain", "external_id"))
    op.create_table("finzola_mappings", *common(), sa.Column("external_org_id", sa.String(120), nullable=False),
        fk("account_id", "bank_accounts", False), sa.Column("routing_field", sa.String(30), nullable=False),
        sa.Column("routing_value", sa.String(120), nullable=False), sa.Column("options", JSONB, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False), sa.Column("revision", sa.Integer, nullable=False),
        fk("authorized_by", "users", False), sa.UniqueConstraint("tenant_id", "external_org_id", "routing_field", "routing_value"))
    op.create_table("finzola_items", *common(), sa.Column("external_org_id", sa.String(120), nullable=False),
        sa.Column("external_id", sa.String(200), nullable=False), sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("expected", JSONB, nullable=False), sa.Column("status", sa.String(40), nullable=False),
        sa.Column("reason", sa.Text, nullable=False), sa.Column("decision", sa.String(30)),
        fk("mapping_id", "finzola_mappings"), fk("account_id", "bank_accounts"), fk("book_entry_id", "book_entries"),
        fk("session_id", "reconciliation_sessions"), fk("transaction_id", "transactions"),
        sa.UniqueConstraint("tenant_id", "external_org_id", "external_id"))
    op.create_table("finzola_destinations", *common(), sa.Column("external_org_id", sa.String(120), nullable=False),
        sa.Column("encrypted_key", sa.Text, nullable=False), sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False), sa.Column("verified_revision", sa.Integer),
        sa.Column("verified_at", sa.DateTime(timezone=True)), sa.Column("auto_delivery", sa.Boolean, nullable=False),
        fk("authorized_by", "users", False), sa.Column("last_error", sa.Text), sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("tenant_id", "external_org_id"), sa.UniqueConstraint("key_hash"))
    op.create_table("finzola_deliveries", *common(), fk("destination_id", "finzola_destinations", False),
        sa.Column("source_kind", sa.String(20), nullable=False), sa.Column("source_id", UUID, nullable=False),
        sa.Column("request_id", sa.String(200), nullable=False), sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("payload", JSONB, nullable=False), sa.Column("revision", sa.Integer, nullable=False),
        fk("authorized_by", "users", False), sa.Column("status", sa.String(30), nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False), sa.Column("acknowledgement", JSONB),
        sa.Column("error", sa.Text), sa.Column("job_id", UUID),
        sa.UniqueConstraint("tenant_id", "destination_id", "request_id", "payload_hash", "revision"))
    for table in ("finzola_records", "finzola_mappings", "finzola_items", "finzola_destinations", "finzola_deliveries"):
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])


def downgrade():
    for table in ("finzola_deliveries", "finzola_destinations", "finzola_items", "finzola_mappings", "finzola_records"):
        op.drop_table(table)
