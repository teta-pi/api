import httpx
from rapidfuzz import fuzz

from app.services.registry.base import RegistryResult, RegistryVerifier


class CzechAresVerifier(RegistryVerifier):
    """
    Czech Republic — ARES (Ministry of Finance).
    Official state register, free REST API, no authentication.
    Covers all Czech economic subjects (s.r.o., a.s., OSVČ…).
    Docs: https://ares.gov.cz/swagger-ui/
    """

    registry_name = "ARES (Czech Republic)"
    country_code = "CZ"

    _SEARCH_URL = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty/vyhledat"
    _GET_URL = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty"

    async def search(self, company_name: str) -> list[RegistryResult]:
        async with httpx.AsyncClient(timeout=12.0) as client:
            try:
                resp = await client.post(
                    self._SEARCH_URL,
                    json={"obchodniJmeno": company_name, "pocet": 5, "start": 0},
                )
                resp.raise_for_status()
                data = resp.json()
            except (httpx.HTTPError, ValueError) as e:
                return [RegistryResult(found=False, registry=self.registry_name, error=str(e))]

        results = []
        for item in data.get("ekonomickeSubjekty", []):
            name = item.get("obchodniJmeno", "")
            score = fuzz.token_sort_ratio(
                self._normalize_name(company_name), self._normalize_name(name)
            )
            if score < 45:
                continue
            sidlo = item.get("sidlo") or {}
            results.append(
                RegistryResult(
                    found=True,
                    registry=self.registry_name,
                    registration_number=str(item.get("ico", "")),
                    legal_name=name,
                    status="active" if not item.get("datumZaniku") else "inactive",
                    founded=item.get("datumVzniku"),
                    address=sidlo.get("textovaAdresa"),
                    raw={"pravni_forma": item.get("pravniForma"), "dic": item.get("dic")},
                )
            )
        return results

    async def get_by_id(self, registration_number: str) -> RegistryResult | None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.get(f"{self._GET_URL}/{registration_number}")
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                item = resp.json()
            except (httpx.HTTPError, ValueError):
                return None
        return RegistryResult(
            found=True,
            registry=self.registry_name,
            registration_number=registration_number,
            legal_name=item.get("obchodniJmeno", ""),
            status="active" if not item.get("datumZaniku") else "inactive",
            founded=item.get("datumVzniku"),
            raw=item,
        )
