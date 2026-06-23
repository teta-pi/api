"""Entity type, privacy flags, agent endpoint fields

Revision ID: 002
Revises: 001
Create Date: 2026-06-23
"""

from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # businesses: entity_type, is_public, agent_endpoint
    op.add_column("businesses", sa.Column(
        "entity_type", sa.String(50), nullable=False, server_default="business"
    ))
    op.add_column("businesses", sa.Column(
        "is_public", sa.Boolean, nullable=False, server_default="true"
    ))
    op.add_column("businesses", sa.Column(
        "agent_endpoint", sa.String(500), nullable=True
    ))
    op.add_column("businesses", sa.Column(
        "agent_endpoint_verified", sa.Boolean, nullable=False, server_default="false"
    ))
    op.create_index("ix_businesses_entity_type", "businesses", ["entity_type"])

    # blocks: is_public
    op.add_column("blocks", sa.Column(
        "is_public", sa.Boolean, nullable=False, server_default="true"
    ))


def downgrade() -> None:
    op.drop_column("blocks", "is_public")
    op.drop_index("ix_businesses_entity_type", table_name="businesses")
    op.drop_column("businesses", "agent_endpoint_verified")
    op.drop_column("businesses", "agent_endpoint")
    op.drop_column("businesses", "is_public")
    op.drop_column("businesses", "entity_type")
