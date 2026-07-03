import logging
import secrets

import redis.asyncio as aioredis
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import create_access_token, hash_password, verify_password
from app.core.config import settings
from app.core.database import get_db
from app.models.user import User
from app.schemas.user import LoginRequest, MagicLinkRequest, Token, UserCreate, UserOut
from app.services.email import send_verification_code

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

_redis = aioredis.from_url(settings.redis_url, decode_responses=True)

_CODE_TTL = 900  # 15 minutes
_MAX_ATTEMPTS = 5


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(payload: UserCreate, db: AsyncSession = Depends(get_db)) -> User:
    existing = await db.execute(select(User).where(User.email == payload.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password) if payload.password else None,
        full_name=payload.full_name,
        auth_provider=payload.auth_provider,
    )
    db.add(user)
    await db.flush()
    return user


@router.post("/token", response_model=Token)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()
    if not user or not user.hashed_password or not verify_password(
        payload.password, user.hashed_password
    ):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    return {"access_token": create_access_token(str(user.id)), "token_type": "bearer"}


async def _send_magic_link_email(email: str, token: str) -> None:
    """
    Send magic link via email provider.
    Currently supports: Resend (RESEND_API_KEY in .env).
    Falls back to logging if not configured.
    """
    magic_url = f"https://app.tetapi.dev/auth/magic?token={token}"
    api_key = getattr(settings, "resend_api_key", "")

    if api_key:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "from": "TETA+PI <noreply@tetapi.dev>",
                        "to": [email],
                        "subject": "Your TETA+PI verification link",
                        "html": f"""
                        <p>Click below to verify your identity on TETA+PI:</p>
                        <p><a href="{magic_url}" style="background:#6B3FA0;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600;">
                          Verify my identity →
                        </a></p>
                        <p style="color:#999;font-size:12px;">This link expires in 15 minutes.
                        If you didn't request this, ignore it.</p>
                        """,
                    },
                )
                resp.raise_for_status()
                logger.info("Magic link sent to %s via Resend", email)
        except Exception as e:
            logger.error("Failed to send magic link email to %s: %s", email, e)
    else:
        # No email provider configured — log for dev access
        logger.info("MAGIC LINK (no email provider): %s → %s", email, magic_url)


@router.post("/magic-link")
async def request_magic_link(
    payload: MagicLinkRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Passwordless email login.
    - Creates user if not exists.
    - Sends magic link via Resend (if RESEND_API_KEY configured) or logs URL.
    - Returns dev_token only when environment == "development".
    """
    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()

    if not user:
        user = User(email=payload.email, auth_provider="magic_link")
        db.add(user)
        await db.flush()
        await db.refresh(user)

    token = create_access_token(str(user.id))

    background_tasks.add_task(_send_magic_link_email, payload.email, token)

    is_dev = settings.environment == "development"
    return {
        "message": "Verification email sent — check your inbox.",
        "dev_token": token if is_dev else None,
    }


class EmailCodeRequest(BaseModel):
    email: EmailStr


class VerifyCodeRequest(BaseModel):
    email: EmailStr
    code: str


@router.post("/email-code")
async def request_email_code(
    payload: EmailCodeRequest,
    background_tasks: BackgroundTasks,
) -> dict:
    """Send a 6-digit verification code to the email. Code lives 15 min in Redis."""
    email = payload.email.lower().strip()

    # Max 1 send per 60s per email
    if await _redis.exists(f"email_code_cooldown:{email}"):
        raise HTTPException(status_code=429, detail="Code already sent — wait a minute before retrying")

    code = f"{secrets.randbelow(1_000_000):06d}"
    await _redis.setex(f"email_code:{email}", _CODE_TTL, code)
    await _redis.setex(f"email_code_cooldown:{email}", 60, "1")
    await _redis.delete(f"email_code_attempts:{email}")

    background_tasks.add_task(send_verification_code, email, code)
    return {"message": "Verification code sent — check your inbox."}


@router.post("/verify-code", response_model=Token)
async def verify_email_code(
    payload: VerifyCodeRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Check the 6-digit code; on success create the user (if new) and return a token."""
    email = payload.email.lower().strip()

    attempts = await _redis.incr(f"email_code_attempts:{email}")
    if attempts == 1:
        await _redis.expire(f"email_code_attempts:{email}", _CODE_TTL)
    if attempts > _MAX_ATTEMPTS:
        await _redis.delete(f"email_code:{email}")
        raise HTTPException(status_code=429, detail="Too many attempts — request a new code")

    stored = await _redis.get(f"email_code:{email}")
    if not stored or not secrets.compare_digest(stored, payload.code.strip()):
        raise HTTPException(status_code=400, detail="Invalid or expired code")

    await _redis.delete(f"email_code:{email}", f"email_code_attempts:{email}")

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if not user:
        user = User(email=email, auth_provider="email_code")
        db.add(user)
        await db.flush()
        await db.refresh(user)

    return {"access_token": create_access_token(str(user.id)), "token_type": "bearer"}


@router.post("/agent-key", response_model=Token)
async def create_agent_key(db: AsyncSession = Depends(get_db)) -> dict:
    """Create an API key for an AI agent account."""
    api_key = f"pk_live_{secrets.token_urlsafe(32)}"
    user = User(
        email=f"agent-{secrets.token_hex(8)}@teta-pi.agent",
        auth_provider="api_key",
        is_agent=True,
        api_key=api_key,
    )
    db.add(user)
    await db.flush()
    return {"access_token": create_access_token(str(user.id)), "token_type": "bearer"}
