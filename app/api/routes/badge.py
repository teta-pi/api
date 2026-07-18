import hashlib
import time
from collections import defaultdict
from xml.sax.saxutils import escape as xml_escape

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.redis import get_redis
from app.models.business import Business

router = APIRouter(prefix="/badge", tags=["badge"])

_CACHE_CONTROL = "public, max-age=3600"

# Simple in-memory rate limiter, same pattern as /claim (LandingSpec v2.1 §02).
# Generous limit: this is a public, high-fanout asset endpoint — every README
# render hits it, and legitimate traffic can arrive from a handful of IPs
# (e.g. GitHub's camo proxy, which itself caches the image).
_RATE_LIMIT = 120
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


def _text_width(text: str) -> int:
    # Rough monospace-ish estimate (shields.io-style), good enough for a
    # generated badge — no font-metrics dependency.
    return round(len(text) * 6.7) + 20


def _render_svg(label: str, status_text: str, color: str) -> str:
    # label comes from user-supplied Business.name — must be XML-escaped,
    # both to keep the SVG well-formed (names with & / < / >) and because a
    # direct navigation to this URL renders the SVG as a document, not just
    # an <img>.
    # escape() alone leaves " unescaped, which breaks the double-quoted
    # aria-label attribute when the entity name contains one.
    label_esc = xml_escape(label, {'"': "&quot;"})
    status_esc = xml_escape(status_text, {'"': "&quot;"})
    label_w = _text_width(label)
    status_w = _text_width(status_text)
    width = label_w + status_w
    height = 20

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" role="img" aria-label="{label_esc}: {status_esc}">
  <linearGradient id="s" x2="0" y2="100%">
    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
    <stop offset="1" stop-opacity=".1"/>
  </linearGradient>
  <clipPath id="r">
    <rect width="{width}" height="{height}" rx="3" fill="#fff"/>
  </clipPath>
  <g clip-path="url(#r)">
    <rect width="{label_w}" height="{height}" fill="#555"/>
    <rect x="{label_w}" width="{status_w}" height="{height}" fill="{color}"/>
    <rect width="{width}" height="{height}" fill="url(#s)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,sans-serif" font-size="11">
    <text x="{label_w / 2}" y="14">{label_esc}</text>
    <text x="{label_w + status_w / 2}" y="14">{status_esc}</text>
  </g>
</svg>"""


def _svg_response(svg: str, request: Request, status_code: int = 200) -> Response:
    etag = f'"{hashlib.md5(svg.encode()).hexdigest()}"'
    headers = {"Cache-Control": _CACHE_CONTROL, "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return Response(content=svg, media_type="image/svg+xml", status_code=status_code, headers=headers)


@router.get("/{entity_id}")
async def get_badge(
    entity_id: str,
    request: Request,
    style: str = Query(default="flat"),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Public, unauthenticated SVG badge for README embedding (GTM Phase 3
    badge loop). No auth by design — cache headers + a cheap Redis counter
    (not a full audit-log row) keep the high-fanout read cost low."""
    _rate_limit(request)

    result = await db.execute(
        select(Business).where(
            (Business.slug == entity_id) | (Business.id.cast("text") == entity_id),
            Business.is_published == True,  # noqa: E712
            Business.is_public == True,  # noqa: E712
        )
    )
    business = result.scalar_one_or_none()

    if business is None:
        svg = _render_svg("TETA+PI", "unknown", "#6e7681")
        return _svg_response(svg, request, status_code=status.HTTP_404_NOT_FOUND)

    verified = business.verification_level != "none"
    status_text = "verified" if verified else "unverified"
    color = "#2ea44f" if verified else "#6e7681"
    svg = _render_svg(business.name, status_text, color)

    redis = await get_redis()
    await redis.incr(f"badge_impressions:{business.id}")

    return _svg_response(svg, request)
