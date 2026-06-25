import httpx
from rapidfuzz import fuzz

from app.core.config import settings
from app.services.registry.base import RegistryResult, RegistryVerifier

_OC_API = "https://api.opencorporates.com/v0.4"

# OpenCorporates jurisdiction codes for common countries
_COUNTRY_TO_JURISDICTION: dict[str, list[str]] = {
    "US": ["us_de", "us_ca", "us_ny", "us_fl", "us_tx", "us_wa"],
    "DE": ["de"],
    "FR": ["fr"],
    "NL": ["nl"],
    "AT": ["at"],
    "CH": ["ch"],
    "ES": ["es"],
    "IT": ["it"],
    "PL": ["pl"],
    "SE": ["se"],
    "NO": ["no"],
    "DK": ["dk"],
    "FI": ["fi"],
    "BE": ["be"],
    "PT": ["pt"],
    "CA": ["ca_on", "ca_bc", "ca_qc"],
    "AU": ["au"],
    "SG": ["sg"],
    "IE": ["ie"],
    "LU": ["lu"],
}


class OpenCorporatesVerifier(RegistryVerifier):
    """
    OpenCorporates — covers 200+ jurisdictions including US states and most EU.
    Free tier works without API key (rate limited). Set OPENCORPORATES_API_KEY for higher limits.
    """

    registry_name = "OpenCorporates"
    country_code = "MULTI"

    async def search(self, company_name: str, country: str | None = None) -> list[RegistryResult]:
        params: dict = {"q": company_name, "per_page": 5}
        if country:
            jurisdictions = _COUNTRY_TO_JURISDICTION.get(country.upper(), [country.lower()])
            params["jurisdiction_code"] = jurisdictions[0]

        api_key = getattr(settings, "opencorporates_api_key", "")
        if api_key:
            params["api_token"] = api_key

        async with httpx.AsyncClient(timeout=12.0) as client:
            try:
                resp = await client.get(f"{_OC_API}/companies/search", params=params)
                resp.raise_for_status()
                data = resp.json()
            except (httpx.HTTPError, ValueError) as e:
                return [RegistryResult(found=False, registry=self.registry_name, error=str(e))]

        companies = data.get("results", {}).get("companies", [])
        results = []
        for item in companies[:5]:
            co = item.get("company", {})
            name = co.get("name", "")
            score = fuzz.token_sort_ratio(
                self._normalize_name(company_name),
                self._normalize_name(name),
            )
            if score >= 45:
                jurisdiction = co.get("jurisdiction_code", "").upper()
                results.append(
                    RegistryResult(
                        found=True,
                        registry=f"OpenCorporates/{jurisdiction}",
                        registration_number=co.get("company_number", ""),
                        legal_name=name,
                        status=(co.get("current_status") or "active").lower(),
                        founded=co.get("incorporation_date"),
                        address=co.get("registered_address_in_full", ""),
                        raw=co,
                    )
                )
        return results

    async def get_by_id(self, registration_number: str) -> RegistryResult | None:
        return None
