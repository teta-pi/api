import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db, AsyncSessionLocal
from app.models.business import Business
from app.models.block import Block
from app.models.media import Media
from app.models.user import User
from app.models.verification_event import VerificationEvent
from app.twira.provenance import current_btc_height
from app.schemas.business import (
    AgentBusinessProfile,
    BusinessCreate,
    BusinessOut,
    BusinessUpdate,
)
from app.services.registry import verify_business_in_registry
from app.services.verification import domain_ownership, email_control

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/businesses", tags=["businesses"])

# Independent, optional verification methods (docs/verification-rework.md §2).
# Each writes its own append-only verification_events row on success.
_METHOD_EVENT_TYPES = {"email_verified", "domain_verified"}


class EmailVerifyStartRequest(BaseModel):
    email: EmailStr


class EmailVerifyConfirmRequest(BaseModel):
    email: EmailStr
    code: str


class DomainVerifyRequest(BaseModel):
    domain: str


class LegalEntityLinkRequest(BaseModel):
    legal_entity_id: uuid.UUID


def _event_payload_hash(payload: dict) -> bytes:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).digest()


async def _get_owned_business(
    db: AsyncSession, business_id: uuid.UUID, current_user: User
) -> Business:
    result = await db.execute(select(Business).where(Business.id == business_id))
    business = result.scalar_one_or_none()
    if not business:
        raise HTTPException(status_code=404, detail="Business not found")
    if business.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your business")
    return business


def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_-]+", "-", slug)
    return slug[:80]


async def _compute_verification_level(db: AsyncSession, business: Business) -> str:
    """Derived, not stored: reflects whichever independent method (registry,
    email, domain) — or media provenance — currently holds for this entity.
    `business.blocks`/`.media` must already be loaded by the caller."""
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

    if business.registry_status == "verified":
        return "registry"

    result = await db.execute(
        select(VerificationEvent.event_type)
        .where(
            VerificationEvent.entity_id == business.id,
            VerificationEvent.event_type.in_(_METHOD_EVENT_TYPES),
        )
        .limit(1)
    )
    verified_via = result.scalar_one_or_none()
    if verified_via:
        return verified_via.removesuffix("_verified")  # "email" | "domain"

    return "none"


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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Business:
    """Creation is decoupled from registry matching (docs/verification-rework.md
    §1): any name is creatable immediately, free, unverified (L0) — no
    registry call here. Registry match is now an explicit, optional
    verification method (POST /{business_id}/verify/registry)."""
    slug = _slugify(payload.name)
    existing = await db.execute(select(Business).where(Business.slug == slug))
    if existing.scalar_one_or_none():
        slug = f"{slug}-{uuid.uuid4().hex[:6]}"

    business = Business(
        owner_id=current_user.id,
        name=payload.name,
        slug=slug,
        description=payload.description,
        country=payload.country,
        entity_type=payload.entity_type,
        registry_status="unverified",
        verification_level="none",
        is_published=True,
        is_public=True,
    )
    db.add(business)
    await db.flush()
    await db.refresh(business)
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
        b.verification_level = await _compute_verification_level(db, b)
    return businesses


@router.post("/{business_id}/publish", response_model=BusinessOut)
async def publish_business(
    business_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Business:
    """Publishing no longer requires registry verification (docs/verification-
    rework.md §1) — entities are already published at creation; this endpoint
    just re-publishes one that was manually unpublished."""
    business = await _get_owned_business(db, business_id, current_user)
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
    business.verification_level = await _compute_verification_level(db, business)
    return business


@router.patch("/{business_id}", response_model=BusinessOut)
async def update_business(
    business_id: uuid.UUID,
    payload: BusinessUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Business:
    """Renaming no longer auto-triggers a registry check — registry match is
    an explicit, optional method now (POST /{business_id}/verify/registry)."""
    business = await _get_owned_business(db, business_id, current_user)

    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(business, field, value)

    await db.flush()
    await db.refresh(business)
    return business


@router.post("/{business_id}/verify/registry")
async def start_registry_verification(
    business_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Official Registry Match — one optional verification method (was
    automatic at creation; now explicit, owner-triggered)."""
    business = await _get_owned_business(db, business_id, current_user)
    background_tasks.add_task(
        _run_registry_verification, str(business.id), business.name, business.country
    )
    return {"message": "Registry verification started"}


@router.post("/{business_id}/verify/email/start")
async def start_email_verification_endpoint(
    business_id: uuid.UUID,
    payload: EmailVerifyStartRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Business Email Control, step 1 — send a 6-digit code to an address on
    the brand's own domain."""
    await _get_owned_business(db, business_id, current_user)
    await email_control.start_email_verification(payload.email, background_tasks)
    return {"message": "Verification code sent — check the inbox."}


@router.post("/{business_id}/verify/email/confirm")
async def confirm_email_verification_endpoint(
    business_id: uuid.UUID,
    payload: EmailVerifyConfirmRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Business Email Control, step 2 — verify the code and record the
    (append-only) verification event."""
    business = await _get_owned_business(db, business_id, current_user)
    ok = await email_control.confirm_email_verification(payload.email, payload.code)
    if not ok:
        raise HTTPException(status_code=400, detail="Invalid or expired code")

    db.add(
        VerificationEvent(
            entity_id=business.id,
            event_type="email_verified",
            level=1,
            source="business_email",
            payload_hash=_event_payload_hash({
                "email": payload.email,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }),
        )
    )
    await db.commit()
    return {"verified": True, "email": payload.email}


@router.post("/{business_id}/verify/domain/start")
async def start_domain_verification_endpoint(
    business_id: uuid.UUID,
    payload: DomainVerifyRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Domain Ownership, step 1 — get a token + DNS TXT record / well-known
    file instructions (same mechanism as the WordPress plugin)."""
    await _get_owned_business(db, business_id, current_user)
    return await domain_ownership.start_domain_verification(str(business_id), payload.domain)


@router.post("/{business_id}/verify/domain/check")
async def check_domain_verification_endpoint(
    business_id: uuid.UUID,
    payload: DomainVerifyRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Domain Ownership, step 2 — check the DNS TXT record or well-known file
    and record the (append-only) verification event on success."""
    business = await _get_owned_business(db, business_id, current_user)
    verified, method = await domain_ownership.check_domain_verification(
        str(business_id), payload.domain
    )
    if not verified:
        return {"verified": False}

    db.add(
        VerificationEvent(
            entity_id=business.id,
            event_type="domain_verified",
            level=1,
            source=method,
            payload_hash=_event_payload_hash({
                "domain": payload.domain,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }),
        )
    )
    await db.commit()
    return {"verified": True, "domain": payload.domain, "method": method}


@router.post("/{business_id}/legal-entity")
async def link_legal_entity(
    business_id: uuid.UUID,
    payload: LegalEntityLinkRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Link a brand to a verified legal entity (e.g. "Google" brand ->
    "Alphabet Inc." legal entity) instead of forcing a registry name-match.
    Publicly disclosed on the profile, not hidden (see public_profile_by_slug)."""
    business = await _get_owned_business(db, business_id, current_user)

    if payload.legal_entity_id == business.id:
        raise HTTPException(status_code=400, detail="An entity cannot link to itself")

    result = await db.execute(select(Business).where(Business.id == payload.legal_entity_id))
    legal_entity = result.scalar_one_or_none()
    if not legal_entity:
        raise HTTPException(status_code=404, detail="Legal entity not found")
    if legal_entity.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="You must own the legal entity too")
    if legal_entity.registry_status != "verified":
        raise HTTPException(status_code=400, detail="Legal entity must be registry-verified first")

    business.legal_entity_id = legal_entity.id
    await db.flush()
    return {"legal_entity_id": str(legal_entity.id), "legal_entity_name": legal_entity.name}


@router.delete("/{business_id}/legal-entity")
async def unlink_legal_entity(
    business_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    business = await _get_owned_business(db, business_id, current_user)
    business.legal_entity_id = None
    await db.flush()
    return {"legal_entity_id": None}


@router.get("/by-slug/{slug}/public")
async def public_profile_by_slug(
    slug: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Public entity page — only published & public entities, public blocks only."""
    result = await db.execute(
        select(Business)
        .where(Business.slug == slug, Business.is_published == True, Business.is_public == True)  # noqa: E712
        .options(selectinload(Business.blocks).selectinload(Block.media))
    )
    business = result.scalar_one_or_none()
    if not business:
        raise HTTPException(status_code=404, detail="Entity not found")

    trust_level = await _compute_verification_level(db, business)

    legal_entity = None
    if business.legal_entity_id:
        le = (
            await db.execute(select(Business).where(Business.id == business.legal_entity_id))
        ).scalar_one_or_none()
        if le:
            legal_entity = {
                "id": str(le.id),
                "name": le.name,
                "slug": le.slug,
                "registry_status": le.registry_status,
            }

    blocks = []
    for block in sorted(business.blocks, key=lambda b: b.order):
        if not block.is_public:
            continue
        media_list = [
            {
                "type": m.type,
                "media_url": m.storage_url,
                "content_hash": m.original_hash,
                "c2pa_verified": m.c2pa_verified,
                "captured_at": m.captured_at.isoformat() if m.captured_at else None,
                "bitcoin_confirmed": m.bitcoin_confirmed,
                "bitcoin_block": m.bitcoin_block,
            }
            for m in block.media
        ]
        blocks.append({"title": block.title, "description": block.description, "media": media_list})

    rd = business.registry_data or {}
    return {
        "name": business.name,
        "slug": business.slug,
        "entity_type": business.entity_type,
        "description": business.description,
        "country": business.country,
        "trust_level": trust_level,
        "registry": {
            "registry": rd.get("registry"),
            "status": business.registry_status,
            "registry_id": business.registry_id,
        },
        # Brand -> verified legal entity link, publicly disclosed, not hidden
        # (docs/verification-rework.md §3).
        "legal_entity": legal_entity,
        "agent_endpoint": business.agent_endpoint,
        "agent_endpoint_verified": business.agent_endpoint_verified,
        "blocks": blocks,
        "created_at": business.created_at.isoformat(),
    }


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

    trust_level = await _compute_verification_level(db, business)

    blocks = []
    for block in business.blocks:
        media_list = []
        for m in block.media:
            media_list.append({
                "type": m.type,
                "media_url": m.storage_url,
                "content_hash": m.original_hash,
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
        "entity_type": business.entity_type,
        "name": business.name,
        "description": business.description,
        "registry": business.registry_data,
        "trust_level": trust_level,
        "agent_endpoint": business.agent_endpoint,
        "agent_endpoint_verified": business.agent_endpoint_verified,
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

    # Proof depth — read the Temporal Moat chronology (verification_events) so
    # agents can set their own trust threshold from OTS lifecycle state, Bitcoin
    # confirmation depth and C2PA chain length. Read-only; no new tables/workers.
    events = (
        await db.execute(
            select(VerificationEvent.ots_status, VerificationEvent.btc_block).where(
                VerificationEvent.entity_id == business_id
            )
        )
    ).all()

    status_rank = {"pending": 0, "anchored": 1, "confirmed": 2}
    ots_status = None
    if events:
        ots_status = max(
            (e.ots_status for e in events), key=lambda s: status_rank.get(s, -1)
        )

    # Deepest Bitcoin confirmation = oldest anchored event; more blocks = harder to forge.
    height = await current_btc_height()
    depths = [
        height - e.btc_block
        for e in events
        if e.ots_status == "confirmed" and e.btc_block is not None
    ]
    btc_timestamp_depth = max(depths) if depths and height else None

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
        "proof_depth": {
            "ots_status": ots_status,
            "btc_timestamp_depth": btc_timestamp_depth,
            "c2pa_chain_length": len(c2pa_proofs),
            "event_count": len(events),
        },
    }
