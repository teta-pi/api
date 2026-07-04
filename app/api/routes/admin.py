"""Back Office admin API (A2). Every endpoint requires admin/support role
and records an entry in the append-only admin_audit_log."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.database import get_db
from app.models.audit_log import AdminAuditLog
from app.models.business import Business
from app.models.claim import Claim
from app.models.user import User
from app.models.verification_event import VerificationEvent

router = APIRouter(prefix="/admin", tags=["admin"])


async def _audit(
    db: AsyncSession,
    actor: User,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    detail: dict | None = None,
) -> None:
    db.add(
        AdminAuditLog(
            actor_id=actor.id,
            actor_email=actor.email,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
        )
    )
    await db.commit()


# ── Stats ─────────────────────────────────────────────────────────────────────


@router.get("/stats")
async def admin_stats(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    users_total = (await db.execute(select(func.count(User.id)))).scalar_one()
    users_today = (
        await db.execute(
            select(func.count(User.id)).where(User.created_at > func.now() - text("interval '1 day'"))
        )
    ).scalar_one()
    users_week = (
        await db.execute(
            select(func.count(User.id)).where(User.created_at > func.now() - text("interval '7 days'"))
        )
    ).scalar_one()

    claims_total = (await db.execute(select(func.count(Claim.id)))).scalar_one()
    claims_pay_ready = (
        await db.execute(select(func.count(Claim.id)).where(Claim.ready_to_pay.is_(True)))
    ).scalar_one()

    entities_total = (await db.execute(select(func.count(Business.id)))).scalar_one()
    entities_by_level_rows = (
        await db.execute(
            select(Business.verification_level, func.count(Business.id)).group_by(
                Business.verification_level
            )
        )
    ).all()

    events_total = (await db.execute(select(func.count(VerificationEvent.id)))).scalar_one()

    await _audit(db, admin, "stats.view")
    return {
        "users": {"total": users_total, "today": users_today, "week": users_week},
        "claims": {
            "total": claims_total,
            "pay_ready": claims_pay_ready,
            "pay_ready_pct": round(100.0 * claims_pay_ready / claims_total, 1) if claims_total else 0.0,
        },
        "entities": {
            "total": entities_total,
            "by_level": {level: count for level, count in entities_by_level_rows},
        },
        "verification_events": events_total,
    }


# ── Users ─────────────────────────────────────────────────────────────────────


class AdminUserRow(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str | None
    auth_provider: str
    role: str
    is_active: bool
    is_agent: bool
    created_at: datetime
    entities_count: int


@router.get("/users")
async def list_users(
    q: str | None = Query(default=None, max_length=200),
    role: str | None = Query(default=None),
    active: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    stmt = select(User, func.count(Business.id).label("entities_count")).outerjoin(
        Business, Business.owner_id == User.id
    ).group_by(User.id)

    count_stmt = select(func.count(User.id))
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where((User.email.ilike(pattern)) | (User.full_name.ilike(pattern)))
        count_stmt = count_stmt.where((User.email.ilike(pattern)) | (User.full_name.ilike(pattern)))
    if role:
        stmt = stmt.where(User.role == role)
        count_stmt = count_stmt.where(User.role == role)
    if active is not None:
        stmt = stmt.where(User.is_active == active)
        count_stmt = count_stmt.where(User.is_active == active)

    total = (await db.execute(count_stmt)).scalar_one()
    rows = (
        await db.execute(stmt.order_by(User.created_at.desc()).limit(limit).offset(offset))
    ).all()

    await _audit(db, admin, "users.list", detail={"q": q, "role": role, "offset": offset})
    return {
        "total": total,
        "results": [
            AdminUserRow(
                id=u.id,
                email=u.email,
                full_name=u.full_name,
                auth_provider=u.auth_provider,
                role=u.role,
                is_active=u.is_active,
                is_agent=u.is_agent,
                created_at=u.created_at,
                entities_count=ec,
            )
            for u, ec in rows
        ],
    }


@router.get("/users/{user_id}")
async def get_user_detail(
    user_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    entities = (
        await db.execute(select(Business).where(Business.owner_id == user_id))
    ).scalars().all()
    entity_ids = [e.id for e in entities]
    events = []
    if entity_ids:
        events = (
            await db.execute(
                select(VerificationEvent)
                .where(VerificationEvent.entity_id.in_(entity_ids))
                .order_by(VerificationEvent.created_at.desc())
                .limit(50)
            )
        ).scalars().all()

    await _audit(db, admin, "users.view", target_type="user", target_id=str(user_id))
    return {
        "user": {
            "id": str(user.id),
            "email": user.email,
            "full_name": user.full_name,
            "auth_provider": user.auth_provider,
            "role": user.role,
            "is_active": user.is_active,
            "is_agent": user.is_agent,
            "created_at": user.created_at,
            "updated_at": user.updated_at,
        },
        "entities": [
            {
                "id": str(e.id),
                "name": e.name,
                "slug": e.slug,
                "entity_type": e.entity_type,
                "verification_level": e.verification_level,
                "registry_status": e.registry_status,
                "registry_id": e.registry_id,
                "country": e.country,
                "is_published": e.is_published,
                "is_public": e.is_public,
                "created_at": e.created_at,
            }
            for e in entities
        ],
        "verification_events": [
            {
                "id": str(ev.id),
                "entity_id": str(ev.entity_id),
                "event_type": ev.event_type,
                "level": ev.level,
                "source": ev.source,
                "ots_status": ev.ots_status,
                "btc_block": ev.btc_block,
                "created_at": ev.created_at,
            }
            for ev in events
        ],
    }


# ── Claims ────────────────────────────────────────────────────────────────────


@router.get("/claims")
async def list_claims(
    ready_to_pay: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    stmt = select(Claim)
    count_stmt = select(func.count(Claim.id))
    if ready_to_pay is not None:
        stmt = stmt.where(Claim.ready_to_pay == ready_to_pay)
        count_stmt = count_stmt.where(Claim.ready_to_pay == ready_to_pay)

    total = (await db.execute(count_stmt)).scalar_one()
    claims = (
        await db.execute(stmt.order_by(Claim.position.asc()).limit(limit).offset(offset))
    ).scalars().all()

    await _audit(db, admin, "claims.list", detail={"offset": offset})
    return {
        "total": total,
        "results": [
            {
                "id": str(c.id),
                "position": c.position,
                "email": c.email,
                "entity_type": c.entity_type,
                "ready_to_pay": c.ready_to_pay,
                "source": c.source,
                "created_at": c.created_at,
            }
            for c in claims
        ],
    }


# ── Entities ──────────────────────────────────────────────────────────────────


@router.get("/entities")
async def list_entities(
    q: str | None = Query(default=None, max_length=200),
    verification_level: str | None = Query(default=None),
    entity_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    stmt = select(Business, User.email).join(User, User.id == Business.owner_id)
    count_stmt = select(func.count(Business.id))
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(Business.name.ilike(pattern))
        count_stmt = count_stmt.where(Business.name.ilike(pattern))
    if verification_level:
        stmt = stmt.where(Business.verification_level == verification_level)
        count_stmt = count_stmt.where(Business.verification_level == verification_level)
    if entity_type:
        stmt = stmt.where(Business.entity_type == entity_type)
        count_stmt = count_stmt.where(Business.entity_type == entity_type)

    total = (await db.execute(count_stmt)).scalar_one()
    rows = (
        await db.execute(stmt.order_by(Business.created_at.desc()).limit(limit).offset(offset))
    ).all()

    await _audit(db, admin, "entities.list", detail={"q": q, "offset": offset})
    return {
        "total": total,
        "results": [
            {
                "id": str(e.id),
                "name": e.name,
                "slug": e.slug,
                "entity_type": e.entity_type,
                "segment": e.segment,
                "verification_level": e.verification_level,
                "registry_status": e.registry_status,
                "registry_id": e.registry_id,
                "country": e.country,
                "owner_email": owner_email,
                "is_published": e.is_published,
                "is_public": e.is_public,
                "t_score": e.t_score,
                "p_score": e.p_score,
                "created_at": e.created_at,
            }
            for e, owner_email in rows
        ],
    }


# ── Audit log (read-only view for admins) ─────────────────────────────────────


@router.get("/audit-log")
async def list_audit_log(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    total = (await db.execute(select(func.count(AdminAuditLog.id)))).scalar_one()
    rows = (
        await db.execute(
            select(AdminAuditLog).order_by(AdminAuditLog.created_at.desc()).limit(limit).offset(offset)
        )
    ).scalars().all()
    return {
        "total": total,
        "results": [
            {
                "id": str(r.id),
                "actor_email": r.actor_email,
                "action": r.action,
                "target_type": r.target_type,
                "target_id": r.target_id,
                "detail": r.detail,
                "created_at": r.created_at,
            }
            for r in rows
        ],
    }
