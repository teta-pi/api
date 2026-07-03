import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Identity, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Claim(Base):
    """Waitlist claim — /claim IS the waitlist (LandingSpec v2.1 §02)."""

    __tablename__ = "claims"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    ready_to_pay: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Monotonic position in the waitlist, assigned by identity sequence
    position: Mapped[int] = mapped_column(BigInteger, Identity(start=1), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
