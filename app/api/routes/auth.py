import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import create_access_token, hash_password, verify_password
from app.core.database import get_db
from app.models.user import User
from app.schemas.user import LoginRequest, MagicLinkRequest, Token, UserCreate, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


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


@router.post("/magic-link")
async def request_magic_link(
    payload: MagicLinkRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    """
    Send a magic link (passwordless email login).
    In production: generate a signed token, send via email provider (SendGrid/Resend).
    For Sprint 1: return the token directly (development only).
    """
    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()

    if not user:
        user = User(email=payload.email, auth_provider="magic_link")
        db.add(user)
        await db.flush()

    token = create_access_token(str(user.id))
    # TODO: send email with magic link instead of returning token
    return {
        "message": "Magic link sent",
        "dev_token": token if True else None,  # Remove in production
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
