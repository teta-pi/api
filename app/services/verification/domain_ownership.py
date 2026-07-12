"""Domain Ownership verification (docs/verification-rework.md §2) — DNS TXT
or file-based check, same mechanism as the WordPress plugin. Runs inline in
the request (no worker): DNS is checked via DNS-over-HTTPS (Cloudflare JSON
API) rather than a resolver library, since httpx is already a dependency.
"""

import logging
import secrets
from urllib.parse import urlparse

import httpx
import redis.asyncio as aioredis

from app.core.config import settings

logger = logging.getLogger(__name__)

_redis = aioredis.from_url(settings.redis_url, decode_responses=True)

_TOKEN_TTL = 86400  # 24h to add the DNS record or upload the file
_TXT_PREFIX = "tetapi-verify"
_DOH_URL = "https://cloudflare-dns.com/dns-query"


def normalize_domain(raw: str) -> str:
    raw = raw.strip().lower()
    if "://" in raw:
        raw = urlparse(raw).netloc or raw
    return raw.split("/")[0].split(":")[0]


def _redis_key(business_id: str, domain: str) -> str:
    return f"domain_verify:{business_id}:{domain}"


async def start_domain_verification(business_id: str, domain: str) -> dict:
    domain = normalize_domain(domain)
    token = secrets.token_urlsafe(16)
    await _redis.setex(_redis_key(business_id, domain), _TOKEN_TTL, token)
    return {
        "domain": domain,
        "token": token,
        "dns_txt": {"host": f"_tetapi-verify.{domain}", "value": f"{_TXT_PREFIX}={token}"},
        "file": {"url": f"https://{domain}/.well-known/tetapi-verify.txt", "content": token},
        "expires_in": _TOKEN_TTL,
    }


async def _check_dns_txt(domain: str, token: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(
                _DOH_URL,
                params={"name": f"_tetapi-verify.{domain}", "type": "TXT"},
                headers={"accept": "application/dns-json"},
            )
            if r.status_code >= 400:
                return False
            answers = r.json().get("Answer", [])
    except Exception:
        return False
    expected = f"{_TXT_PREFIX}={token}"
    return any(expected in a.get("data", "").strip('"') for a in answers)


async def _check_file(domain: str, token: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(
                f"https://{domain}/.well-known/tetapi-verify.txt", follow_redirects=True
            )
            if r.status_code >= 400:
                return False
            return r.text.strip() == token
    except Exception:
        return False


async def check_domain_verification(business_id: str, domain: str) -> tuple[bool, str | None]:
    domain = normalize_domain(domain)
    key = _redis_key(business_id, domain)
    token = await _redis.get(key)
    if not token:
        return False, None

    if await _check_dns_txt(domain, token):
        await _redis.delete(key)
        return True, "dns_txt"
    if await _check_file(domain, token):
        await _redis.delete(key)
        return True, "file"
    return False, None
