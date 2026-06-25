import logging
import re
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db, AsyncSessionLocal
from app.models.business import Business
from app.models.block import Block
from app.models.media import Media
from app.models.user import User
from app.schemas.business import (
    AgentBusinessProfile,
    BusinessCreate,
    BusinessOut,
    BusinessUpdate,
)
from app.services.registry import verify_business_in_registry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/businesses", tags=["businesses"])


def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_-]+", "-", slug)
    return slug[:80]


def _compute_verification_level(business: Business) -> str:
    if business.registry_status != "verified":
        return "none"
    has_c2pa = any(
        m.c2pa_verified
        for block in business.blocks
        for m in block.media
    )
    has_btc = any(
        m.bitcoin_confirmed
        for block in business.blocks
        for m in block.media
    )
    if has_c2pa and has_btc:
        return "full"
    if has_btc:
        return "partial"
    return "registry"


async def _run_registry_verification(business_id: str, name: str, country: str | None) -> None:
    """Run registry verification inline (no Celery needed)."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Business).where(Business.id == business_id))
        business = result.scalar_one_or_none()
        if not business:
            return
        try:
            matches = await verify_business_in_registry(name, country)
            if matches:
                best = matches[0]
                business.registry_status = "verified"
                business.registry_id = best.registration_number
                business.registry_data = {
                    "legal_name": best.legal_name,
                    "registry": best.registry,
                    "status": best.status,
                    "founded": best.founded,
                    "address": best.address,
                    "country": (best.raw or {}).get("country") or country or "",
                }
                business.verification_level = "registry"
            else:
                business.registry_status = "not_found"
        except Exception as e:
            logger.warning("Registry verification failed for %s: %s", business_id, e)
            business.registry_status = "not_found"
        await session.commit()


@router.post("", response_model=BusinessOut, status_code=status.HTTP_201_CREATED)
async def create_business(
    payload: BusinessCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Business:
    slug = _slugify(payload.name)
    existing = await db.execute(select(Business).where(Business.slug == slug))
    if existing.scalar_one_or_none():
        slug = f"{slug}-{uuid.uuid4().hex[:6]}"

    is_business = payload.entity_type == "business"

    business = Business(
        owner_id=current_user.id,
        name=payload.name,
        slug=slug,
        description=payload.description,
        country=payload.country,
        entity_type=payload.entity_type,
        # Non-business entities (journalists/artists/orgs) are self-asserted:
        # they're verified by email, not by registry
        registry_status="self_asserted" if not is_business else "pending",
        verification_level="registry" if not is_business else "none",
        is_published=not is_business,  # Non-business goes live immediately
        is_public=not is_business,
    )
    db.add(business)
    await db.flush()
    await db.refresh(business)

    if is_business:
        background_tasks.add_task(
            _run_registry_verification,
            str(business.id),
            payload.name,
            payload.country,
        )

    return business


@router.get("", response_model=list[BusinessOut])
async def list_businesses(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[Business]:
    """List businesses owned by the current user."""
    result = await db.execute(
        select(Business)
        .where(Business.owner_id == current_user.id)
        .options(selectinload(Business.blocks).selectinload(Block.media))
        .order_by(Business.created_at.desc())
    )
    businesses = list(result.scalars().all())
    for b in businesses:
        b.verification_level = _compute_verification_level(b)
    return businesses


@router.post("/{business_id}/publish", response_model=BusinessOut)
async def publish_business(
    business_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Business:
    result = await db.execute(
        select(Business).where(Business.id == business_id)
    )
    business = result.scalar_one_or_none()
    if not business:
        raise HTTPException(status_code=404, detail="Business not found")
    if business.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your business")
    if business.entity_type == "business" and business.registry_status not in ("verified",):
        raise HTTPException(
            status_code=400,
            detail="Business must be registry-verified before publishing",
        )
    business.is_published = True
    business.is_public = True
    await db.flush()
    return business


@router.get("/{business_id}", response_model=BusinessOut)
async def get_business(
    business_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Business:
    result = await db.execute(
        select(Business)
        .where(Business.id == business_id)
        .options(selectinload(Business.blocks).selectinload(Block.media))
    )
    business = result.scalar_one_or_none()
    if not business:
        raise HTTPException(status_code=404, detail="Business not found")

    # Recompute verification level from facts
    business.verification_level = _compute_verification_level(business)
    return business


@router.patch("/{business_id}", response_model=BusinessOut)
async def update_business(
    business_id: uuid.UUID,
    payload: BusinessUpdate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Business:
    result = await db.execute(select(Business).where(Business.id == business_id))
    business = result.scalar_one_or_none()
    if not business:
        raise HTTPException(status_code=404, detail="Business not found")
    if business.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your business")

    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(business, field, value)

    await db.flush()

    if payload.name and business.entity_type == "business":
        background_tasks.add_task(
            _run_registry_verification, str(business.id), payload.name, business.country
        )

    return business


@router.get("/{business_id}/preview", response_model=AgentBusinessProfile)
async def agent_preview(
    business_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """What AI agents see — structured JSON."""
    result = await db.execute(
        select(Business)
        .where(Business.id == business_id)
        .options(selectinload(Business.blocks).selectinload(Block.media))
    )
    business = result.scalar_one_or_none()
    if not business:
        raise HTTPException(status_code=404, detail="Business not found")

    trust_level = _compute_verification_level(business)

    blocks = []
    for block in business.blocks:
        media_list = []
        for m in block.media:
            media_list.append({
                "type": m.type,
                "c2pa_verified": m.c2pa_verified,
                "c2pa_signer": m.c2pa_signer,
                "captured_at": m.captured_at.isoformat() if m.captured_at else None,
                "bitcoin_confirmed": m.bitcoin_confirmed,
                "bitcoin_block": m.bitcoin_block,
            })
        blocks.append({
            "title": block.title,
            "description": block.description,
            "media": media_list,
        })

    return {
        "id": business.id,
        "name": business.name,
        "description": business.description,
        "registry": business.registry_data,
        "trust_level": trust_level,
        "blocks": blocks,
    }


@router.get("/{business_id}/proof")
async def get_proof(
    business_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return cryptographic proofs for independent verification."""
    result = await db.execute(
        select(Business)
        .where(Business.id == business_id)
        .options(selectinload(Business.blocks).selectinload(Block.media))
    )
    business = result.scalar_one_or_none()
    if not business:
        raise HTTPException(status_code=404, detail="Business not found")

    registry_data = business.registry_data or {}
    c2pa_proofs = []
    bitcoin_proofs = []

    for block in business.blocks:
        for m in block.media:
            if m.c2pa_manifest:
                import hashlib, json
                c2pa_proofs.append({
                    "media_id": str(m.id),
                    "manifest_hash": "sha256:" + hashlib.sha256(
                        json.dumps(m.c2pa_manifest).encode()
                    ).hexdigest(),
                    "signer": m.c2pa_signer,
                })
            if m.bitcoin_confirmed:
                bitcoin_proofs.append({
                    "media_id": str(m.id),
                    "bitcoin_block": m.bitcoin_block,
                    "ots_proof_url": f"https://teta-pi.io/proofs/{m.id}.ots",
                })

    return {
        "registry_proof": {
            "source": registry_data.get("registry", ""),
            "verified_at": registry_data.get("verified_at", ""),
            "data_hash": "sha256:" + __import__("hashlib").sha256(
                __import__("json").dumps(registry_data).encode()
            ).hexdigest() if registry_data else None,
        },
        "c2pa_proofs": c2pa_proofs,
        "bitcoin_proofs": bitcoin_proofs,
    }
