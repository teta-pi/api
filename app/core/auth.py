from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from jwt import PyJWTError
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# HS256 with a symmetric server secret — never an asymmetric keypair, and the
# accepted algorithm is pinned here, not read from the token header. That is
# what kept PYSEC-2026-4298 (python-jose algorithm confusion via a DER-encoded
# public key used as the HMAC secret) unreachable for us; we moved off
# python-jose anyway in 15.10 because the advisory has no fix version and this
# is the authentication path. PyJWT emits byte-identical tokens for our payload,
# so sessions issued before the migration stay valid.
ALGORITHM = "HS256"


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def create_access_token(
    subject: str | Any,
    expires_delta: timedelta | None = None,
    token_version: int = 0,
) -> str:
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    )
    # "ver" claim lets "log out everywhere" invalidate previously issued tokens
    payload = {"sub": str(subject), "exp": expire, "ver": token_version}
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        # require sub+exp: every token we mint has both, so a crafted one that
        # omits "exp" (i.e. never expires) is refused rather than trusted.
        return jwt.decode(
            token,
            settings.secret_key,
            algorithms=[ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
    except PyJWTError:
        raise ValueError("Invalid token")
