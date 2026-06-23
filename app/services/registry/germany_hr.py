import httpx
from rapidfuzz import fuzz

from app.services.registry.base import RegistryResult, RegistryVerifier


class GermanyHandelsregisterVerifier(RegistryVerifier):
    """
    Germany Handelsregister.
    Public portal: https://www.handelsregister.de
    Note: full API access requires registration; this implementation uses the
    public search endpoint. For production, integrate with the official API or
    use the EBR (European Business Register) network as a fallback.
    """

    registry_name = "Handelsregister"
    country_code = "DE"

    # Handelsregister public search (unofficial endpoint used by research tools)
    _SEARCH_URL = "https://www.handelsregister.de/rp_web/search"

    async def search(self, company_name: str) -> list[RegistryResult]:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            try:
                resp = await client.post(
                    self._SEARCH_URL,
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

        # Parse HTML response (the HR portal returns HTML, not JSON)
        results = self._parse_hr_html(resp.text, company_name)
        return results

    def _parse_hr_html(self, html: str, query: str) -> list[RegistryResult]:
        """Extract company entries from Handelsregister HTML response."""
        import re

        results = []
        # Look for table rows with company data (format varies by portal version)
        pattern = r"<td[^>]*>([^<]*(?:GmbH|AG|KG|OHG|e\.V\.|SE|UG)[^<]*)</td>"
        matches = re.findall(pattern, html, re.IGNORECASE)
        for match in matches[:5]:
            name = match.strip()
            score = fuzz.token_sort_ratio(self._normalize_name(query), self._normalize_name(name))
            if score >= 50:
                hrb_match = re.search(r"(HRB|HRA)\s*(\d+)", html)
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
        # Handelsregister lookup by number requires authenticated access
        # Return a structured result indicating manual verification is needed
        return RegistryResult(
            found=True,
            registry=self.registry_name,
            registration_number=registration_number,
            legal_name="",
            status="requires_manual_verification",
            raw={"registration_number": registration_number},
        )
