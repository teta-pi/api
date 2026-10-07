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
    device_signature_verified: bool = False
    bitcoin_status: str
    bitcoin_block: int | None
    bitcoin_confirmed_at: datetime | None
    ots_proof_url: str | None


class DeviceRegisterRequest(BaseModel):
    registration_token: str
    device_fingerprint: str
    device_public_key: str
    label: str = "Pi CAM"


class DeviceRegisterResponse(BaseModel):
    device_id: uuid.UUID
    api_key: str
    entity_id: str
    entity_name: str
    entity_slug: str | None = None
    registered_at: datetime


class QRTokenResponse(BaseModel):
    token: str
    entity_id: str
    entity_name: str
    expires_in: int = 900  # seconds


class DeviceMediaUploadResponse(BaseModel):
    media_id: uuid.UUID
    c2pa_verified: bool
    c2pa_signer: str | None
    device_signature_verified: bool = False
    bitcoin_status: str
    teta_pi_verified: bool = False


class DeviceSummary(BaseModel):
    id: uuid.UUID
    label: str
    registered_at: datetime
    revoked_at: datetime | None = None  # 1.25 — set once the key is killed


class DeviceListResponse(BaseModel):
    paired: bool
    devices: list[DeviceSummary]


class DeviceRevokeResponse(BaseModel):
    device_id: uuid.UUID
    revoked_at: datetime
