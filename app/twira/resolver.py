"""TWIRA-ranked intent resolution (SystemSpec v2.1 §04).

Pipeline: embed(query) → pgvector ANN candidate set → assemble α·T + β·I + γ·P
(T, P precomputed on businesses; I query-time) → ranked results with breakdown.
"""

import logging
import uuid
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business import Business
from app.models.verification_event import VerificationEvent
from app.services.ai import generate_embedding
from app.twira.intent import intent_score
from app.twira.score import twira_score

logger = logging.getLogger(__name__)

_CANDIDATES_SQL = text(
    """
    SELECT DISTINCT ON (b.business_id)
           b.business_id,
           1 - (b.embedding <=> CAST(:query_emb AS vector)) AS sim
    FROM blocks b
    JOIN businesses biz ON biz.id = b.business_id
    WHERE b.is_public AND b.embedding IS NOT NULL
      AND biz.is_public AND biz.is_published
    ORDER BY b.business_id, b.embedding <=> CAST(:query_emb AS vector)
    LIMIT 100
    """
)


async def twira_resolve(
    db: AsyncSession,
    query: str,
    entity_types: list[str] | None = None,
    limit: int = 10,
    min_trust: float | None = None,
) -> list[dict]:
    """Returns ranked entities with TWIRA breakdown. Empty list when no
    embeddings exist yet — caller should fall back to keyword resolution."""
    try:
        query_emb = await generate_embedding(query)
    except Exception:
        logger.exception("Embedding generation failed — TWIRA resolve unavailable")
        return []
    if not query_emb:
        # No embedding provider configured — caller falls back to keyword resolver
        return []

    candidate_rows = (await db.execute(_CANDIDATES_SQL, {"query_emb": str(query_emb)})).all()
    candidate_ids = [row.business_id for row in candidate_rows]
    if not candidate_ids:
        return []

    stmt = select(Business).where(Business.id.in_(candidate_ids))
    if entity_types:
        stmt = stmt.where(Business.entity_type.in_(entity_types))
    if min_trust is not None:
        stmt = stmt.where(Business.t_score >= min_trust)
    entities = (await db.execute(stmt)).scalars().all()
    if not entities:
        return []

    # first_verified_at = MIN(created_at) of confirmed events (Temporal Moat, §02)
    fv_rows = (
        await db.execute(
            select(VerificationEvent.entity_id, func.min(VerificationEvent.created_at))
            .where(
                VerificationEvent.entity_id.in_([e.id for e in entities]),
                VerificationEvent.ots_status == "confirmed",
            )
            .group_by(VerificationEvent.entity_id)
        )
    ).all()
    first_verified: dict[uuid.UUID, datetime] = {eid: dt for eid, dt in fv_rows}

    results: list[dict] = []
    for entity in entities:
        i = await intent_score(db, query_emb, entity.id)
        score = twira_score(entity.t_score, i, entity.p_score)
        results.append(
            {
                "entity": entity,
                "score": round(score, 4),
                "t": round(entity.t_score, 4),
                "i": round(i, 4),
                "p": round(entity.p_score, 4),
                "first_verified_at": first_verified.get(entity.id),
            }
        )

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:limit]
