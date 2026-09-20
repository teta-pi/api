import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(255), default="PI Camera")
    device_fingerprint: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    device_public_key: Mapped[str] = mapped_column(Text, nullable=False)
    # NULL once revoked (migration 015) — the secret is erased, not just flagged.
    api_key: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Set by owner (DELETE /devices/{id}), the device itself
    # (POST /devices/self-revoke) or an admin (DELETE /admin/devices/{id}).
    # A revoked device's X-Device-Api-Key is rejected with 401; re-pairing the
    # same fingerprint via /devices/register mints a fresh key and clears this.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
