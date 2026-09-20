"""devices.revoked_at + nullable api_key — Pi CAM device key revocation (1.25)

Closes known-issues 6.6b / security S-21: once a Pi CAM was paired its
X-Device-Api-Key was valid forever — no owner, device or admin path could
kill it. Revocation keeps the row (media rows still point at the device's
entity, and the fingerprint must stay unique so a re-pair of the same phone
is idempotent) but stamps `revoked_at` and NULLs `api_key`, so the leaked
secret no longer exists anywhere in the DB — `_get_device` cannot match it
even if a future filter regresses.

Revision ID: 015
Revises: 014
Create Date: 2026-09-20
"""

import sqlalchemy as sa

from alembic import op

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "devices",
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Unique index stays: Postgres treats NULLs as distinct, so any number of
    # revoked (api_key IS NULL) rows coexist.
    op.alter_column("devices", "api_key", existing_type=sa.String(255), nullable=True)


def downgrade() -> None:
    # Revoked rows have no key to restore — drop them rather than fail the
    # NOT NULL re-apply (their media keeps pointing at the entity via blocks).
    op.execute("DELETE FROM devices WHERE api_key IS NULL")
    op.alter_column("devices", "api_key", existing_type=sa.String(255), nullable=False)
    op.drop_column("devices", "revoked_at")
