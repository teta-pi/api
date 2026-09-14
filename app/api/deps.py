import uuid

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import decode_token
from app.core.database import get_db
from app.models.user import User

security = HTTPBearer()

# Optional bearer: lets anonymous/agent readers through while still identifying
# the owner. auto_error=False → no Authorization header yields None, not a 403.
_optional_security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    token = credentials.credentials

    # Agent API keys ("pk_live_...") authenticate directly
    if token.startswith("pk_live_"):
        result = await db.execute(select(User).where(User.api_key == token))
        user = result.scalar_one_or_none()
        if not user or not user.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
        return user

    try:
        payload = decode_token(token)
        user_id = uuid.UUID(payload["sub"])
    except (ValueError, KeyError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if payload.get("ver", 0) != user.token_version:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired — sign in again")
    return user


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_optional_security),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Resolve the caller if a valid token is present, else None.

    Reuses get_current_user's full logic (API keys, token version) so anonymous
    and invalid-token requests fall through to the public view instead of 401.
    Used by every public-by-UUID read (S-8 blocks, S-17 entity row/preview/proof)
    so the owner sees their private data while everyone else gets the public view.
    """
    if credentials is None:
        return None
    try:
        return await get_current_user(credentials, db)
    except HTTPException:
        return None


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """Gate for back-office endpoints: role must be admin or support."""
    if user.role not in ("admin", "support"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user
