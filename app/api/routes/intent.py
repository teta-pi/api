from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.intent_graph.schema import Intent, IntentResolution
from app.intent_graph.resolver import IntentResolver
from app.models.business import EntityType
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/resolve-intent", tags=["intent"])


class IntentRequest(BaseModel):
    query: str
    entity_type: str = "business"
    verified_only: bool = True
    has_agent_endpoint: Optional[bool] = None
    location: Optional[str] = None


class IntentResponse(BaseModel):
    query: str
    results: list[IntentResolution]


@router.post("", response_model=IntentResponse)
async def resolve_intent(
    payload: IntentRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Resolve a natural-language intent into ranked verified entities.

    Examples:
      {"query": "find verified pizza restaurant in Lisbon"}
      {"query": "freight logistics agent Germany", "has_agent_endpoint": true}
    """
    try:
        et = EntityType(payload.entity_type)
    except ValueError:
        et = EntityType.business

    intent = Intent(
        raw_query=payload.query,
        entity_type=et,
        location=payload.location,
        verified_only=payload.verified_only,
        has_agent_endpoint=payload.has_agent_endpoint,
    )

    resolver = IntentResolver(db)
    results = await resolver.resolve(intent)

    return {"query": payload.query, "results": results}
