"""Entity model extension + Temporal Moat + TWIRA base (SystemSpec v2.1 S1-S3)

- businesses: segment, t_score, p_score
- blocks: c2pa_manifest, ots_proof, embedding vector(1536) + HNSW index
- verification_events: append-only (trigger-enforced), OTS lifecycle fields
- endpoint_probes: uptime probe results

Revision ID: 006
Revises: 005
Create Date: 2026-07-03
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # businesses: segment + TWIRA stored scores
    op.add_column("businesses", sa.Column("segment", sa.String(20), nullable=False, server_default="operator"))
    op.add_column("businesses", sa.Column("t_score", sa.Float, nullable=False, server_default="0"))
    op.add_column("businesses", sa.Column("p_score", sa.Float, nullable=False, server_default="0"))

    # blocks: C2PA manifest, OTS proof, embedding
    op.add_column("blocks", sa.Column("c2pa_manifest", JSONB, nullable=True))
    op.add_column("blocks", sa.Column("ots_proof", sa.LargeBinary, nullable=True))
    op.execute("ALTER TABLE blocks ADD COLUMN embedding vector(1536)")
    op.execute("CREATE INDEX ix_blocks_embedding_hnsw ON blocks USING hnsw (embedding vector_cosine_ops)")

    # verification_events — the Temporal Moat
    op.create_table(
        "verification_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("entity_id", UUID(as_uuid=True), sa.ForeignKey("businesses.id"), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("level", sa.Integer, nullable=False),
        sa.Column("source", sa.String(50), nullable=False, server_default="self_declared"),
        sa.Column("payload_hash", sa.LargeBinary, nullable=False),
        sa.Column("ots_proof", sa.LargeBinary, nullable=True),
        sa.Column("ots_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("btc_block", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_verification_events_entity_created", "verification_events", ["entity_id", "created_at"])

    # Append-only enforcement. App and workers share one DB role, so instead of
    # REVOKE we use a trigger: DELETE always forbidden; UPDATE may only change
    # the OTS lifecycle columns (ots_proof, ots_status, btc_block).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION verification_events_append_only()
        RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'verification_events is append-only: DELETE forbidden';
          END IF;
          IF NEW.entity_id    IS DISTINCT FROM OLD.entity_id
             OR NEW.event_type   IS DISTINCT FROM OLD.event_type
             OR NEW.level        IS DISTINCT FROM OLD.level
             OR NEW.source       IS DISTINCT FROM OLD.source
             OR NEW.payload_hash IS DISTINCT FROM OLD.payload_hash
             OR NEW.created_at   IS DISTINCT FROM OLD.created_at
             OR NEW.id           IS DISTINCT FROM OLD.id THEN
            RAISE EXCEPTION 'verification_events is append-only: only OTS lifecycle columns may change';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_verification_events_append_only
        BEFORE UPDATE OR DELETE ON verification_events
        FOR EACH ROW EXECUTE FUNCTION verification_events_append_only()
        """
    )

    # endpoint_probes — uptime history for P component
    op.create_table(
        "endpoint_probes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("entity_id", UUID(as_uuid=True), sa.ForeignKey("businesses.id"), nullable=False),
        sa.Column("ok", sa.Boolean, nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_endpoint_probes_entity_at", "endpoint_probes", ["entity_id", "at"])


def downgrade() -> None:
    op.drop_table("endpoint_probes")
    op.execute("DROP TRIGGER IF EXISTS trg_verification_events_append_only ON verification_events")
    op.execute("DROP FUNCTION IF EXISTS verification_events_append_only")
    op.drop_table("verification_events")
    op.execute("DROP INDEX IF EXISTS ix_blocks_embedding_hnsw")
    op.drop_column("blocks", "embedding")
    op.drop_column("blocks", "ots_proof")
    op.drop_column("blocks", "c2pa_manifest")
    op.drop_column("businesses", "p_score")
    op.drop_column("businesses", "t_score")
    op.drop_column("businesses", "segment")
