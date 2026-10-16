"""Business Email Control verification (docs/verification-rework.md §2).

Proves control of a mailbox on the brand's own domain via a 6-digit code —
same Redis-backed pattern as `/auth/email-code` / `/auth/verify-code`, reusing
`app.services.email.send_verification_code` (Resend). Namespaced under
`biz_email_code:*` so it can never collide with account sign-in codes; no
changes to routes/auth.py needed.
"""

import logging
import secrets

from fastapi import BackgroundTasks, HTTPException

from app.core import ratelimit
from app.core.config import settings
from app.core.redis import get_redis
from app.services.email import send_verification_code

logger = logging.getLogger(__name__)

_CODE_TTL = 900  # 15 minutes

# Atomic check-and-consume: GET then DEL as two separate awaited calls let
# two concurrent requests with the same still-valid code both pass the
# comparison before either delete lands, each recording its own
# verification_events row (audit #13). Only delete the code from inside the
# script, and only on a match, so a wrong guess still leaves the code in
# place for the remaining attempts (unlike a plain GETDEL, which would
# consume it on the first wrong guess too).
_CONFIRM_LUA = """
local stored = redis.call('GET', KEYS[1])
if not stored then return false end
if stored == ARGV[1] then
  redis.call('DEL', KEYS[1])
  return stored
end
return false
"""

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

    redis = await get_redis()

    # Brute-force-adjacent (spamming codes), same reasoning as the attempts
    # counter below — a Redis outage fails closed, not open (S-10, docs/api.md).
    await ratelimit.enforce(
        f"biz_email_code_cooldown:{email}", limit=1,
        window_seconds=settings.rate_limit_email_code_cooldown_seconds, fail_open=False,
    )

    code = f"{secrets.randbelow(1_000_000):06d}"
    await redis.setex(f"biz_email_code:{email}", _CODE_TTL, code)
    await redis.delete(f"biz_email_code_attempts:{email}")

    background_tasks.add_task(send_verification_code, email, code)


async def confirm_email_verification(email: str, code: str) -> bool:
    """Returns True on a matching code. Raises 429 on brute-force; a bad/expired
    code just returns False (caller raises the 400)."""
    email = email.strip().lower()
    redis = await get_redis()
    key = f"biz_email_code:{email}"
    attempts_key = f"biz_email_code_attempts:{email}"

    # This IS the brute-force protection against guessing the 6-digit code —
    # fails closed (503) on a Redis outage rather than going unlimited.
    allowed = await ratelimit.increment(
        attempts_key, limit=settings.rate_limit_email_code_max_attempts,
        window_seconds=settings.rate_limit_email_code_attempts_window_seconds, fail_open=False,
    )
    if not allowed:
        await redis.delete(key)
        raise HTTPException(status_code=429, detail="Too many attempts — request a new code")

    matched = await redis.eval(_CONFIRM_LUA, 1, key, code.strip())
    if not matched:
        return False

    await redis.delete(attempts_key)
    return True
