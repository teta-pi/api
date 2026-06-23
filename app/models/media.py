import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, LargeBinary, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Media(Base):
    __tablename__ = "media"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    block_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("blocks.id"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(20), nullable=False)  # video | photo | file
    storage_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    original_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    c2pa_manifest: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    c2pa_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    c2pa_signer: Mapped[str | None] = mapped_column(String(255), nullable=True)

    bitcoin_proof: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    bitcoin_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    bitcoin_block: Mapped[int | None] = mapped_column(Integer, nullable=True)

    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    block: Mapped["Block"] = relationship(back_populates="media")  # noqa: F821
