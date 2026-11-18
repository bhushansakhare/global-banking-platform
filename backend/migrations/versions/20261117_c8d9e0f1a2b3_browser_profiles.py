"""Add optional browser binding without replacing any existing account or connection."""
from alembic import op
import sqlalchemy as sa

revision = "c8d9e0f1a2b3"
down_revision = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("bank_accounts", sa.Column("browser_profile_key", sa.String(60), nullable=True))
    op.add_column("bank_accounts", sa.Column("browser_identifier_encrypted", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("bank_accounts", "browser_identifier_encrypted")
    op.drop_column("bank_accounts", "browser_profile_key")
