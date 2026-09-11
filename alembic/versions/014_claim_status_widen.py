"""businesses.claim_status VARCHAR(20) -> VARCHAR(30) (hotfix)

013 sized claim_status as VARCHAR(20), but "pre_verified_unclaimed" is 22
characters — every POST /admin/entities/bulk-preverify INSERT 500s on
prod with StringDataRightTruncationError. Widened to 30 (longest current
value is "pre_verified_unclaimed" at 22; the other three are
self_registered=15, claimed=7, opted_out=9) — room for future statuses
without another emergency migration.

Revision ID: 014
Revises: 013
Create Date: 2026-09-11
"""

import sqlalchemy as sa
from alembic import op

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "businesses", "claim_status",
        type_=sa.String(30),
        existing_type=sa.String(20),
        existing_server_default="self_registered",
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "businesses", "claim_status",
        type_=sa.String(20),
        existing_type=sa.String(30),
        existing_server_default="self_registered",
        existing_nullable=False,
    )
