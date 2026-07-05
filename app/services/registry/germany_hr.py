import asyncio
import re
import time

import httpx
from bs4 import BeautifulSoup
from rapidfuzz import fuzz

from app.services.registry.base import RegistryResult, RegistryVerifier

_REG_NO = re.compile(r"(HRA|HRB|GnR|VR|PR)\s*\d+(\s+[A-Z]+)?")


class GermanyHandelsregisterVerifier(RegistryVerifier):
    """
    Germany — official common register portal (handelsregister.de).
    Free public access guaranteed by § 9 HGB; no API, so we drive the
    JSF search form directly (same flow as bundesAPI/handelsregister).
    Covers GmbH, UG, AG, e.V., OHG, KG — all German register entries.
    """

    registry_name = "Handelsregister"
    country_code = "DE"

    _BASE = "https://www.handelsregister.de"

    # The portal dislikes concurrent JSF sessions — serialize access,
    # cache results (10 min) and retry once on transient empty responses.
    _lock: asyncio.Lock = asyncio.Lock()
    _cache: dict[str, tuple[float, list]] = {}
    _CACHE_TTL = 600.0
    _HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.6",
    }

    @staticmethod
    def _collect_form_fields(form) -> dict:
        data: dict[str, str] = {}
        for i in form.find_all("input"):
            n = i.get("name")
            if n and i.get("type") not in ("submit", "button"):
                data[n] = i.get("value", "")
        for t in form.find_all("textarea"):
            if t.get("name"):
                data[t["name"]] = ""
        for s in form.find_all("select"):
            n = s.get("name")
            if not n:
                continue
            sel = s.find("option", selected=True) or s.find("option")
            data[n] = sel.get("value", "") if sel else ""
        return data

    async def search(self, company_name: str) -> list[RegistryResult]:
        key = company_name.strip().lower()
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < self._CACHE_TTL:
            return cached[1]

        async with self._lock:
            cached = self._cache.get(key)
            if cached and time.monotonic() - cached[0] < self._CACHE_TTL:
                return cached[1]
            results = await self._search_portal(company_name)
            if not results:
                await asyncio.sleep(1.5)
                results = await self._search_portal(company_name)
            self._cache[key] = (time.monotonic(), results)
            if len(self._cache) > 500:
                oldest = min(self._cache, key=lambda k: self._cache[k][0])
                del self._cache[oldest]
            return results

    async def _search_portal(self, company_name: str) -> list[RegistryResult]:
        try:
            async with httpx.AsyncClient(
                timeout=25.0, headers=self._HEADERS, follow_redirects=True
            ) as client:
                # 1. Homepage — session cookie + naviForm ViewState
                resp = await client.get(f"{self._BASE}/")
                soup = BeautifulSoup(resp.text, "html.parser")
                navi = soup.find("form", {"name": "naviForm"})
                if not navi:
                    return [RegistryResult(found=False, registry=self.registry_name, error="portal layout changed (naviForm)")]
                fields = {
                    i.get("name"): i.get("value", "")
                    for i in navi.find_all("input") if i.get("name")
                }
                fields["naviForm:erweiterteSucheLink"] = "naviForm:erweiterteSucheLink"

                # 2. Navigate to advanced search
                resp = await client.post(f"{self._BASE}{navi['action']}", data=fields)
                soup = BeautifulSoup(resp.text, "html.parser")
                form = soup.find("form", {"id": "form"})
                if not form:
                    return [RegistryResult(found=False, registry=self.registry_name, error="portal layout changed (search form)")]

                # 3. Submit search — option 1 = all keywords (option 2 requires
                #    extra filters since the 2025 portal update)
                data = self._collect_form_fields(form)
                data["form:schlagwoerter"] = company_name
                data["form:schlagwortOptionen"] = "1"
                data["form:btnSuche"] = "Suchen"
                resp = await client.post(f"{self._BASE}{form['action']}", data=data)
        except httpx.HTTPError as e:
            return [RegistryResult(found=False, registry=self.registry_name, error=str(e))]

        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        for row in soup.find_all("tr", attrs={"data-ri": True})[:5]:
            cells = [c.get_text(" ", strip=True) for c in row.find_all("td")]
            if len(cells) < 4:
                continue
            court_and_no = cells[1] if len(cells) > 1 else ""
            name = cells[2] if len(cells) > 2 else ""
            city = cells[3] if len(cells) > 3 else ""
            status = cells[4] if len(cells) > 4 else ""

            score = fuzz.token_sort_ratio(
                self._normalize_name(company_name), self._normalize_name(name)
            )
            # partial_ratio catches "WumWam" inside longer legal names
            partial = fuzz.partial_ratio(company_name.lower(), name.lower())
            if score < 45 and partial < 80:
                continue

            m = _REG_NO.search(court_and_no)
            reg_no = m.group(0).strip() if m else ""
            court = _REG_NO.sub("", court_and_no).strip()

            results.append(
                RegistryResult(
                    found=True,
                    registry=self.registry_name,
                    registration_number=reg_no,
                    legal_name=name,
                    status="active" if "aktuell" in status.lower() else (status or "unknown"),
                    address=city or None,
                    raw={"court": court},
                )
            )
        return results

    async def get_by_id(self, registration_number: str) -> RegistryResult | None:
        # Portal supports register-number search, but v1 resolves by name only
        return None
