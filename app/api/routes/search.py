from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import String, cast, or_, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.business import Business
from app.models.block import Block
from app.schemas.business import BusinessSearchResult

router = APIRouter(prefix="/search", tags=["search"])

LEVEL_WEIGHTS = {
    "none": 0.0,
    "registry": 0.3,
    "partial": 0.6,
    "full": 0.9,
    "live": 1.0,
}

LEVEL_BADGES = {
    "full": ["registry", "c2pa", "bitcoin_ts"],
    "partial": ["registry", "bitcoin_ts"],
    "registry": ["registry"],
    "live": ["registry", "c2pa", "bitcoin_ts", "live"],
    "none": [],
}


@router.get("", response_model=list[BusinessSearchResult])
async def search_businesses(
    q: str = Query("", description="Natural language search query"),
    level: Literal["any", "registry", "partial", "full"] = Query("any"),
    country: str | None = Query(None),
    entity_type: str | None = Query(None, description="business | person | organization"),
    has_agent_endpoint: bool | None = Query(None),
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """
    Search verified entities. Only returns published + public ones.
    Sprint 1: keyword fallback. Sprint 3: pgvector cosine similarity.
    """
    stmt = (
        select(Business)
        .where(Business.is_published == True)  # noqa: E712
        .where(Business.is_public == True)  # noqa: E712
        .options(selectinload(Business.blocks).selectinload(Block.media))
    )

    if level != "any":
        stmt = stmt.where(Business.verification_level == level)
    if country:
        stmt = stmt.where(Business.country == country.upper())
    if entity_type:
        stmt = stmt.where(Business.entity_type == entity_type)
    if has_agent_endpoint is True:
        stmt = stmt.where(Business.agent_endpoint.is_not(None))
    elif has_agent_endpoint is False:
        stmt = stmt.where(Business.agent_endpoint.is_(None))

    q_stripped = q.strip()
    if q_stripped:
        # Query text must actually reach the DB filter — previously the
        # SQL query ignored `q` entirely and only re-scored whichever rows
        # the LIMIT/OFFSET happened to fetch, so matching entities outside
        # that arbitrary window never surfaced (bug found by QA task 6.2).
        pattern = f"%{q_stripped}%"
        stmt = stmt.where(
            or_(
                Business.name.ilike(pattern),
                Business.slug.ilike(pattern),
                Business.description.ilike(pattern),
                cast(Business.ai_categories, String).ilike(pattern),
            )
        )

    result = await db.execute(stmt.offset(offset).limit(limit))
    businesses = list(result.scalars().all())

    results = []
    for biz in businesses:
        relevance = 0.5
        if q_stripped:
            q_lower = q_stripped.lower()
            if q_lower in (biz.name or "").lower():
                relevance = 0.9
            elif q_lower in (biz.slug or "").lower():
                relevance = 0.85
            elif q_lower in (biz.description or "").lower():
                relevance = 0.7
            elif biz.ai_categories and q_lower in str(biz.ai_categories).lower():
                relevance = 0.65
            else:
                relevance = 0.3

        level_weight = LEVEL_WEIGHTS.get(biz.verification_level, 0.0)
        endpoint_bonus = 0.05 if biz.agent_endpoint else 0.0
        score = relevance * 0.6 + level_weight * 0.3 + endpoint_bonus + 0.05

        results.append({
            "id": biz.id,
            "entity_type": biz.entity_type,
            "name": biz.name,
            "slug": biz.slug,
            "description": biz.description,
            "verification_level": biz.verification_level,
            "badges": LEVEL_BADGES.get(biz.verification_level, []),
            "relevance_score": round(score, 3),
            "country": biz.country,
            "block_count": len(biz.blocks),
            "registry_id": biz.registry_id,
            "registry_data": biz.registry_data,
            "ai_categories": biz.ai_categories,
            "agent_endpoint": biz.agent_endpoint,
            "agent_endpoint_verified": biz.agent_endpoint_verified,
        })

    results.sort(key=lambda r: r["relevance_score"], reverse=True)
    return results
