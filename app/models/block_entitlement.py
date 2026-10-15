import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# Pack sizes for the one-time purchasable grants (owner, 2026-10-15: $7/$49/$149).
# 'included' and 'admin' are not fixed-size packs — granted_blocks is set directly.
PACK_SIZES = {"pack_10": 10, "pack_100": 100, "pack_1000": 1000}
GRANT_SOURCES = {"included", "pack_10", "pack_100", "pack_1000", "admin"}


class BlockEntitlement(Base):
    """Append-only ledger of block-allowance grants (1.30).

    Same append-only discipline as verification_events/admin_audit_log: DELETE
    and UPDATE are blocked by a DB trigger (migration 017). A correction is a
    new compensating row, never an edit to an existing one. Grants never
    expire (the packs are one-time-forever) — there is no expires_at column.
    Effective allowance for a business = settings.included_block_allowance +
    sum(granted_blocks) over its rows.
    """

    __tablename__ = "block_entitlements"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id"), nullable=False
    )
    granted_blocks: Mapped[int] = mapped_column(Integer, nullable=False)
    # 'included' | 'pack_10' | 'pack_100' | 'pack_1000' | 'admin'
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    # who/what granted it: a user id, 'system', or an admin's email
    granted_by: Mapped[str] = mapped_column(String(320), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
