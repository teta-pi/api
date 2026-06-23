import uuid
from datetime import datetime

from pydantic import BaseModel


class MediaUploadResponse(BaseModel):
    media_id: uuid.UUID
    c2pa_verified: bool
    c2pa_signer: str | None
    bitcoin_status: str
    estimated_confirmation: str | None = "~60 minutes"


class MediaVerifyResponse(BaseModel):
    media_id: uuid.UUID
    c2pa_verified: bool
    c2pa_verified_at: datetime | None
    bitcoin_status: str
    bitcoin_block: int | None
    bitcoin_confirmed_at: datetime | None
    ots_proof_url: str | None


class DeviceRegisterRequest(BaseModel):
    business_id: uuid.UUID
    device_fingerprint: str
    device_public_key: str
    label: str = "PI Camera"


class DeviceRegisterResponse(BaseModel):
    device_id: uuid.UUID
    api_key: str
    registered_at: datetime
