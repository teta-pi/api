import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Business(Base):
    __tablename__ = "businesses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)  # ISO 3166-1 alpha-2

    registry_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    registry_status: Mapped[str] = mapped_column(
        String(50), default="pending"
    )  # pending | verified | failed | multiple_matches
    registry_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # none | registry | partial | full | live
    verification_level: Mapped[str] = mapped_column(String(50), default="none")
    ai_categories: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # pgvector column — added via migration after pgvector extension is enabled
    # embedding: vector(1536) — declared in migration, not here (SQLAlchemy pgvector support varies)

    is_published: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    owner: Mapped["User"] = relationship(back_populates="businesses")  # noqa: F821
    blocks: Mapped[list["Block"]] = relationship(  # noqa: F821
        back_populates="business", cascade="all, delete-orphan", order_by="Block.order"
    )
