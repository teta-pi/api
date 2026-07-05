import asyncio

from app.services.registry.base import RegistryResult
from app.services.registry.ukraine_edr import UkraineEDRVerifier
from app.services.registry.germany_hr import GermanyHandelsregisterVerifier
from app.services.registry.companies_house import UKCompaniesHouseVerifier
from app.services.registry.gleif import GLEIFVerifier
from app.services.registry.us_sec import USSecVerifier
from app.services.registry.opencorporates import OpenCorporatesVerifier
from app.services.registry.norway_brreg import NorwayBrregVerifier
from app.services.registry.france_re import FranceRechercheEntreprisesVerifier
from app.services.registry.czech_ares import CzechAresVerifier
from app.services.registry.finland_prh import FinlandPrhVerifier
from app.services.registry.us_states import USStateRegistriesVerifier
from app.services.registry.premium import NorthDataVerifier, OpendatabotVerifier

_VERIFIERS_BY_COUNTRY: dict[str, object] = {
    "UA": UkraineEDRVerifier(),
    "DE": GermanyHandelsregisterVerifier(),
    "GB": UKCompaniesHouseVerifier(),
    "US": USSecVerifier(),
    "NO": NorwayBrregVerifier(),
    "FR": FranceRechercheEntreprisesVerifier(),
    "CZ": CzechAresVerifier(),
    "FI": FinlandPrhVerifier(),
}

_US_STATES = USStateRegistriesVerifier()
# Commercial providers — no-ops until their API keys are configured
_NORTHDATA = NorthDataVerifier()
_OPENDATABOT = OpendatabotVerifier()

_GLEIF = GLEIFVerifier()
_OPENCORPORATES = OpenCorporatesVerifier()


async def verify_business_in_registry(
    company_name: str,
    country: str | None = None,
) -> list[RegistryResult]:
    """
    Search registries for a company name.

    Strategy (in order):
    1. Country-specific verifier when country is known (UA/DE/GB/US/NO).
    2. GLEIF — global LEI registry, covers multinational entities.
    3. OpenCorporates — 200+ jurisdictions fallback (FR/NL/ES/IT/PL/CA/AU/SG/…).
    4. US SEC EDGAR — US-specific public company data (no country or US).

    Results are merged and deduplicated by registration number / legal name.
    """
    country_upper = country.upper() if country else None
    tasks = []

    # 1. Country-specific verifier — or ALL free country registries when
    #    the country is unknown (claim-flow search sends no country)
    if country_upper and country_upper in _VERIFIERS_BY_COUNTRY:
        tasks.append(_VERIFIERS_BY_COUNTRY[country_upper].search(company_name))
    elif not country_upper:
        for _code, _verifier in _VERIFIERS_BY_COUNTRY.items():
            if _code == "US":
                continue  # SEC added below
            tasks.append(_verifier.search(company_name))

    # 2. GLEIF (always)
    tasks.append(_GLEIF.search(company_name, country=country))

    # 3. OpenCorporates — always as global fallback, with jurisdiction hint
    tasks.append(_OPENCORPORATES.search(company_name, country=country))

    # 4. SEC EDGAR for US or unknown country (avoids duplicate when US verifier already ran)
    if not country_upper or country_upper not in _VERIFIERS_BY_COUNTRY:
        tasks.append(_VERIFIERS_BY_COUNTRY["US"].search(company_name))

    # 5. US state registries (NY/CO open data) — SEC only covers public companies
    if not country_upper or country_upper == "US":
        tasks.append(_US_STATES.search(company_name))

    # 6. Commercial providers when licensed (deep DE/EU + full UA coverage)
    if _NORTHDATA.enabled and (not country_upper or country_upper == "DE"):
        tasks.append(_NORTHDATA.search(company_name))
    if _OPENDATABOT.enabled and (not country_upper or country_upper == "UA"):
        tasks.append(_OPENDATABOT.search(company_name))

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

    # Rank primarily by name similarity to the query, then keep
    # country-specific registries ahead of global fallbacks on ties.
    from rapidfuzz import fuzz

    _FALLBACK = {"GLEIF": 1, "OpenCorporates": 2}

    def _sort_key(r: RegistryResult) -> tuple:
        similarity = max(
            fuzz.token_sort_ratio(company_name.lower(), r.legal_name.lower()),
            fuzz.partial_ratio(company_name.lower(), r.legal_name.lower()),
        )
        fallback_rank = next(
            (v for k, v in _FALLBACK.items() if r.registry.startswith(k)), 0
        )
        return (-similarity, fallback_rank)

    merged.sort(key=_sort_key)
    return merged
