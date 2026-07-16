import logging
import secrets

import redis.asyncio as aioredis
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import create_access_token, hash_password, verify_password
from app.core.config import settings
from app.core.database import get_db
from app.models.user import User
from app.schemas.user import LoginRequest, MagicLinkRequest, Token
from app.api.deps import get_current_user
from app.services.email import send_verification_code

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

_redis = aioredis.from_url(settings.redis_url, decode_responses=True)

_CODE_TTL = 900  # 15 minutes
_MAX_ATTEMPTS = 5


@router.post("/token", response_model=Token)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()
    if not user or not user.hashed_password or not verify_password(
        payload.password, user.hashed_password
    ):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    return {"access_token": create_access_token(str(user.id), token_version=user.token_version), "token_type": "bearer"}


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

    token = create_access_token(str(user.id), token_version=user.token_version)

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
async def request_email_code(payload: EmailCodeRequest) -> dict:
    """Send a 6-digit verification code to the email. Code lives 15 min in Redis."""
    email = payload.email.lower().strip()

    # Max 1 send per 60s per email
    if await _redis.exists(f"email_code_cooldown:{email}"):
        raise HTTPException(status_code=429, detail="Code already sent — wait a minute before retrying")

    code = f"{secrets.randbelow(1_000_000):06d}"
    await _redis.setex(f"email_code:{email}", _CODE_TTL, code)
    await _redis.setex(f"email_code_cooldown:{email}", 60, "1")
    await _redis.delete(f"email_code_attempts:{email}")

    sent = await send_verification_code(email, code)
    if not sent:
        # Don't leave the user locked out by the cooldown for a code that never arrived.
        await _redis.delete(f"email_code:{email}", f"email_code_cooldown:{email}")
        raise HTTPException(status_code=502, detail="Could not send email, try again later")

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

    return {"access_token": create_access_token(str(user.id), token_version=user.token_version), "token_type": "bearer"}


class SetPasswordRequest(BaseModel):
    password: str


@router.post("/set-password")
async def set_password(
    payload: SetPasswordRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Set or change the account password (key login via /auth/token)."""
    if len(payload.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    user.hashed_password = hash_password(payload.password)
    await db.commit()
    return {"message": "Password set — you can now sign in with email + password."}


class ChangeEmailRequest(BaseModel):
    new_email: EmailStr


class ConfirmEmailChangeRequest(BaseModel):
    new_email: EmailStr
    code: str


@router.post("/change-email")
async def request_email_change(
    payload: ChangeEmailRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Step 1: send a verification code to the NEW address."""
    new_email = payload.new_email.lower().strip()
    existing = await db.execute(select(User).where(User.email == new_email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="This email is already in use")

    if await _redis.exists(f"email_code_cooldown:{new_email}"):
        raise HTTPException(status_code=429, detail="Code already sent — wait a minute")

    code = f"{secrets.randbelow(1_000_000):06d}"
    await _redis.setex(f"email_code:{new_email}", _CODE_TTL, code)
    await _redis.setex(f"email_code_cooldown:{new_email}", 60, "1")
    # Bind the pending change to this user so nobody else can claim the code
    await _redis.setex(f"email_change_owner:{new_email}", _CODE_TTL, str(user.id))

    background_tasks.add_task(send_verification_code, new_email, code)
    return {"message": "Verification code sent to the new address."}


@router.post("/confirm-email-change")
async def confirm_email_change(
    payload: ConfirmEmailChangeRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Step 2: verify the code and switch the account email."""
    new_email = payload.new_email.lower().strip()

    owner = await _redis.get(f"email_change_owner:{new_email}")
    if owner != str(user.id):
        raise HTTPException(status_code=400, detail="No pending email change for this address")

    stored = await _redis.get(f"email_code:{new_email}")
    if not stored or not secrets.compare_digest(stored, payload.code.strip()):
        raise HTTPException(status_code=400, detail="Invalid or expired code")

    await _redis.delete(f"email_code:{new_email}", f"email_change_owner:{new_email}")
    user.email = new_email
    await db.commit()
    return {"message": "Email updated.", "email": new_email}


@router.post("/logout-all", response_model=Token)
async def logout_everywhere(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Invalidate every issued token; returns a fresh one for this session."""
    user.token_version += 1
    await db.commit()
    return {
        "access_token": create_access_token(str(user.id), token_version=user.token_version),
        "token_type": "bearer",
    }


@router.post("/delete-account")
async def delete_own_account(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """GDPR self-service erasure: wipe PII, deactivate, unpublish entities.
    Bitcoin-anchored verification events remain (documented in privacy policy)."""
    if user.role == "admin":
        raise HTTPException(status_code=400, detail="Admin accounts cannot self-delete")

    from app.models.business import Business
    from app.models.claim import Claim

    old_email = user.email
    user.email = f"deleted-{user.id.hex[:12]}@anon.tetapi.dev"
    user.full_name = None
    user.hashed_password = None
    user.api_key = None
    user.is_active = False
    user.token_version += 1  # kill all sessions

    claims = (await db.execute(select(Claim).where(Claim.email == old_email))).scalars().all()
    for c in claims:
        c.email = user.email
        c.source = None

    entities = (
        await db.execute(select(Business).where(Business.owner_id == user.id))
    ).scalars().all()
    for e in entities:
        e.is_published = False
        e.is_public = False

    await db.commit()
    return {"status": "deleted"}


@router.post("/personal-api-key")
async def generate_personal_api_key(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Generate (or rotate) a personal API key. Shown once — store it safely."""
    api_key = f"pk_live_{secrets.token_urlsafe(32)}"
    user.api_key = api_key
    await db.commit()
    return {"api_key": api_key, "note": "Shown once. Rotating invalidates the previous key."}


@router.post("/avatar")
async def upload_avatar(
    file: UploadFile,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Upload an account avatar (PNG/JPEG/WebP, max 2 MB)."""
    if file.content_type not in ("image/png", "image/jpeg", "image/webp"):
        raise HTTPException(status_code=400, detail="Only PNG, JPEG or WebP allowed")
    content = await file.read()
    if len(content) > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Max 2 MB")

    from app.api.routes.media import _save_local

    ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[file.content_type]
    user.avatar_url = _save_local(content, f"avatar.{ext}")
    await db.commit()
    return {"avatar_url": user.avatar_url}


@router.get("/me")
async def get_me(user: User = Depends(get_current_user)) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "avatar_url": user.avatar_url,
        "has_password": user.hashed_password is not None,
        "has_api_key": user.api_key is not None,
    }


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
