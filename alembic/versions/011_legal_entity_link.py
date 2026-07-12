"""businesses.legal_entity_id (brand -> verified legal entity link)

Verification rework (docs/verification-rework.md §4): business entity creation
is decoupled from registry matching; a brand can instead link to a verified
legal entity (e.g. "Google" brand -> "Alphabet Inc." legal entity).

`verification_events.event_type` gains three new allowed values
(email_verified, domain_verified, document_verified) per the same spec, but
the column has always been a plain String(50) with no DB-level enum/check
constraint (see 006) — the allowed-values list is documented in the model
only, so no schema change is required for that part. The append-only trigger
from 006 is untouched by this migration; verified below in upgrade().

Revision ID: 011
Revises: 010
Create Date: 2026-07-12
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "businesses",
        sa.Column("legal_entity_id", UUID(as_uuid=True), sa.ForeignKey("businesses.id"), nullable=True),
    )
    op.create_index("ix_businesses_legal_entity_id", "businesses", ["legal_entity_id"])

    # Append-only guarantee on verification_events is untouched by this
    # migration (no DDL against that table). Assert the trigger from 006 is
    # still attached so a future migration can't silently drop it.
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_trigger
            WHERE tgname = 'trg_verification_events_append_only'
              AND tgrelid = 'verification_events'::regclass
          ) THEN
            RAISE EXCEPTION 'verification_events append-only trigger missing — refusing migration';
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.drop_index("ix_businesses_legal_entity_id", table_name="businesses")
    op.drop_column("businesses", "legal_entity_id")
