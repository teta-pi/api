import httpx
from rapidfuzz import fuzz

from app.services.registry.base import RegistryResult, RegistryVerifier


class NorwayBrregVerifier(RegistryVerifier):
    """
    Norway Brønnøysund Register Centre (Brønnøysundregistrene).
    Free open JSON REST API, no authentication required.
    Covers all Norwegian companies (AS, ASA, ENK, ANS, etc.).
    Docs: https://data.brreg.no/enhetsregisteret/api/docs
    """

    registry_name = "Brønnøysundregistrene"
    country_code = "NO"

    _SEARCH_URL = "https://data.brreg.no/enhetsregisteret/api/enheter"

    async def search(self, company_name: str) -> list[RegistryResult]:
        async with httpx.AsyncClient(timeout=12.0, headers={"Accept": "application/json"}) as client:
            try:
                resp = await client.get(
                    self._SEARCH_URL,
                    params={"navn": company_name, "size": 5},
                )
                resp.raise_for_status()
                data = resp.json()
            except (httpx.HTTPError, ValueError) as e:
                return [RegistryResult(found=False, registry=self.registry_name, error=str(e))]

        items = data.get("_embedded", {}).get("enheter", [])
        results = []
        for item in items:
            name = item.get("navn", "")
            score = fuzz.token_sort_ratio(
                self._normalize_name(company_name),
                self._normalize_name(name),
            )
            if score >= 45:
                org_form = item.get("organisasjonsform", {}).get("kode", "")
                address_parts = item.get("forretningsadresse", {})
                address = ", ".join(filter(None, [
                    " ".join(address_parts.get("adresse", [])),
                    address_parts.get("postnummer", ""),
                    address_parts.get("poststed", ""),
                ]))
                results.append(
                    RegistryResult(
                        found=True,
                        registry=self.registry_name,
                        registration_number=str(item.get("organisasjonsnummer", "")),
                        legal_name=name,
                        status="active" if not item.get("konkurs") and not item.get("underAvvikling") else "inactive",
                        founded=item.get("stiftelsesdato") or item.get("registreringsdatoEnhetsregisteret"),
                        address=address,
                        raw={"org_form": org_form, **item},
                    )
                )
        return results

    async def get_by_id(self, registration_number: str) -> RegistryResult | None:
        async with httpx.AsyncClient(timeout=10.0, headers={"Accept": "application/json"}) as client:
            try:
                resp = await client.get(f"{self._SEARCH_URL}/{registration_number}")
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
            legal_name=item.get("navn", ""),
            status="active" if not item.get("konkurs") else "inactive",
            founded=item.get("stiftelsesdato"),
            raw=item,
        )
