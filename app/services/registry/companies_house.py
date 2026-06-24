import httpx
from rapidfuzz import fuzz

from app.core.config import settings
from app.services.registry.base import RegistryResult, RegistryVerifier


class UKCompaniesHouseVerifier(RegistryVerifier):
    """
    UK Companies House — official public API.
    Requires free API key from https://developer.company-information.service.gov.uk
    """

    registry_name = "Companies House"
    country_code = "GB"

    async def search(self, company_name: str) -> list[RegistryResult]:
        if not settings.uk_companies_house_api_key:
            return [
                RegistryResult(
                    found=False,
                    registry=self.registry_name,
                    error="UK Companies House API key not configured",
                )
            ]
        async with httpx.AsyncClient(
            auth=(settings.uk_companies_house_api_key, ""),
            base_url=settings.uk_companies_house_api_url,
            timeout=10.0,
        ) as client:
            try:
                resp = await client.get(
                    "/search/companies",
                    params={"q": company_name, "items_per_page": 5},
                )
                resp.raise_for_status()
                data = resp.json()
            except (httpx.HTTPError, ValueError) as e:
                return [RegistryResult(found=False, registry=self.registry_name, error=str(e))]

        results = []
        for item in data.get("items", []):
            score = fuzz.token_sort_ratio(
                self._normalize_name(company_name),
                self._normalize_name(item.get("title", "")),
            )
            if score >= 60:
                results.append(
                    RegistryResult(
                        found=True,
                        registry=self.registry_name,
                        registration_number=item.get("company_number", ""),
                        legal_name=item.get("title", ""),
                        status=item.get("company_status", "active"),
                        address=item.get("address_snippet", ""),
                        raw=item,
                    )
                )
        return results

    async def get_by_id(self, registration_number: str) -> RegistryResult | None:
        if not settings.uk_companies_house_api_key:
            return None
        async with httpx.AsyncClient(
            auth=(settings.uk_companies_house_api_key, ""),
            base_url=settings.uk_companies_house_api_url,
            timeout=10.0,
        ) as client:
            try:
                resp = await client.get(f"/company/{registration_number}")
                resp.raise_for_status()
                item = resp.json()
            except httpx.HTTPError:
                return None

        return RegistryResult(
            found=True,
            registry=self.registry_name,
            registration_number=item.get("company_number", registration_number),
            legal_name=item.get("company_name", ""),
            status=item.get("company_status", "active"),
            address=item.get("registered_office_address", {}).get("address_line_1", ""),
            raw=item,
        )
