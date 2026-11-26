"""indexes on media.storage_url and users.avatar_url (1.35, security S-36)

`GET /media/local/{file_id}/{name}` now serves a file only while a row still
references its exact URL (app/services/media_files.is_referenced). That is one
equality lookup on each of these columns per request, so both get a btree.

Revision ID: 018
Revises: 017
Create Date: 2026-11-26
"""

from alembic import op

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_media_storage_url", "media", ["storage_url"])
    op.create_index("ix_users_avatar_url", "users", ["avatar_url"])


def downgrade() -> None:
    op.drop_index("ix_users_avatar_url", table_name="users")
    op.drop_index("ix_media_storage_url", table_name="media")
