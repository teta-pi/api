from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.intent_graph.schema import Intent, IntentResolution, TwiraBreakdown
from app.intent_graph.resolver import IntentResolver
from app.twira.resolver import twira_resolve
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

    # TWIRA-ranked pipeline first (SystemSpec v2.1 §04); empty when no
    # embeddings exist yet or the embedding provider is unavailable.
    twira_results = await twira_resolve(db, payload.query, [et.value], limit=10)
    if twira_results:
        results = [
            IntentResolution(
                entity_id=r["entity"].slug,
                entity_type=r["entity"].entity_type,
                entity_name=r["entity"].name,
                relevance_score=r["score"],
                verification_level=r["entity"].verification_level,
                agent_endpoint=r["entity"].agent_endpoint,
                agent_endpoint_verified=r["entity"].agent_endpoint_verified,
                country=r["entity"].country,
                registry_id=r["entity"].registry_id,
                twira=TwiraBreakdown(score=r["score"], t=r["t"], i=r["i"], p=r["p"]),
                first_verified_at=r["first_verified_at"],
                proof_url=f"https://api.tetapi.dev/api/v1/businesses/{r['entity'].slug}/proof",
            )
            for r in twira_results
        ]
        return {"query": payload.query, "results": results}

    # Fallback: keyword resolver (pre-TWIRA behaviour)
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
