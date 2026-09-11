"""businesses.claim_status + pre_verified_source (roadmap 1.11, bulk pre-verification import)

GTM Phase 2 hard blocker: bulk-imported profiles (from public GitHub org /
domain / npm package metadata, see routes/admin.py::bulk_preverify_entities)
must be explicitly, durably distinguishable from a normal self-claimed
entity — both in the DB and on the public /e/[slug] page. `claim_status` is
that flag; `pre_verified_source` records what public data it was built from
and the opt-out token, so a later claim (routes/businesses.py::claim_*) can
verify against the same anchor and the public page can show provenance.

Revision ID: 013
Revises: 012
Create Date: 2026-09-11
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # self_registered (normal /businesses POST) | pre_verified_unclaimed
    # (bulk admin import, owner is the system import account) | claimed
    # (real owner completed domain-ownership claim) | opted_out (owner
    # requested removal via the one-click opt-out link — unpublished, kept
    # for audit, not deleted).
    op.add_column(
        "businesses",
        sa.Column("claim_status", sa.String(20), nullable=False, server_default="self_registered"),
    )
    op.create_index("ix_businesses_claim_status", "businesses", ["claim_status"])

    # {github_org?, domain?, npm_package?, pulled_from, imported_at,
    #  opt_out_token} — only set for claim_status='pre_verified_unclaimed'
    # rows (and kept afterwards for provenance once claimed/opted_out).
    op.add_column("businesses", sa.Column("pre_verified_source", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("businesses", "pre_verified_source")
    op.drop_index("ix_businesses_claim_status", table_name="businesses")
    op.drop_column("businesses", "claim_status")
