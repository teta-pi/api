import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator


class BusinessCreate(BaseModel):
    name: str
    description: str | None = None
    country: str | None = None


class BusinessUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    country: str | None = None
    is_published: bool | None = None


class RegistryData(BaseModel):
    registry: str
    registration_number: str
    legal_name: str
    status: str
    founded: str | None = None
    address: str | None = None
    verified_at: str


class BusinessOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    slug: str
    description: str | None
    country: str | None
    registry_id: str | None
    registry_status: str
    registry_data: dict | None
    verification_level: str
    ai_categories: dict | None
    is_published: bool
    created_at: datetime
    updated_at: datetime


class BusinessSearchResult(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    description: str | None
    verification_level: str
    badges: list[str]
    relevance_score: float
    country: str | None
    block_count: int
    registry_id: str | None
    registry_data: dict | None
    ai_categories: dict | None


class AgentBusinessProfile(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    registry: dict | None
    trust_level: str
    blocks: list[dict]
