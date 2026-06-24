import asyncio

from app.services.registry.base import RegistryResult
from app.services.registry.ukraine_edr import UkraineEDRVerifier
from app.services.registry.germany_hr import GermanyHandelsregisterVerifier
from app.services.registry.companies_house import UKCompaniesHouseVerifier
from app.services.registry.gleif import GLEIFVerifier
from app.services.registry.us_sec import USSecVerifier

_VERIFIERS_BY_COUNTRY = {
    "UA": UkraineEDRVerifier(),
    "DE": GermanyHandelsregisterVerifier(),
    "GB": UKCompaniesHouseVerifier(),
    "US": USSecVerifier(),
}

_GLEIF = GLEIFVerifier()


async def verify_business_in_registry(
    company_name: str,
    country: str | None = None,
) -> list[RegistryResult]:
    """
    Search registries for a company name.
    Strategy:
    - Country-specific verifier (UA/DE/GB/US) when country is given.
    - GLEIF always runs (global open registry, covers EU + US + more).
    - Merge and deduplicate by registration number / legal name.
    """
    tasks = []
    country_upper = country.upper() if country else None

    if country_upper and country_upper in _VERIFIERS_BY_COUNTRY:
        tasks.append(_VERIFIERS_BY_COUNTRY[country_upper].search(company_name))

    tasks.append(_GLEIF.search(company_name, country=country))

    # If no country or non-specific country, also search US SEC
    if not country_upper or country_upper == "US":
        if not (country_upper == "US"):  # avoid double US search
            tasks.append(_VERIFIERS_BY_COUNTRY["US"].search(company_name))

    all_batches = await asyncio.gather(*tasks, return_exceptions=True)

    seen: set[str] = set()
    merged: list[RegistryResult] = []

    for batch in all_batches:
        if not isinstance(batch, list):
            continue
        for r in batch:
            if not r.found:
                continue
            key = r.registration_number or r.legal_name.lower()
            if key and key not in seen:
                seen.add(key)
                merged.append(r)

    def _sort_key(r: RegistryResult) -> int:
        if country_upper and r.registry not in ("GLEIF", "SEC EDGAR"):
            return 0
        if r.registry == "GLEIF":
            return 1
        return 2

    merged.sort(key=_sort_key)
    return merged
