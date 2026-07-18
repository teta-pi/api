import re
from typing import Optional

from sqlalchemy import String, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.business import Business, EntityType
from app.models.block import Block
from app.intent_graph.schema import Intent, IntentResolution

# Common location tokens to strip from query before name matching
_LOCATION_PREFIXES = re.compile(
    r"\b(in|near|around|from|at|based in)\b", re.IGNORECASE
)

LEVEL_WEIGHTS = {
    "none": 0.0,
    "registry": 0.3,
    "partial": 0.6,
    "full": 0.9,
    "live": 1.0,
}


def _extract_location(query: str) -> tuple[str, Optional[str]]:
    """Heuristically split 'find pizza in Lisbon' → ('find pizza', 'Lisbon')."""
    match = _LOCATION_PREFIXES.search(query)
    if match:
        location = query[match.end():].strip().split()[0] if query[match.end():].strip() else None
        clean_query = query[: match.start()].strip()
        return clean_query, location
    return query, None


class IntentResolver:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def resolve(self, intent: Intent) -> list[IntentResolution]:
        """
        Sprint 1: keyword relevance over name + description + ai_categories.
        Sprint 3: replace with pgvector cosine similarity using text-embedding-3-small.
        """
        clean_q, location = _extract_location(intent.raw_query)
        if intent.location:
            location = intent.location

        stmt = (
            select(Business)
            .where(Business.is_published == True)  # noqa: E712
            .where(Business.is_public == True)  # noqa: E712
            .where(Business.entity_type == intent.entity_type.value)
            .options(selectinload(Business.blocks).selectinload(Block.media))
        )

        if intent.verified_only:
            stmt = stmt.where(Business.verification_level != "none")
        if location:
            stmt = stmt.where(
                or_(
                    Business.country.ilike(f"%{location[:2]}%"),
                    cast(Business.registry_data, String).ilike(f"%{location}%"),
                )
            )
        if intent.has_agent_endpoint is True:
            stmt = stmt.where(Business.agent_endpoint.is_not(None))

        q_lower = clean_q.lower().strip()
        if q_lower:
            # The query text must reach the DB filter, not just re-score an
            # arbitrary LIMIT'd window — previously `clean_q` was never used
            # to filter the SQL query, so once entity count exceeded the
            # LIMIT below, true matches routinely fell outside the fetched
            # window and resolve_intent silently returned nothing (QA 6.2).
            tokens = [t for t in q_lower.split() if t]
            conditions = [
                Business.name.ilike(f"%{q_lower}%"),
                Business.description.ilike(f"%{q_lower}%"),
                cast(Business.ai_categories, String).ilike(f"%{q_lower}%"),
                Business.slug.ilike(f"%{q_lower}%"),
            ]
            for tok in tokens:
                conditions.append(Business.name.ilike(f"%{tok}%"))
            stmt = stmt.where(or_(*conditions))

        result = await self.db.execute(stmt.limit(50))
        candidates = list(result.scalars().all())

        resolved: list[IntentResolution] = []

        for biz in candidates:
            name_score = 0.0
            if q_lower in (biz.name or "").lower():
                name_score = 0.9
            elif any(tok in (biz.name or "").lower() for tok in q_lower.split()):
                name_score = 0.6
            elif q_lower in (biz.slug or "").lower():
                name_score = 0.55
            elif q_lower in (biz.description or "").lower():
                name_score = 0.5
            elif biz.ai_categories and q_lower in str(biz.ai_categories).lower():
                name_score = 0.45
            else:
                name_score = 0.1

            level_weight = LEVEL_WEIGHTS.get(biz.verification_level, 0.0)
            endpoint_bonus = 0.05 if biz.agent_endpoint else 0.0
            score = name_score * 0.65 + level_weight * 0.3 + endpoint_bonus

            if score < 0.15:
                continue

            resolved.append(
                IntentResolution(
                    entity_id=str(biz.id),
                    entity_type=biz.entity_type,
                    entity_name=biz.name,
                    relevance_score=round(score, 3),
                    verification_level=biz.verification_level,
                    agent_endpoint=biz.agent_endpoint,
                    agent_endpoint_verified=biz.agent_endpoint_verified,
                    country=biz.country,
                    registry_id=biz.registry_id,
                    proof_url=f"https://app.tetapi.dev/e/{biz.slug}",
                )
            )

        resolved.sort(key=lambda r: r.relevance_score, reverse=True)
        return resolved[:10]


