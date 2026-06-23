import asyncio
import logging
import uuid

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="submit_bitcoin_timestamp", bind=True, max_retries=3)
def submit_bitcoin_timestamp(self, media_id: str, content_hex: str) -> dict:
    return asyncio.get_event_loop().run_until_complete(
        _submit_async(media_id, content_hex)
    )


async def _submit_async(media_id: str, content_hex: str) -> dict:
    from sqlalchemy import select
    from app.core.database import AsyncSessionLocal
    from app.models.media import Media
    from app.services.bitcoin import submit_hash

    content = bytes.fromhex(content_hex)
    proof_bytes = await submit_hash(content)

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Media).where(Media.id == uuid.UUID(media_id)))
        media = result.scalar_one_or_none()
        if media and proof_bytes:
            media.bitcoin_proof = proof_bytes
            await db.commit()

    return {"status": "submitted" if proof_bytes else "failed"}


@celery_app.task(name="check_bitcoin_confirmations")
def check_bitcoin_confirmations() -> dict:
    """Periodic task: check pending OTS proofs for Bitcoin confirmation."""
    return asyncio.get_event_loop().run_until_complete(_check_confirmations_async())


async def _check_confirmations_async() -> dict:
    from sqlalchemy import select
    from app.core.database import AsyncSessionLocal
    from app.models.media import Media
    from app.services.bitcoin import verify_proof

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Media).where(Media.bitcoin_proof != None, Media.bitcoin_confirmed == False)  # noqa
        )
        pending = list(result.scalars().all())

    confirmed = 0
    for media in pending:
        if not media.bitcoin_proof:
            continue
        verification = await verify_proof(media.bitcoin_proof, b"")  # proof-only check
        if verification["confirmed"]:
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Media).where(Media.id == media.id))
                m = result.scalar_one_or_none()
                if m:
                    m.bitcoin_confirmed = True
                    m.bitcoin_block = verification["bitcoin_block"]
                    await db.commit()
            confirmed += 1

    return {"checked": len(pending), "confirmed": confirmed}
