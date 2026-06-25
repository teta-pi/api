"""FK indexes, registry self_asserted status, trgm search indexes

Revision ID: 004
Revises: 003
Create Date: 2026-06-25
"""

from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # FK indexes — critical for JOIN performance
    op.create_index("ix_businesses_owner_id", "businesses", ["owner_id"])
    op.create_index("ix_blocks_business_id", "blocks", ["business_id"])
    op.create_index("ix_media_block_id", "media", ["block_id"])
    op.create_index("ix_devices_business_id", "devices", ["business_id"])

    # Filter indexes for common query patterns
    op.create_index(
        "ix_businesses_published_public",
        "businesses",
        ["is_published", "is_public"],
    )
    op.create_index(
        "ix_businesses_verification_level",
        "businesses",
        ["verification_level"],
    )
    op.create_index(
        "ix_businesses_registry_status",
        "businesses",
        ["registry_status"],
    )

    # Trigram index for name search (enables fast LIKE / ILIKE)
    op.execute(
        "CREATE INDEX ix_businesses_name_trgm ON businesses "
        "USING gin (name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_businesses_slug_trgm ON businesses "
        "USING gin (slug gin_trgm_ops)"
    )

    # Partial index: only active, published, public entities for search
    op.execute(
        "CREATE INDEX ix_businesses_search ON businesses (name, entity_type, country) "
        "WHERE is_published = true AND is_public = true"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_businesses_search")
    op.execute("DROP INDEX IF EXISTS ix_businesses_slug_trgm")
    op.execute("DROP INDEX IF EXISTS ix_businesses_name_trgm")
    op.drop_index("ix_businesses_registry_status", table_name="businesses")
    op.drop_index("ix_businesses_verification_level", table_name="businesses")
    op.drop_index("ix_businesses_published_public", table_name="businesses")
    op.drop_index("ix_devices_business_id", table_name="devices")
    op.drop_index("ix_media_block_id", table_name="media")
    op.drop_index("ix_blocks_business_id", table_name="blocks")
    op.drop_index("ix_businesses_owner_id", table_name="businesses")
