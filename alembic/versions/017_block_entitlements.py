"""block_entitlements — append-only block-allowance grants (1.30)

Model: "everything free, you pay only for extra blocks" (10 included +
one-time purchasable packs). Effective allowance = settings.
included_block_allowance + sum(granted_blocks) for a business. Grants never
expire and are never edited — a correction is a new compensating row, same
append-only discipline as verification_events (006) / admin_audit_log (007).

Revision ID: 017
Revises: 016
Create Date: 2026-10-15
"""

import sqlalchemy as sa

from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "block_entitlements",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("business_id", UUID(as_uuid=True), sa.ForeignKey("businesses.id"), nullable=False),
        sa.Column("granted_blocks", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("granted_by", sa.String(320), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_block_entitlements_business", "block_entitlements", ["business_id"])

    # Append-only: no UPDATE, no DELETE — ever (same pattern as admin_audit_log, 007)
    op.execute(
        """
        CREATE OR REPLACE FUNCTION block_entitlements_append_only()
        RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'block_entitlements is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_block_entitlements_append_only
        BEFORE UPDATE OR DELETE ON block_entitlements
        FOR EACH ROW EXECUTE FUNCTION block_entitlements_append_only()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_block_entitlements_append_only ON block_entitlements")
    op.execute("DROP FUNCTION IF EXISTS block_entitlements_append_only")
    op.drop_index("ix_block_entitlements_business", table_name="block_entitlements")
    op.drop_table("block_entitlements")
