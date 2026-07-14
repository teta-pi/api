from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.business import Business
from app.models.user import User

router = APIRouter(prefix="/verify-endpoint", tags=["endpoint-verification"])


class EndpointVerifyRequest(BaseModel):
    endpoint_url: str
    entity_id: str | None = None


class EndpointVerifyResponse(BaseModel):
    endpoint: str
    entity_id: str | None
    is_active: bool
    belongs_to_entity: bool
    data_consistent: bool
    last_checked: str
    verification_proof: str | None


async def _verify_active(url: str, client: httpx.AsyncClient) -> bool:
    """Check that the endpoint responds with 2xx."""
    try:
        r = await client.get(url, timeout=8.0, follow_redirects=True)
        return r.status_code < 400
    except Exception:
        return False


async def _verify_ownership(url: str, entity: Business) -> bool:
    """
    Check domain ownership: the endpoint's hostname must match the entity's
    registry domain, agent_endpoint claim, or slug-derived domain.
    Sprint 1: checks that the declared agent_endpoint field matches the URL's host.
    Sprint 2: add DNS TXT record check (TETA+PI verification token).
    """
    if not entity.agent_endpoint:
        return False
    declared = urlparse(entity.agent_endpoint).netloc
    submitted = urlparse(url).netloc
    return declared == submitted


async def _verify_consistency(url: str, entity: Business, client: httpx.AsyncClient) -> bool:
    """
    Fetch the endpoint and compare key fields against the verified profile.
    Expects the endpoint to return JSON with at least `name` or `entity_id`.
    Sprint 1: loose match on entity name. Sprint 2: structured schema validation.
    """
    try:
        r = await client.get(url, timeout=8.0, follow_redirects=True)
        if r.status_code >= 400:
            return False
        data = r.json()
        endpoint_name = (data.get("name") or data.get("entity_name") or "").lower()
        if not endpoint_name:
            return True  # No name to compare — assume consistent
        return entity.name.lower() in endpoint_name or endpoint_name in entity.name.lower()
    except Exception:
        return False


@router.post("", response_model=EndpointVerifyResponse)
async def verify_endpoint(
    payload: EndpointVerifyRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    entity: Business | None = None

    if payload.entity_id:
        result = await db.execute(
            select(Business).where(
                (Business.slug == payload.entity_id) | (Business.id.cast("text") == payload.entity_id)
            )
        )
        entity = result.scalar_one_or_none()
        if not entity:
            raise HTTPException(status_code=404, detail="Entity not found")

    async with httpx.AsyncClient() as client:
        is_active = await _verify_active(payload.endpoint_url, client)
        belongs = await _verify_ownership(payload.endpoint_url, entity) if entity else False
        consistent = (
            await _verify_consistency(payload.endpoint_url, entity, client)
            if entity and is_active
            else False
        )

    # If all checks pass and entity is known, persist the verified endpoint
    if entity and is_active and belongs and consistent:
        entity.agent_endpoint = payload.endpoint_url
        entity.agent_endpoint_verified = True
        await db.commit()

    return {
        "endpoint": payload.endpoint_url,
        "entity_id": payload.entity_id,
        "is_active": is_active,
        "belongs_to_entity": belongs,
        "data_consistent": consistent,
        "last_checked": datetime.now(timezone.utc).isoformat(),
        "verification_proof": f"btc:pending:{payload.endpoint_url}" if is_active else None,
    }
