"""Claims table + claim_stats view — /claim IS the waitlist (LandingSpec/SystemSpec v2.1)

Revision ID: 005
Revises: 004
Create Date: 2026-07-03
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "claims",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("ready_to_pay", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("source", JSONB, nullable=True),
        sa.Column("position", sa.BigInteger, sa.Identity(start=1), unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_claims_email", "claims", ["email"])

    # Investor dashboard view (SystemSpec v2.1 §05)
    op.execute(
        """
        CREATE VIEW claim_stats AS
        SELECT count(*) AS total,
               count(*) FILTER (WHERE ready_to_pay) AS pay_ready,
               round(100.0 * count(*) FILTER (WHERE ready_to_pay) / nullif(count(*), 0), 1)
                 AS pay_ready_pct
        FROM claims
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS claim_stats")
    op.drop_index("ix_claims_email", table_name="claims")
    op.drop_table("claims")
