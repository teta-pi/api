"""Widen users.full_name to Text for Fernet ciphertext (Back Office A3)

Revision ID: 008
Revises: 007
Create Date: 2026-07-04
"""

import sqlalchemy as sa
from alembic import op

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("users", "full_name", type_=sa.Text, existing_type=sa.String(255))


def downgrade() -> None:
    op.alter_column("users", "full_name", type_=sa.String(255), existing_type=sa.Text)
