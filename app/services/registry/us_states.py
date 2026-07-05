import asyncio

import httpx
from rapidfuzz import fuzz

from app.services.registry.base import RegistryResult, RegistryVerifier


class USStateRegistriesVerifier(RegistryVerifier):
    """
    US state-level business registries via official state open-data portals
    (Socrata). Free, no authentication, legally open data.

    Covered:
    - New York — Department of State, Active Corporations (data.ny.gov)
    - Colorado — Secretary of State, Business Entities (data.colorado.gov)

    Complements SEC EDGAR (public companies only) with state LLCs/corps.
    """

    registry_name = "US State Registries"
    country_code = "US"

    _NY_URL = "https://data.ny.gov/resource/n9v6-gdp6.json"
    _CO_URL = "https://data.colorado.gov/resource/4ykn-tg5h.json"

    async def _search_ny(self, client: httpx.AsyncClient, company_name: str) -> list[RegistryResult]:
        try:
            resp = await client.get(self._NY_URL, params={"$q": company_name, "$limit": 5})
            resp.raise_for_status()
            items = resp.json()
        except (httpx.HTTPError, ValueError):
            return []
        results = []
        for item in items:
            name = item.get("current_entity_name", "")
            score = fuzz.token_sort_ratio(self._normalize_name(company_name), self._normalize_name(name))
            if score < 45:
                continue
            results.append(
                RegistryResult(
                    found=True,
                    registry="NY Department of State",
                    registration_number=str(item.get("dos_id", "")),
                    legal_name=name,
                    status="active",
                    founded=(item.get("initial_dos_filing_date") or "")[:10] or None,
                    address=", ".join(filter(None, [item.get("county"), "NY, US"])),
                    raw={"entity_type": item.get("entity_type"), "jurisdiction": item.get("jurisdiction")},
                )
            )
        return results

    async def _search_co(self, client: httpx.AsyncClient, company_name: str) -> list[RegistryResult]:
        try:
            resp = await client.get(self._CO_URL, params={"$q": company_name, "$limit": 5})
            resp.raise_for_status()
            items = resp.json()
        except (httpx.HTTPError, ValueError):
            return []
        results = []
        for item in items:
            name = item.get("entityname", "")
            score = fuzz.token_sort_ratio(self._normalize_name(company_name), self._normalize_name(name))
            if score < 45:
                continue
            status = (item.get("entitystatus") or "").lower()
            results.append(
                RegistryResult(
                    found=True,
                    registry="Colorado Secretary of State",
                    registration_number=str(item.get("entityid", "")),
                    legal_name=name,
                    status="active" if "good standing" in status or status == "exists" else status or "unknown",
                    founded=(item.get("entityformdate") or "")[:10] or None,
                    address=", ".join(filter(None, [
                        item.get("principaladdress1"), item.get("principalcity"), "CO, US",
                    ])),
                    raw={"entity_type": item.get("entitytype")},
                )
            )
        return results

    async def search(self, company_name: str) -> list[RegistryResult]:
        async with httpx.AsyncClient(timeout=12.0) as client:
            batches = await asyncio.gather(
                self._search_ny(client, company_name),
                self._search_co(client, company_name),
                return_exceptions=True,
            )
        results: list[RegistryResult] = []
        for b in batches:
            if isinstance(b, list):
                results.extend(b)
        return results

    async def get_by_id(self, registration_number: str) -> RegistryResult | None:
        # State IDs are portal-specific; direct lookup not supported in v1
        return None
