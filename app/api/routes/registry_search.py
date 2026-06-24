from fastapi import APIRouter, Query

from app.services.registry.router import verify_business_in_registry

router = APIRouter(prefix="/registry", tags=["registry"])


@router.get("/search")
async def registry_search(
    q: str = Query(..., min_length=2, description="Company name to search"),
    country: str | None = Query(None, description="ISO-2 country code filter (UA, DE, GB)"),
):
    """
    Synchronous search across government registries.
    Used in onboarding identify-step before a Business record is created.
    Returns up to 5 matches per registry.
    """
    results = await verify_business_in_registry(q, country)
    return [
        {
            "registration_number": r.registration_number,
            "legal_name": r.legal_name,
            "status": r.status,
            "founded": r.founded,
            "address": r.address,
            "registry": r.registry,
            "country": (r.raw or {}).get("country") or country or _infer_country(r.registry),
        }
        for r in results
        if r.found
    ]


def _infer_country(registry_name: str) -> str:
    low = registry_name.lower()
    if "ukraine" in low or "edr" in low:
        return "UA"
    if "handels" in low or "germany" in low:
        return "DE"
    if "companies house" in low or "uk" in low:
        return "GB"
    if "sec" in low or "edgar" in low:
        return "US"
    return ""
