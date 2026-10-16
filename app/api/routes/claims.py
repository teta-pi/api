from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import ratelimit
from app.core.config import settings
from app.core.database import get_db
from app.models.claim import Claim
from app.schemas.claim import ClaimCreate, ClaimResponse, ClaimStats
from app.services.email import send_claim_confirmation

router = APIRouter(prefix="/claim", tags=["claims"])

# Public waitlist signup, no credential guessing involved — a Redis outage
# fails open (S-10, app/core/ratelimit.py; reasoning in docs/api.md).
async def _rate_limit(request: Request) -> None:
    await ratelimit.enforce_ip(
        request,
        bucket="claim",
        limit=settings.rate_limit_claim,
        window_seconds=settings.rate_limit_claim_window_seconds,
        fail_open=True,
    )


@router.post("", response_model=ClaimResponse, status_code=status.HTTP_201_CREATED)
async def create_claim(
    payload: ClaimCreate,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> ClaimResponse:
    await _rate_limit(request)

    email = payload.email.lower().strip()

    existing = await db.execute(select(Claim).where(Claim.email == email))
    claim = existing.scalar_one_or_none()
    if claim is not None:
        response.status_code = status.HTTP_409_CONFLICT
        return ClaimResponse(position=claim.position)

    claim = Claim(
        email=email,
        entity_type=payload.entity_type,
        ready_to_pay=payload.ready_to_pay,
        source=payload.source,
    )
    db.add(claim)
    try:
        await db.commit()
    except IntegrityError:
        # Raced with a concurrent insert of the same email — return existing position
        await db.rollback()
        existing = await db.execute(select(Claim).where(Claim.email == email))
        claim = existing.scalar_one()
        response.status_code = status.HTTP_409_CONFLICT
        return ClaimResponse(position=claim.position)

    await db.refresh(claim)
    background.add_task(send_claim_confirmation, email, claim.position, claim.ready_to_pay)
    return ClaimResponse(position=claim.position)


@router.get("/stats", response_model=ClaimStats)
async def claim_stats(db: AsyncSession = Depends(get_db)) -> ClaimStats:
    result = await db.execute(
        select(
            func.count(Claim.id),
            func.count(Claim.id).filter(Claim.ready_to_pay.is_(True)),
        )
    )
    total, pay_ready = result.one()
    pct = round(100.0 * pay_ready / total, 1) if total else 0.0
    return ClaimStats(total=total, pay_ready=pay_ready, pay_ready_pct=pct)
