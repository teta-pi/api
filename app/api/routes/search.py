from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
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
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """
    Semantic search over verified businesses.
    Sprint 1: keyword-based fallback. Sprint 3: pgvector cosine similarity.
    """
    stmt = (
        select(Business)
        .where(Business.is_published == True)  # noqa: E712
        .where(Business.verification_level != "none")
        .options(selectinload(Business.blocks).selectinload(Block.media))
    )

    if level != "any":
        stmt = stmt.where(Business.verification_level == level)
    if country:
        stmt = stmt.where(Business.country == country.upper())

    result = await db.execute(stmt.offset(offset).limit(limit))
    businesses = list(result.scalars().all())

    results = []
    for biz in businesses:
        # Simple keyword relevance for Sprint 1
        relevance = 0.5
        if q.strip():
            query_lower = q.lower()
            if query_lower in (biz.name or "").lower():
                relevance = 0.9
            elif query_lower in (biz.description or "").lower():
                relevance = 0.7
            else:
                relevance = 0.3

        level_weight = LEVEL_WEIGHTS.get(biz.verification_level, 0.0)
        score = relevance * 0.6 + level_weight * 0.3 + 0.1

        results.append({
            "id": biz.id,
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
        })

    results.sort(key=lambda r: r["relevance_score"], reverse=True)
    return results
