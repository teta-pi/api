"""P — Provenance Integrity. Precomputed, stored in businesses.p_score (SystemSpec v2.1 §3.3)."""

import logging
import time
import uuid
from statistics import median

import httpx
from sqlalchemy import Float, cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.block import Block
from app.models.endpoint_probe import EndpointProbe
from app.models.verification_event import VerificationEvent

logger = logging.getLogger(__name__)

# Cached current Bitcoin block height, refreshed every 10 min (mempool.space)
_height_cache: dict = {"height": 0, "at": 0.0}
_HEIGHT_TTL = 600.0


async def current_btc_height() -> int:
    now = time.monotonic()
    if _height_cache["height"] and now - _height_cache["at"] < _HEIGHT_TTL:
        return _height_cache["height"]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get("https://mempool.space/api/blocks/tip/height")
            resp.raise_for_status()
            _height_cache["height"] = int(resp.text)
            _height_cache["at"] = now
    except (httpx.HTTPError, ValueError):
        logger.warning("Could not refresh BTC height; using cached %s", _height_cache["height"])
    return _height_cache["height"]


async def provenance_score(db: AsyncSession, entity_id: uuid.UUID) -> float:
    height = await current_btc_height()

    # Median Bitcoin timestamp depth over confirmed events; ~30 days of blocks caps at 1
    blocks_rows = (
        await db.execute(
            select(VerificationEvent.btc_block).where(
                VerificationEvent.entity_id == entity_id,
                VerificationEvent.ots_status == "confirmed",
                VerificationEvent.btc_block.is_not(None),
            )
        )
    ).scalars().all()
    if blocks_rows and height:
        depth = median(height - b for b in blocks_rows)
        d_norm = min(depth / 4320, 1.0)
    else:
        d_norm = 0.0

    # C2PA chain length caps at 10 signed blocks
    c2pa_count = (
        await db.execute(
            select(func.count(Block.id)).where(
                Block.business_id == entity_id, Block.c2pa_manifest.is_not(None)
            )
        )
    ).scalar_one()
    chain = min(c2pa_count / 10, 1.0)

    # Endpoint uptime over the last 30 days
    uptime_row = (
        await db.execute(
            select(func.avg(cast(EndpointProbe.ok, Float))).where(
                EndpointProbe.entity_id == entity_id,
                EndpointProbe.at > func.now() - text("interval '30 days'"),
            )
        )
    ).scalar_one()
    uptime = float(uptime_row) if uptime_row is not None else 0.0

    return 0.4 * d_norm + 0.3 * chain + 0.3 * uptime
