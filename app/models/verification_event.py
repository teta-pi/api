import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class VerificationEvent(Base):
    """Temporal Moat — append-only chronology of verification actions (SystemSpec v2.1 §02).

    Application-level rule: INSERT + SELECT only. The only permitted UPDATE is the
    OTS lifecycle job advancing ots_status/ots_proof/btc_block — enforced by a DB
    trigger (see migration 006), since app and worker share one DB role.
    """

    __tablename__ = "verification_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id"), nullable=False
    )
    # 'registered' | 'level_up' | 'block_signed' | 'endpoint_verified' | 'reverified'
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)  # 1 | 2 | 3 at time of event
    # 'official_registry' | 'c2pa_camera' | 'linked_account' | 'self_declared'
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="self_declared")
    payload_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)  # sha256 of canonical payload
    ots_proof: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # 'pending' -> 'anchored' -> 'confirmed'
    ots_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    btc_block: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
