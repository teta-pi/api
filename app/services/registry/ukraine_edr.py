import httpx
from rapidfuzz import fuzz

from app.core.config import settings
from app.services.registry.base import RegistryResult, RegistryVerifier


class UkraineEDRVerifier(RegistryVerifier):
    """
    Unified State Register (EDR) of Ukraine.
    Public API: https://usr.minjust.gov.ua
    """

    registry_name = "EDR Ukraine"
    country_code = "UA"

    async def search(self, company_name: str) -> list[RegistryResult]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.get(
                    f"{settings.ukraine_edr_api_url}/1.0/subjects",
                    params={"name": company_name, "page": 1},
                )
                resp.raise_for_status()
                data = resp.json()
            except (httpx.HTTPError, ValueError) as e:
                return [RegistryResult(found=False, registry=self.registry_name, error=str(e))]

        results = []
        for item in data.get("records", [])[:5]:
            score = fuzz.token_sort_ratio(
                self._normalize_name(company_name),
                self._normalize_name(item.get("name", "")),
            )
            if score >= 60:
                results.append(
                    RegistryResult(
                        found=True,
                        registry=self.registry_name,
                        registration_number=str(item.get("edrpou", "")),
                        legal_name=item.get("name", ""),
                        status=item.get("status", "active"),
                        address=item.get("address", ""),
                        raw=item,
                    )
                )
        return results

    async def get_by_id(self, registration_number: str) -> RegistryResult | None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.get(
                    f"{settings.ukraine_edr_api_url}/1.0/subjects/{registration_number}"
                )
                resp.raise_for_status()
                item = resp.json()
            except httpx.HTTPError:
                return None

        return RegistryResult(
            found=True,
            registry=self.registry_name,
            registration_number=str(item.get("edrpou", registration_number)),
            legal_name=item.get("name", ""),
            status=item.get("status", "active"),
            address=item.get("address", ""),
            raw=item,
        )
