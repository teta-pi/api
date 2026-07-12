# API

FastAPI. Base: `https://api.tetapi.dev/api/v1`. Routers registered in
`api/app/main.py`; source in `api/app/api/routes/`. Docs UI at `/docs`.
Auth via `Authorization: Bearer <JWT|pk_live_…>`; deps in `api/app/api/deps.py`
(`get_current_user`, `require_admin`).

## Auth (`routes/auth.py`)
| Method | Path | Notes |
|---|---|---|
| POST | `/auth/token` | password sign-in → JWT |
| POST | `/auth/magic-link` | legacy, superseded by email-code |
| POST | `/auth/email-code` | send 6-digit code (Redis, 15 min, 60s cooldown) |
| POST | `/auth/verify-code` | verify code → JWT, creates user if new |
| POST | `/auth/set-password` | auth'd; enables password sign-in |
| POST | `/auth/change-email` + `/auth/confirm-email-change` | code to new address, bound to user in Redis |
| POST | `/auth/logout-all` | bump token_version, return fresh JWT |
| POST | `/auth/delete-account` | GDPR self-erasure; admins can't |
| POST | `/auth/personal-api-key` | generate/rotate `pk_live_…` |
| POST | `/auth/avatar` | upload PNG/JPEG/WebP ≤2MB |
| GET | `/auth/me` | account summary |

## Entities & content
- `routes/businesses.py` — CRUD (owner-checked). `POST /businesses` creates any
  name immediately, free, unverified (L0) — **no registry call**
  (`registry_status="unverified"`, `is_published=is_public=True` for every
  entity_type). Verification is now a choice of independent, owner-triggered
  methods (docs/verification-rework.md §2), each writing its own append-only
  `verification_events` row on success:
  - `POST /{id}/verify/registry` — official registry match (existing check,
    now explicit instead of automatic at create/rename).
  - `POST /{id}/verify/email/start` + `/confirm` — Business Email Control:
    6-digit code to an address on the brand's own domain (Redis-backed, same
    pattern as `/auth/email-code`, namespaced `biz_email_code:*`; rejects
    free-mailbox domains). Writes `email_verified`.
  - `POST /{id}/verify/domain/start` + `/check` — Domain Ownership: DNS TXT
    (via DNS-over-HTTPS, no resolver dependency) or a `.well-known` file
    token, same mechanism as the WordPress plugin. Writes `domain_verified`.
  - Document upload: **not implemented** — UI-only "Coming soon" is 3.4's job.
  - `POST` / `DELETE /{id}/legal-entity` — link/unlink a brand to a verified
    legal entity (`businesses.legal_entity_id`); requires the caller to own
    both entities and the legal entity to already be `registry_status=verified`.
    Publicly disclosed via `legal_entity` in the public/preview payloads, not hidden.
  - `POST /{id}/publish` no longer gates on registry verification (entities are
    already published at creation).
  - `verification_level` (`none|registry|email|domain|partial|full`) is derived
    on read from `registry_status` + `verification_events`, not stored.
  - `GET /businesses/{id}/preview` (agent JSON), `GET /businesses/{id}/proof`,
    `GET /businesses/by-slug/{slug}/public` (published+public only, public
    blocks only — powers `/e/[slug]`; includes the `legal_entity` disclosure).
- `routes/blocks.py` — block CRUD, owner-checked via parent business.
- `routes/media.py` — `/media/upload` (JWT), `/media/device-upload` (api_key),
  local storage under `UPLOAD_DIR`, served at `/media/local/{id}/{name}`.

## Search & intent
- `routes/search.py` — `/search` keyword+level search over published entities.
- `routes/registry_search.py` — `/registry/search?q=&country=` → official registry
  lookup (see `docs/registries.md`).
- `routes/intent.py` — `POST /resolve-intent`: TWIRA-ranked (falls back to keyword
  when no embeddings), returns per-component breakdown + first_verified_at.
- `routes/endpoint_verification.py` — `/verify-endpoint`.

## Claims (waitlist) — `routes/claims.py`
`POST /claim` (201 + position, 409 idempotent, rate-limit 5/min/IP),
`GET /claim/stats` (total / pay_ready / pct).

## Admin (back office) — `routes/admin.py`, all `require_admin` + audited
`GET /admin/stats`, `GET /admin/analytics` (GoatCounter traffic bridge, see
`docs/analytics.md`), `GET /admin/product-metrics` (growth trends, entity_type
mix, claim→verified funnel — see `docs/analytics.md`), `/admin/users`
(search/filter/paginate), `/admin/users/{id}`, `/admin/users/{id}/export`
(GDPR), `POST /admin/users/{id}/anonymize`, `GET /admin/users/{id}/flags`
(disposable email / dup registry_id / country mismatch), `POST
/admin/entities/{id}/validate` (re-check registry → append-only event),
`/admin/claims`, `/admin/entities`, `/admin/audit-log`.

## Services (`api/app/services/`)
`ai.py` (OpenAI embeddings + categories), `bitcoin.py` (OpenTimestamps, not
OP_RETURN), `c2pa.py`, `email.py` (Resend: verification codes, claim confirmation),
`registry/` (verifiers + router), `verification/` (`email_control.py` — business
email control, reuses `email.py`'s Resend sender; `domain_ownership.py` — DNS
TXT via DNS-over-HTTPS + `.well-known` file check; both used by
`routes/businesses.py`'s `/verify/*` endpoints, no changes to `routes/auth.py`
needed).

## Conventions
- Rate limiters and the Handelsregister lock are **in-memory** → correct only under
  `uvicorn --workers 1`. Move to Redis before scaling.
- Errors: raise `HTTPException`; owner checks compare `owner_id == current_user.id`.
