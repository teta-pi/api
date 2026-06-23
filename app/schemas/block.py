import uuid
from datetime import datetime

from pydantic import BaseModel


class BlockCreate(BaseModel):
    title: str
    description: str | None = None
    order: int = 0


class BlockUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    order: int | None = None


class BlockReorder(BaseModel):
    block_ids: list[uuid.UUID]


class MediaOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    type: str
    storage_url: str
    c2pa_verified: bool
    c2pa_signer: str | None
    bitcoin_confirmed: bool
    bitcoin_block: int | None
    uploaded_at: datetime


class BlockOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    business_id: uuid.UUID
    title: str
    description: str | None
    order: int
    verification_status: str
    media: list[MediaOut]
    created_at: datetime
