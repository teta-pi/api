import re
from datetime import datetime

import httpx
from rapidfuzz import fuzz

from app.services.registry.base import RegistryResult, RegistryVerifier

_EDGAR_SEARCH = "https://efts.sec.gov/LATEST/search-index"
_EDGAR_COMPANY = "https://data.sec.gov/submissions/CIK{cik}.json"
_HEADERS = {"User-Agent": "TETA+PI registry-search api@tetapi.dev"}

_DISPLAY_NAME_RE = re.compile(r"^(.+?)\s+\(([A-Z]{1,5})\)\s+\(CIK\s+(\d+)\)$")


def _parse_display_name(raw: str) -> tuple[str, str, str]:
    """'Apple Inc.  (AAPL)  (CIK 0000320193)' → (name, ticker, cik)"""
    m = _DISPLAY_NAME_RE.match(raw.strip())
    if m:
        return m.group(1).strip(), m.group(2), m.group(3).lstrip("0")
    return raw.strip(), "", ""


class USSecVerifier(RegistryVerifier):
    """
    US SEC EDGAR — public company registry (SEC-registered entities).
    No API key required. Covers ~10k US public companies.
    """

    registry_name = "SEC EDGAR"
    country_code = "US"

    async def search(self, company_name: str) -> list[RegistryResult]:
        year = datetime.now().year - 1
        async with httpx.AsyncClient(timeout=12.0, headers=_HEADERS) as client:
            try:
                resp = await client.get(
                    _EDGAR_SEARCH,
                    params={
                        "q": f'"{company_name}"',
                        "forms": "10-K",
                        "dateRange": "custom",
                        "startdt": f"{year}-01-01",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            except (httpx.HTTPError, ValueError) as e:
                return [RegistryResult(found=False, registry=self.registry_name, error=str(e))]

        hits = data.get("hits", {}).get("hits", [])
        seen_ciks: set[str] = set()
        results: list[RegistryResult] = []

        for hit in hits:
            src = hit.get("_source", {})
            display_names = src.get("display_names", [])
            if not display_names:
                continue

            name, ticker, cik = _parse_display_name(display_names[0])
            if not name or cik in seen_ciks:
                continue
            seen_ciks.add(cik)

            score = fuzz.token_sort_ratio(
                self._normalize_name(company_name),
                self._normalize_name(name),
            )
            if score < 60:
                continue

            state = (src.get("inc_states") or [""])[0]
            location = (src.get("biz_locations") or [""])[0]

            results.append(
                RegistryResult(
                    found=True,
                    registry=self.registry_name,
                    registration_number=cik,
                    legal_name=name,
                    status="active",
                    address=location,
                    raw={"cik": cik, "ticker": ticker, "state": state, "location": location},
                )
            )
            if len(results) >= 5:
                break

        return results

    async def get_by_id(self, registration_number: str) -> RegistryResult | None:
        cik = registration_number.zfill(10)
        async with httpx.AsyncClient(timeout=10.0, headers=_HEADERS) as client:
            try:
                r = await client.get(_EDGAR_COMPANY.format(cik=cik))
                r.raise_for_status()
                data = r.json()
            except (httpx.HTTPError, ValueError):
                return None

        return RegistryResult(
            found=True,
            registry=self.registry_name,
            registration_number=registration_number,
            legal_name=data.get("name", ""),
            status="active",
            address=f"{data.get('stateOfIncorporation', '')}, US",
            raw=data,
        )
