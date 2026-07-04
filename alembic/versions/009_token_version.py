"""users.token_version — "log out everywhere" support

Revision ID: 009
Revises: 008
Create Date: 2026-07-04
"""

import sqlalchemy as sa
from alembic import op

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("users", "token_version")
