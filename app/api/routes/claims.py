import time
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.claim import Claim
from app.schemas.claim import ClaimCreate, ClaimResponse, ClaimStats

router = APIRouter(prefix="/claim", tags=["claims"])

# Simple in-memory rate limiter: 5 req/min per IP (LandingSpec v2.1 §02)
_RATE_LIMIT = 5
_RATE_WINDOW = 60.0
_hits: dict[str, list[float]] = defaultdict(list)


def _rate_limit(request: Request) -> None:
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "unknown")
    ip = ip.split(",")[0].strip()
    now = time.monotonic()
    window = [t for t in _hits[ip] if now - t < _RATE_WINDOW]
    if len(window) >= _RATE_LIMIT:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded")
    window.append(now)
    _hits[ip] = window


@router.post("", response_model=ClaimResponse, status_code=status.HTTP_201_CREATED)
async def create_claim(
    payload: ClaimCreate,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> ClaimResponse:
    _rate_limit(request)

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
