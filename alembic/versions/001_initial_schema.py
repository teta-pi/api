"""Initial schema with pgvector

Revision ID: 001
Revises:
Create Date: 2026-06-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), unique=True, nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=True),
        sa.Column("full_name", sa.String(255), nullable=True),
        sa.Column("auth_provider", sa.String(50), nullable=False, server_default="email"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("is_agent", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("api_key", sa.String(255), unique=True, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "businesses",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), unique=True, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("country", sa.String(2), nullable=True),
        sa.Column("registry_id", sa.String(255), nullable=True),
        sa.Column("registry_status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("registry_data", JSONB, nullable=True),
        sa.Column("verification_level", sa.String(50), nullable=False, server_default="none"),
        sa.Column("ai_categories", JSONB, nullable=True),
        sa.Column("embedding", sa.Text, nullable=True),  # Replaced with vector(1536) below
        sa.Column("is_published", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    # Replace text column with actual pgvector type
    op.execute("ALTER TABLE businesses DROP COLUMN embedding")
    op.execute("ALTER TABLE businesses ADD COLUMN embedding vector(1536)")

    op.create_index("ix_businesses_slug", "businesses", ["slug"])
    op.execute(
        "CREATE INDEX ix_businesses_embedding ON businesses USING ivfflat (embedding vector_cosine_ops)"
    )

    op.create_table(
        "blocks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", UUID(as_uuid=True), sa.ForeignKey("businesses.id"), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("verification_status", sa.String(50), nullable=False, server_default="unverified"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "media",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("block_id", UUID(as_uuid=True), sa.ForeignKey("blocks.id"), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("storage_url", sa.String(1024), nullable=False),
        sa.Column("original_hash", sa.String(64), nullable=True),
        sa.Column("c2pa_manifest", JSONB, nullable=True),
        sa.Column("c2pa_verified", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("c2pa_signer", sa.String(255), nullable=True),
        sa.Column("bitcoin_proof", sa.LargeBinary, nullable=True),
        sa.Column("bitcoin_confirmed", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("bitcoin_block", sa.Integer, nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "devices",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", UUID(as_uuid=True), sa.ForeignKey("businesses.id"), nullable=False),
        sa.Column("label", sa.String(255), nullable=False, server_default="PI Camera"),
        sa.Column("device_fingerprint", sa.String(512), unique=True, nullable=False),
        sa.Column("device_public_key", sa.Text, nullable=False),
        sa.Column("api_key", sa.String(255), unique=True, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("registered_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("devices")
    op.drop_table("media")
    op.drop_table("blocks")
    op.drop_table("businesses")
    op.drop_table("users")
