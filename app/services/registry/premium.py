"""Commercial registry providers — legally used via paid API subscriptions.

Both are inactive until their API key appears in .env:
- NorthData (northdata.com) — deep coverage of DE + EU companies
- Opendatabot (opendatabot.ua) — full Ukrainian TOV/FOP coverage

Set NORTHDATA_API_KEY / OPENDATABOT_API_KEY on the server to activate.
"""

import httpx
from rapidfuzz import fuzz

from app.core.config import settings
from app.services.registry.base import RegistryResult, RegistryVerifier


class NorthDataVerifier(RegistryVerifier):
    """NorthData Power Search API — commercial licence required."""

    registry_name = "NorthData (DE/EU)"
    country_code = "DE"

    _SEARCH_URL = "https://www.northdata.com/_api/search/v1/power"

    @property
    def enabled(self) -> bool:
        return bool(settings.northdata_api_key)

    async def search(self, company_name: str) -> list[RegistryResult]:
        if not self.enabled:
            return []
        async with httpx.AsyncClient(timeout=12.0) as client:
            try:
                resp = await client.get(
                    self._SEARCH_URL,
                    params={"query": company_name, "countries": "DE", "limit": 5},
                    headers={"X-Api-Key": settings.northdata_api_key},
                )
                resp.raise_for_status()
                data = resp.json()
            except (httpx.HTTPError, ValueError) as e:
                return [RegistryResult(found=False, registry=self.registry_name, error=str(e))]

        results = []
        for item in data.get("results", []):
            company = item.get("company") or item
            name = company.get("name", {})
            name = name.get("name", "") if isinstance(name, dict) else str(name)
            score = fuzz.token_sort_ratio(self._normalize_name(company_name), self._normalize_name(name))
            if score < 45:
                continue
            register = company.get("register") or {}
            address = company.get("address") or {}
            results.append(
                RegistryResult(
                    found=True,
                    registry=self.registry_name,
                    registration_number=register.get("id", ""),
                    legal_name=name,
                    status=company.get("status", "active"),
                    address=", ".join(filter(None, [address.get("street"), address.get("postalCode"), address.get("city")])) or None,
                    raw={"register_court": register.get("court")},
                )
            )
        return results

    async def get_by_id(self, registration_number: str) -> RegistryResult | None:
        return None


class OpendatabotVerifier(RegistryVerifier):
    """Opendatabot API v3 — commercial licence required."""

    registry_name = "Opendatabot (UA)"
    country_code = "UA"

    _SEARCH_URL = "https://opendatabot.com/api/v3/search"

    @property
    def enabled(self) -> bool:
        return bool(settings.opendatabot_api_key)

    async def search(self, company_name: str) -> list[RegistryResult]:
        if not self.enabled:
            return []
        async with httpx.AsyncClient(timeout=12.0) as client:
            try:
                resp = await client.get(
                    self._SEARCH_URL,
                    params={"text": company_name, "apiKey": settings.opendatabot_api_key},
                )
                resp.raise_for_status()
                data = resp.json()
            except (httpx.HTTPError, ValueError) as e:
                return [RegistryResult(found=False, registry=self.registry_name, error=str(e))]

        items = (data.get("data") or {}).get("items") or data.get("items") or []
        results = []
        for item in items[:5]:
            name = item.get("full_name") or item.get("name") or ""
            score = fuzz.token_sort_ratio(self._normalize_name(company_name), self._normalize_name(name))
            if score < 45:
                continue
            results.append(
                RegistryResult(
                    found=True,
                    registry=self.registry_name,
                    registration_number=str(item.get("code", "")),
                    legal_name=name,
                    status=item.get("status", ""),
                    address=item.get("location"),
                    raw={"ceo": item.get("ceo_name")},
                )
            )
        return results

    async def get_by_id(self, registration_number: str) -> RegistryResult | None:
        return None
