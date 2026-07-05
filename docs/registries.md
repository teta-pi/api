# Registries

Registry verification lives in `api/app/services/registry/`. Each verifier
subclasses `RegistryVerifier` (`base.py`) and returns `RegistryResult`s. The
`router.py` fans out queries and merges/ranks results.

## Routing strategy (`router.py::verify_business_in_registry`)
1. If a country is given and has a dedicated verifier → use it. If **no country**
   (the claim-flow search sends none) → fan out to **all** free country verifiers
   in parallel.
2. GLEIF (global LEI) always.
3. OpenCorporates (200+ jurisdictions) always, as fallback.
4. SEC EDGAR for US/unknown.
5. US state registries (NY, CO) for US/unknown.
6. Premium providers (NorthData, Opendatabot) when their key is set.

Results merge (dedup by registration number / legal name) and **sort by fuzzy name
similarity to the query** (token_sort + partial_ratio), global fallbacks lose ties.

## Verifiers
| Country | Registry | File | Source | Status |
|---|---|---|---|---|
| DE | Handelsregister | `germany_hr.py` | official portal JSF scrape | ✅ works; serialized (lock+10min cache+retry) — portal rejects concurrent sessions |
| GB | Companies House | `companies_house.py` | official API (key optional) | ✅ |
| US | SEC EDGAR | `us_sec.py` | SEC public API | ✅ public companies only |
| US | NY DOS + Colorado SOS | `us_states.py` | official Socrata open data | ✅ LLCs/corps SEC misses |
| FR | SIRENE | `france_re.py` | recherche-entreprises.api.gouv.fr | ✅ gov API, no key |
| CZ | ARES | `czech_ares.py` | Ministry of Finance REST | ✅ no key |
| FI | PRH/YTJ | `finland_prh.py` | avoindata.prh.fi v3 | ✅ no key |
| NO | Brønnøysundregistrene | `norway_brreg.py` | official open API | ✅ |
| — | GLEIF | `gleif.py` | global LEI | ✅ |
| — | OpenCorporates | `opencorporates.py` | 200+ jurisdictions | ✅ fallback |
| UA | EDR | `ukraine_edr.py` | usr.minjust.gov.ua | ❌ upstream API dead; needs Opendatabot |
| DE/EU | NorthData | `premium.py` | commercial API | 🔑 inactive until `NORTHDATA_API_KEY` |
| UA | Opendatabot | `premium.py` | commercial API | 🔑 inactive until `OPENDATABOT_API_KEY` |

## Why the German scrape (decisions)
`api.offeneregister.de` is dead and the old `/rp_web/search` URL 404s. The official
`handelsregister.de` portal has no API but guarantees free public access (§9 HGB).
We drive its JSF form: homepage → `naviForm` (advanced search) → submit `form` with
**all** hidden fields + `schlagwortOptionen=1` (option 2 now requires extra filters).
Validated live: finds "WumWam BARVINOK … GmbH" (Berlin, HRB 54940). Needs
`beautifulsoup4`.

## Adding a verifier
1. New file subclassing `RegistryVerifier`; implement `search()` (+ `get_by_id()`).
2. Fuzzy-match names with `rapidfuzz` (threshold ~45, or partial_ratio ≥80 for short
   brand names inside long legal names).
3. Register in `router.py` (country map or fan-out) and update the ranking if needed.
4. Add it to landing `registries.html`, `llms.txt`, and both `agent.json` files.
5. Test against the **live** API before shipping (local Python may be too old to
   import the app; hit the registry endpoint directly with curl).

## Legality note
Only official/open government APIs and licensed commercial APIs are used. The German
portal access is the statutory free public register. UA and deep DE/EU need paid
licences (NorthData / Opendatabot) — activated by env key, never scraped illegally.
