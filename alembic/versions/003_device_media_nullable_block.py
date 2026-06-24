"""device media nullable block_id

Revision ID: 003
Revises: 002
Create Date: 2026-06-24
"""

from alembic import op

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Allow media uploaded directly by Pi CAM devices (no block context yet)
    op.alter_column("media", "block_id", nullable=True)


def downgrade() -> None:
    op.alter_column("media", "block_id", nullable=False)
