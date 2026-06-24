import httpx
from rapidfuzz import fuzz

from app.services.registry.base import RegistryResult, RegistryVerifier

_GLEIF_API = "https://api.gleif.org/api/v1"


class GLEIFVerifier(RegistryVerifier):
    """
    Global Legal Entity Identifier Foundation — fully open, no auth required.
    Covers 2M+ entities from 200+ jurisdictions via LEI codes.
    Uses /lei-records?filter[entity.names] for single-call search.
    """

    registry_name = "GLEIF"
    country_code = "GLOBAL"

    async def search(self, company_name: str, country: str | None = None) -> list[RegistryResult]:
        params: dict = {"filter[entity.names]": company_name, "page[size]": 5}
        if country:
            params["filter[entity.legalAddress.country]"] = country.upper()

        async with httpx.AsyncClient(timeout=12.0) as client:
            try:
                resp = await client.get(
                    f"{_GLEIF_API}/lei-records",
                    params=params,
                    headers={"Accept": "application/vnd.api+json"},
                )
                resp.raise_for_status()
                data = resp.json()
            except (httpx.HTTPError, ValueError) as e:
                return [RegistryResult(found=False, registry=self.registry_name, error=str(e))]

        results = []
        for record in data.get("data", []):
            attrs = record.get("attributes", {})
            entity = attrs.get("entity", {})
            legal_name = entity.get("legalName", {}).get("name", "")
            status = entity.get("status", "ACTIVE").lower()
            entity_country = entity.get("legalAddress", {}).get("country", "")
            region = entity.get("legalAddress", {}).get("region", "")
            city = entity.get("legalAddress", {}).get("city", "")
            address_lines = entity.get("legalAddress", {}).get("addressLines", [])
            address = ", ".join(filter(None, [
                address_lines[0] if address_lines else "",
                city,
                region,
                entity_country,
            ]))
            founded = entity.get("creationDate", "")
            if founded:
                founded = founded[:10]  # YYYY-MM-DD

            score = fuzz.token_sort_ratio(
                self._normalize_name(company_name),
                self._normalize_name(legal_name),
            )
            if score >= 45:
                results.append(
                    RegistryResult(
                        found=True,
                        registry=self.registry_name,
                        registration_number=record.get("id", ""),
                        legal_name=legal_name,
                        status=status,
                        founded=founded or None,
                        address=address,
                        raw={"lei": record.get("id"), "country": entity_country, "jurisdiction": entity.get("jurisdiction")},
                    )
                )
        return results

    async def get_by_id(self, registration_number: str) -> RegistryResult | None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                r = await client.get(
                    f"{_GLEIF_API}/lei-records/{registration_number}",
                    headers={"Accept": "application/vnd.api+json"},
                )
                r.raise_for_status()
                record = r.json().get("data", {})
            except (httpx.HTTPError, ValueError):
                return None

        attrs = record.get("attributes", {})
        entity = attrs.get("entity", {})
        return RegistryResult(
            found=True,
            registry=self.registry_name,
            registration_number=registration_number,
            legal_name=entity.get("legalName", {}).get("name", ""),
            status=entity.get("status", "ACTIVE").lower(),
            raw={"lei": registration_number, "country": entity.get("legalAddress", {}).get("country")},
        )
