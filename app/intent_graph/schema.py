from typing import Optional

from pydantic import BaseModel

from app.models.business import EntityType


class Intent(BaseModel):
    raw_query: str
    entity_type: EntityType = EntityType.business
    location: Optional[str] = None
    verified_only: bool = True
    has_agent_endpoint: Optional[bool] = None
    attributes: dict = {}


class IntentResolution(BaseModel):
    entity_id: str
    entity_type: str
    entity_name: str
    relevance_score: float
    verification_level: str
    agent_endpoint: Optional[str]
    agent_endpoint_verified: bool
    country: Optional[str]
    registry_id: Optional[str]
