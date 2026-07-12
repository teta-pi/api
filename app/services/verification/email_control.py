"""Business Email Control verification (docs/verification-rework.md §2).

Proves control of a mailbox on the brand's own domain via a 6-digit code —
same Redis-backed pattern as `/auth/email-code` / `/auth/verify-code`, reusing
`app.services.email.send_verification_code` (Resend). Namespaced under
`biz_email_code:*` so it can never collide with account sign-in codes; no
changes to routes/auth.py needed.
"""

import logging
import secrets

import redis.asyncio as aioredis
from fastapi import BackgroundTasks, HTTPException

from app.core.config import settings
from app.services.email import send_verification_code

logger = logging.getLogger(__name__)

_redis = aioredis.from_url(settings.redis_url, decode_responses=True)

_CODE_TTL = 900  # 15 minutes
_MAX_ATTEMPTS = 5

# A personal mailbox doesn't prove control of a brand's own domain.
_FREE_EMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com",
    "aol.com", "proton.me", "protonmail.com", "gmx.com", "mail.com",
}


def _domain_of(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower()


async def start_email_verification(email: str, background_tasks: BackgroundTasks) -> None:
    email = email.strip().lower()
    if _domain_of(email) in _FREE_EMAIL_DOMAINS:
        raise HTTPException(
            status_code=400,
            detail="Use an address on your business's own domain, not a personal mailbox",
        )

    cooldown_key = f"biz_email_code_cooldown:{email}"
    if await _redis.exists(cooldown_key):
        raise HTTPException(status_code=429, detail="Code already sent — wait a minute before retrying")

    code = f"{secrets.randbelow(1_000_000):06d}"
    await _redis.setex(f"biz_email_code:{email}", _CODE_TTL, code)
    await _redis.setex(cooldown_key, 60, "1")
    await _redis.delete(f"biz_email_code_attempts:{email}")

    background_tasks.add_task(send_verification_code, email, code)


async def confirm_email_verification(email: str, code: str) -> bool:
    """Returns True on a matching code. Raises 429 on brute-force; a bad/expired
    code just returns False (caller raises the 400)."""
    email = email.strip().lower()
    key = f"biz_email_code:{email}"
    attempts_key = f"biz_email_code_attempts:{email}"

    attempts = await _redis.incr(attempts_key)
    if attempts == 1:
        await _redis.expire(attempts_key, _CODE_TTL)
    if attempts > _MAX_ATTEMPTS:
        await _redis.delete(key)
        raise HTTPException(status_code=429, detail="Too many attempts — request a new code")

    stored = await _redis.get(key)
    if not stored or not secrets.compare_digest(stored, code.strip()):
        return False

    await _redis.delete(key, attempts_key)
    return True
