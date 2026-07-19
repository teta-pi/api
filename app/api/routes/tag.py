import json
import time
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import String, cast, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.routes.businesses import _compute_verification_level
from app.core.database import get_db
from app.core.redis import get_redis
from app.models.block import Block
from app.models.business import Business

router = APIRouter(tags=["tag"])

_APP_URL = "https://app.tetapi.dev"
_API_URL = "https://api.tetapi.dev"
_WK_CACHE_CONTROL = "public, max-age=300"  # ~5 min (docs/universal-tag.md §Part B)

# Same in-memory limiter pattern as badge.py (B5, docs/security.md) — generous
# since it's one hit per page load on every installed site. Redis migration
# for multi-worker is tracked as S-10; not needed at current single-worker scale.
_RATE_LIMIT = 240
_RATE_WINDOW = 60.0
_hits: dict[str, list[float]] = defaultdict(list)

# Bounded per-entity page list, capped via ZREMRANGEBYRANK — a Redis sorted
# set, not a new DB table (12.5b storage decision: option (a), same (a)/(b)
# choice flagged for 2.4; see docs/decisions.md). Cheap append, no per-hit
# write to Postgres.
_MAX_PAGES_PER_ENTITY = 200


def _rate_limit(request: Request) -> None:
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "unknown")
    ip = ip.split(",")[0].strip()
    now = time.monotonic()
    window = [t for t in _hits[ip] if now - t < _RATE_WINDOW]
    if len(window) >= _RATE_LIMIT:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded")
    window.append(now)
    _hits[ip] = window


class TagPingIn(BaseModel):
    entity_id: str = Field(max_length=255)
    page_url: str = Field(max_length=2048)
    page_title: str | None = Field(default=None, max_length=300)
    referrer: str | None = Field(default=None, max_length=2048)


async def _find_business(db: AsyncSession, entity_id: str, *, with_blocks: bool = False) -> Business | None:
    stmt = select(Business).where(
        (Business.slug == entity_id) | (cast(Business.id, String) == entity_id),
        Business.is_published == True,  # noqa: E712
        Business.is_public == True,  # noqa: E712
    )
    if with_blocks:
        stmt = stmt.options(selectinload(Business.blocks).selectinload(Block.media))
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


@router.post("/v1/tag-ping", status_code=status.HTTP_204_NO_CONTENT)
async def tag_ping(
    payload: TagPingIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Anonymous beacon fired by tag.js once per page load (B5,
    docs/universal-tag.md). Always 204, even for an unknown/spoofed
    entity_id — the response must never disclose whether an entity exists,
    matching tag.js's "fails silently, never blocks render" contract."""
    _rate_limit(request)

    business = await _find_business(db, payload.entity_id)
    if business is not None:
        redis = await get_redis()
        member = payload.page_url[:2048]
        if payload.page_title:
            member = f"{member}\x1f{payload.page_title[:300]}"
        key = f"tag_pages:{business.id}"
        await redis.zadd(key, {member: time.time()})
        await redis.zremrangebyrank(key, 0, -(_MAX_PAGES_PER_ENTITY + 1))
        await redis.incr(f"tag_impressions:{business.id}")

    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _profile_url(business: Business) -> str:
    return f"{_APP_URL}/e/{business.slug}"


def _proof_url(business: Business) -> str:
    return f"{_API_URL}/api/v1/businesses/{business.id}/proof"


@router.get("/wk/{entity_id}/agent.json")
async def wk_agent_json(entity_id: str, db: AsyncSession = Depends(get_db)) -> Response:
    """Part B of the Universal Tag (docs/universal-tag.md) — reverse-proxied
    from the entity's own domain via verify.tetapi.dev (12.5c). Same
    schema.org JSON-LD shape tag.js injects client-side."""
    business = await _find_business(db, entity_id)
    if business is None:
        raise HTTPException(status_code=404, detail="Entity not found")

    doc = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": business.name,
        "description": business.description,
        "sameAs": [_profile_url(business)],
        "identifier": {
            "@type": "PropertyValue",
            "propertyID": "tetapi_entity_id",
            "value": str(business.id),
        },
    }
    return Response(content=_json_dumps(doc), media_type="application/json", headers={"Cache-Control": _WK_CACHE_CONTROL})


@router.get("/wk/{entity_id}/agent-card.json")
async def wk_agent_card_json(entity_id: str, db: AsyncSession = Depends(get_db)) -> Response:
    """What AI agents read for this entity — richer than agent.json, mirrors
    `/businesses/{id}/preview` (public fields only)."""
    business = await _find_business(db, entity_id, with_blocks=True)
    if business is None:
        raise HTTPException(status_code=404, detail="Entity not found")

    trust_level = await _compute_verification_level(db, business)
    blocks = [
        {"title": b.title, "description": b.description}
        for b in sorted(business.blocks, key=lambda b: b.order)
        if b.is_public
    ]

    doc = {
        "entity_id": str(business.id),
        "entity_type": business.entity_type,
        "name": business.name,
        "description": business.description,
        "trust_level": trust_level,
        "verified_profile_url": _profile_url(business),
        "proof_url": _proof_url(business),
        "agent_endpoint": business.agent_endpoint,
        "agent_endpoint_verified": business.agent_endpoint_verified,
        "blocks": blocks,
    }
    return Response(content=_json_dumps(doc), media_type="application/json", headers={"Cache-Control": _WK_CACHE_CONTROL})


@router.get("/wk/{entity_id}/llms.txt")
async def wk_llms_txt(entity_id: str, db: AsyncSession = Depends(get_db)) -> Response:
    business = await _find_business(db, entity_id, with_blocks=True)
    if business is None:
        raise HTTPException(status_code=404, detail="Entity not found")

    trust_level = await _compute_verification_level(db, business)
    lines = [f"# {business.name}", ""]
    if business.description:
        lines += [f"> {business.description}", ""]
    lines += [
        f"- [Verified profile]({_profile_url(business)})",
        f"- [Agent-readable proof]({_proof_url(business)})",
        "",
        f"Verified via TETA+PI (trust_level: {trust_level}).",
    ]
    return Response(content="\n".join(lines) + "\n", media_type="text/plain", headers={"Cache-Control": _WK_CACHE_CONTROL})


def _json_dumps(doc: dict) -> str:
    return json.dumps(doc, ensure_ascii=False)
