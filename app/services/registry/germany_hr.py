import httpx
from rapidfuzz import fuzz

from app.services.registry.base import RegistryResult, RegistryVerifier


class GermanyHandelsregisterVerifier(RegistryVerifier):
    """
    Germany company search via offenes-register.de (open-source, JSON, no auth required).
    Covers HRB/HRA entries from Handelsregister. Falls back to the official HR portal
    HTML scrape only if the open API is unavailable.
    """

    registry_name = "Handelsregister"
    country_code = "DE"

    _OPEN_URL = "https://api.offeneregister.de/companies"
    _HR_URL = "https://www.handelsregister.de/rp_web/search"

    async def search(self, company_name: str) -> list[RegistryResult]:
        results = await self._search_offenes_register(company_name)
        if results:
            return results
        return await self._search_hr_portal(company_name)

    async def _search_offenes_register(self, company_name: str) -> list[RegistryResult]:
        async with httpx.AsyncClient(timeout=12.0) as client:
            try:
                resp = await client.get(
                    self._OPEN_URL,
                    params={"search": company_name, "limit": 5},
                )
                resp.raise_for_status()
                data = resp.json()
            except (httpx.HTTPError, ValueError):
                return []

        results = []
        items = data if isinstance(data, list) else data.get("results", [])
        for item in items[:5]:
            name = item.get("name", "")
            score = fuzz.token_sort_ratio(
                self._normalize_name(company_name),
                self._normalize_name(name),
            )
            if score >= 45:
                results.append(
                    RegistryResult(
                        found=True,
                        registry=self.registry_name,
                        registration_number=item.get("registered_number") or item.get("native_company_number", ""),
                        legal_name=name,
                        status=(item.get("current_status") or "active").lower(),
                        founded=item.get("registered_at") or item.get("incorporation_date"),
                        address=item.get("registered_address") or "",
                        raw=item,
                    )
                )
        return results

    async def _search_hr_portal(self, company_name: str) -> list[RegistryResult]:
        import re
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            try:
                resp = await client.post(
                    self._HR_URL,
                    data={
                        "schlagwoerter": company_name,
                        "schlagwortOptionen": "2",
                        "registerArt": "HRB",
                        "maxErgebnisse": "5",
                    },
                )
                resp.raise_for_status()
            except httpx.HTTPError as e:
                return [RegistryResult(found=False, registry=self.registry_name, error=str(e))]

        results = []
        pattern = r"<td[^>]*>([^<]*(?:GmbH|AG|KG|OHG|e\.V\.|SE|UG)[^<]*)</td>"
        matches = re.findall(pattern, resp.text, re.IGNORECASE)
        for match in matches[:5]:
            name = match.strip()
            score = fuzz.token_sort_ratio(self._normalize_name(company_name), self._normalize_name(name))
            if score >= 50:
                hrb_match = re.search(r"(HRB|HRA)\s*(\d+)", resp.text)
                reg_num = f"{hrb_match.group(1)}-{hrb_match.group(2)}" if hrb_match else ""
                results.append(
                    RegistryResult(
                        found=True,
                        registry=self.registry_name,
                        registration_number=reg_num,
                        legal_name=name,
                        status="active",
                        raw={"source": "handelsregister.de", "name": name},
                    )
                )
        return results

    async def get_by_id(self, registration_number: str) -> RegistryResult | None:
        return RegistryResult(
            found=True,
            registry=self.registry_name,
            registration_number=registration_number,
            legal_name="",
            status="requires_manual_verification",
            raw={"registration_number": registration_number},
        )
