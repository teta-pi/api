import httpx
from rapidfuzz import fuzz

from app.services.registry.base import RegistryResult, RegistryVerifier


class FinlandPrhVerifier(RegistryVerifier):
    """
    Finland — PRH / YTJ open data (Finnish Patent and Registration Office).
    Official open data API, free, no authentication.
    Covers all Finnish companies (Oy, Oyj, Tmi…).
    Docs: https://avoindata.prh.fi/en/ytj/swagger-ui
    """

    registry_name = "PRH (Finland)"
    country_code = "FI"

    _SEARCH_URL = "https://avoindata.prh.fi/opendata-ytj-api/v3/companies"

    @staticmethod
    def _primary_name(item: dict) -> str:
        names = item.get("names") or []
        for n in names:
            # type "1" = current registered name
            if n.get("type") == "1" and not n.get("endDate"):
                return n.get("name", "")
        return names[0].get("name", "") if names else ""

    async def search(self, company_name: str) -> list[RegistryResult]:
        async with httpx.AsyncClient(timeout=12.0) as client:
            try:
                resp = await client.get(self._SEARCH_URL, params={"name": company_name})
                resp.raise_for_status()
                data = resp.json()
            except (httpx.HTTPError, ValueError) as e:
                return [RegistryResult(found=False, registry=self.registry_name, error=str(e))]

        results = []
        for item in (data.get("companies") or [])[:5]:
            name = self._primary_name(item)
            score = fuzz.token_sort_ratio(
                self._normalize_name(company_name), self._normalize_name(name)
            )
            if score < 45:
                continue
            business_id = (item.get("businessId") or {}).get("value", "")
            status = item.get("status", "")
            addresses = item.get("addresses") or []
            address = None
            if addresses:
                a = addresses[0]
                address = ", ".join(filter(None, [
                    a.get("street"), a.get("postCode"),
                    (a.get("postOffices") or [{}])[0].get("city") if a.get("postOffices") else None,
                ]))
            results.append(
                RegistryResult(
                    found=True,
                    registry=self.registry_name,
                    registration_number=business_id,
                    legal_name=name,
                    status="active" if status in ("2", "REGISTERED", "") else "inactive",
                    founded=item.get("registrationDate"),
                    address=address,
                    raw={"status_code": status},
                )
            )
        return results

    async def get_by_id(self, registration_number: str) -> RegistryResult | None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.get(self._SEARCH_URL, params={"businessId": registration_number})
                resp.raise_for_status()
                data = resp.json()
            except (httpx.HTTPError, ValueError):
                return None
        items = data.get("companies") or []
        if not items:
            return None
        item = items[0]
        return RegistryResult(
            found=True,
            registry=self.registry_name,
            registration_number=registration_number,
            legal_name=self._primary_name(item),
            status="active",
            founded=item.get("registrationDate"),
            raw=item,
        )
