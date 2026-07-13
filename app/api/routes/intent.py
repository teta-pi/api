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
    # Plural filter takes precedence over `entity_type` when provided.
    entity_types: Optional[list[str]] = None
    verified_only: bool = True
    has_agent_endpoint: Optional[bool] = None
    location: Optional[str] = None
    # Minimum Trust component (T) score, 0–1 — drops entities with a weaker
    # verification history from TWIRA-ranked results.
    min_trust: Optional[float] = None


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
    # Resolve requested entity type(s). `entity_types` (plural) wins; fall back
    # to the single `entity_type`. Unknown values are dropped, not fatal.
    raw_types = payload.entity_types or [payload.entity_type]
    ets: list[str] = []
    for t in raw_types:
        try:
            ets.append(EntityType(t).value)
        except ValueError:
            continue
    if not ets:
        ets = [EntityType.business.value]

    # TWIRA-ranked pipeline first (SystemSpec v2.1 §04); empty when no
    # embeddings exist yet or the embedding provider is unavailable.
    twira_results = await twira_resolve(
        db, payload.query, ets, limit=10, min_trust=payload.min_trust
    )
    if twira_results:
        results = [
            IntentResolution(
                entity_id=str(r["entity"].id),
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

    # Fallback: keyword resolver (pre-TWIRA behaviour). It ranks a single
    # entity type, so use the first resolved one; min_trust is TWIRA-only.
    intent = Intent(
        raw_query=payload.query,
        entity_type=EntityType(ets[0]),
        location=payload.location,
        verified_only=payload.verified_only,
        has_agent_endpoint=payload.has_agent_endpoint,
    )

    resolver = IntentResolver(db)
    results = await resolver.resolve(intent)

    return {"query": payload.query, "results": results}
