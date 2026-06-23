import asyncio
import logging
import uuid
from datetime import datetime, timezone

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="verify_registry", bind=True, max_retries=3)
def verify_registry_task(self, business_id: str, company_name: str, country: str | None) -> dict:
    """
    Background task: search company in government registries and update Business record.
    """
    return asyncio.get_event_loop().run_until_complete(
        _verify_registry_async(business_id, company_name, country)
    )


async def _verify_registry_async(
    business_id: str, company_name: str, country: str | None
) -> dict:
    from sqlalchemy import select
    from app.core.database import AsyncSessionLocal
    from app.models.business import Business
    from app.services.registry import verify_business_in_registry

    results = await verify_business_in_registry(company_name, country)
    found = [r for r in results if r.found]

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Business).where(Business.id == uuid.UUID(business_id))
        )
        business = result.scalar_one_or_none()
        if not business:
            return {"status": "business_not_found"}

        if not found:
            business.registry_status = "failed"
        elif len(found) == 1:
            r = found[0]
            business.registry_status = "verified"
            business.registry_id = r.registration_number
            business.country = business.country or r.raw.get("country")
            business.registry_data = {
                "registry": r.registry,
                "registration_number": r.registration_number,
                "legal_name": r.legal_name,
                "status": r.status,
                "founded": r.founded,
                "address": r.address,
                "verified_at": datetime.now(timezone.utc).isoformat(),
            }
            if business.verification_level == "none":
                business.verification_level = "registry"
            business.is_published = True
        else:
            business.registry_status = "multiple_matches"

        await db.commit()

    logger.info("Registry verification for %s: %s matches", business_id, len(found))
    return {"status": "ok", "matches": len(found)}
