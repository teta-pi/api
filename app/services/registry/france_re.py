import httpx
from rapidfuzz import fuzz

from app.services.registry.base import RegistryResult, RegistryVerifier


class FranceRechercheEntreprisesVerifier(RegistryVerifier):
    """
    France — API Recherche d'entreprises (api.gouv.fr).
    Official government API, free, no authentication.
    Covers all French companies (SIRENE base: SA, SARL, SAS, micro…).
    Docs: https://recherche-entreprises.api.gouv.fr/docs/
    """

    registry_name = "SIRENE (France)"
    country_code = "FR"

    _SEARCH_URL = "https://recherche-entreprises.api.gouv.fr/search"

    async def search(self, company_name: str) -> list[RegistryResult]:
        async with httpx.AsyncClient(timeout=12.0) as client:
            try:
                resp = await client.get(
                    self._SEARCH_URL,
                    params={"q": company_name, "per_page": 5, "page": 1},
                )
                resp.raise_for_status()
                data = resp.json()
            except (httpx.HTTPError, ValueError) as e:
                return [RegistryResult(found=False, registry=self.registry_name, error=str(e))]

        results = []
        for item in data.get("results", []):
            name = item.get("nom_complet") or item.get("nom_raison_sociale") or ""
            score = fuzz.token_sort_ratio(
                self._normalize_name(company_name), self._normalize_name(name)
            )
            if score < 45:
                continue
            siege = item.get("siege") or {}
            address = ", ".join(filter(None, [
                siege.get("adresse"),
                siege.get("code_postal"),
                siege.get("libelle_commune"),
            ]))
            results.append(
                RegistryResult(
                    found=True,
                    registry=self.registry_name,
                    registration_number=str(item.get("siren", "")),
                    legal_name=name,
                    status="active" if item.get("etat_administratif") == "A" else "inactive",
                    founded=item.get("date_creation"),
                    address=address or None,
                    raw={"nature_juridique": item.get("nature_juridique"), "siren": item.get("siren")},
                )
            )
        return results

    async def get_by_id(self, registration_number: str) -> RegistryResult | None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.get(self._SEARCH_URL, params={"q": registration_number, "per_page": 1})
                resp.raise_for_status()
                data = resp.json()
            except (httpx.HTTPError, ValueError):
                return None
        items = data.get("results", [])
        if not items:
            return None
        item = items[0]
        return RegistryResult(
            found=True,
            registry=self.registry_name,
            registration_number=str(item.get("siren", "")),
            legal_name=item.get("nom_complet", ""),
            status="active" if item.get("etat_administratif") == "A" else "inactive",
            founded=item.get("date_creation"),
            raw=item,
        )
