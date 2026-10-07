"""media.device_signature_verified — real device-key ECDSA signal (1.29)

14.12 (pi-cam) started sending content_signature/signature_alg; 1.29 verifies
it server-side against devices.device_public_key. This is a signal
independent of c2pa_verified (which stays gated off — known-issues §6.8,
security.md S-26 — until real C2PA manifest verification exists).

Revision ID: 016
Revises: 015
Create Date: 2026-10-06
"""

import sqlalchemy as sa

from alembic import op

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "media",
        sa.Column(
            "device_signature_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("media", "device_signature_verified")
