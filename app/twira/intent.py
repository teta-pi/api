"""I — Intent Alignment. Query-time pgvector similarity (SystemSpec v2.1 §3.2)."""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# I = max(sim_i * c2pa_w_i) over the entity's top-3 public blocks.
_INTENT_SQL = text(
    """
    SELECT 1 - (b.embedding <=> CAST(:query_emb AS vector)) AS sim,
           CASE WHEN b.c2pa_manifest IS NOT NULL THEN 1.0 ELSE 0.6 END AS c2pa_w
    FROM blocks b
    WHERE b.business_id = :eid AND b.is_public AND b.embedding IS NOT NULL
    ORDER BY b.embedding <=> CAST(:query_emb AS vector)
    LIMIT 3
    """
)


async def intent_score(db: AsyncSession, query_emb: list[float], entity_id: uuid.UUID) -> float:
    rows = (
        await db.execute(
            _INTENT_SQL,
            {"query_emb": str(query_emb), "eid": str(entity_id)},
        )
    ).all()
    if not rows:
        return 0.0
    return max(float(sim) * float(c2pa_w) for sim, c2pa_w in rows)
