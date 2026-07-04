"""User roles + admin audit log (Back Office A1)

Revision ID: 007
Revises: 006
Create Date: 2026-07-04
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None

# First admins: founder + operations agent
ADMIN_EMAILS = ("tetakta@gmail.com", "agent@tetapi.dev")


def upgrade() -> None:
    op.add_column("users", sa.Column("role", sa.String(20), nullable=False, server_default="user"))
    op.create_index("ix_users_role", "users", ["role"])

    op.create_table(
        "admin_audit_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("actor_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("actor_email", sa.String(320), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("target_type", sa.String(50), nullable=True),
        sa.Column("target_id", sa.String(100), nullable=True),
        sa.Column("detail", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_admin_audit_log_created", "admin_audit_log", ["created_at"])
    op.create_index("ix_admin_audit_log_actor", "admin_audit_log", ["actor_id"])

    # Append-only: no UPDATE, no DELETE — ever
    op.execute(
        """
        CREATE OR REPLACE FUNCTION admin_audit_log_append_only()
        RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'admin_audit_log is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_admin_audit_log_append_only
        BEFORE UPDATE OR DELETE ON admin_audit_log
        FOR EACH ROW EXECUTE FUNCTION admin_audit_log_append_only()
        """
    )

    # Promote initial admins (rows may not exist yet — that's fine)
    for email in ADMIN_EMAILS:
        op.execute(f"UPDATE users SET role = 'admin' WHERE email = '{email}'")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_admin_audit_log_append_only ON admin_audit_log")
    op.execute("DROP FUNCTION IF EXISTS admin_audit_log_append_only")
    op.drop_table("admin_audit_log")
    op.drop_index("ix_users_role", table_name="users")
    op.drop_column("users", "role")
