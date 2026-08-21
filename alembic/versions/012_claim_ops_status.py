"""claims.ops_status (backoffice outreach tracking, roadmap 11.2)

Adds an operator-facing status to waitlist claims so the /admin Claims tab
can track outreach without touching the legacy `ready_to_pay` field (retired
by 3.5 — new claims never set it). Allowed values (enforced in the API, not
a DB constraint, matching the ready_to_pay/entity_type precedent in this
table): contacted, converted, rejected. NULL means "new / untouched".

Revision ID: 012
Revises: 011
Create Date: 2026-08-21
"""

import sqlalchemy as sa
from alembic import op

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("claims", sa.Column("ops_status", sa.String(20), nullable=True))
    op.add_column("claims", sa.Column("ops_status_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_claims_ops_status", "claims", ["ops_status"])


def downgrade() -> None:
    op.drop_index("ix_claims_ops_status", table_name="claims")
    op.drop_column("claims", "ops_status_updated_at")
    op.drop_column("claims", "ops_status")
