from app.services.registry.base import RegistryResult
from app.services.registry.ukraine_edr import UkraineEDRVerifier
from app.services.registry.germany_hr import GermanyHandelsregisterVerifier
from app.services.registry.companies_house import UKCompaniesHouseVerifier

_VERIFIERS = {
    "UA": UkraineEDRVerifier(),
    "DE": GermanyHandelsregisterVerifier(),
    "GB": UKCompaniesHouseVerifier(),
}


async def verify_business_in_registry(
    company_name: str,
    country: str | None = None,
) -> list[RegistryResult]:
    """
    Search across registries. If country is given, only query that registry.
    Otherwise search all configured registries concurrently.
    """
    import asyncio

    if country and country.upper() in _VERIFIERS:
        verifier = _VERIFIERS[country.upper()]
        return await verifier.search(company_name)

    # Search all registries concurrently
    tasks = [v.search(company_name) for v in _VERIFIERS.values()]
    all_results = await asyncio.gather(*tasks, return_exceptions=True)

    merged: list[RegistryResult] = []
    for batch in all_results:
        if isinstance(batch, list):
            merged.extend(r for r in batch if r.found)
    return merged
