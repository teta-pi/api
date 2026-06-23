import asyncio
import logging
import uuid

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="categorize_business_ai")
def categorize_business_ai(business_id: str) -> dict:
    return asyncio.get_event_loop().run_until_complete(_categorize_async(business_id))


async def _categorize_async(business_id: str) -> dict:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.core.database import AsyncSessionLocal
    from app.models.business import Business
    from app.models.block import Block
    from app.services.ai import categorize_business, generate_embedding

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Business)
            .where(Business.id == uuid.UUID(business_id))
            .options(selectinload(Business.blocks))
        )
        business = result.scalar_one_or_none()
        if not business:
            return {"status": "not_found"}

        block_texts = [
            f"{b.title}: {b.description}" for b in business.blocks if b.title
        ]

        categories = await categorize_business(
            business.name, business.description, block_texts
        )
        business.ai_categories = categories

        # Generate embedding for semantic search
        full_text = " ".join([
            business.name or "",
            business.description or "",
            *block_texts,
        ])
        embedding = await generate_embedding(full_text)
        if embedding:
            # TODO: store embedding in pgvector column
            # business.embedding = embedding
            pass

        await db.commit()

    return {"status": "ok", "categories": categories}
