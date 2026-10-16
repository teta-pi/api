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

Session tokens: HS256 over the symmetric `SECRET_KEY`, minted and verified in
`app/core/auth.py` with claims `sub` / `exp` / `ver` (`ver` is the
`logout-all` kill switch). The accepted algorithm is pinned in code and
`exp`+`sub` are required on decode, so a header-supplied `alg` — `none`
included — is never honoured. Signed with **PyJWT** since 15.10; it was
`python-jose` until PYSEC-2026-4298 (and its `ecdsa` dep,
PYSEC-2026-1325) turned out to have no fix version. Neither advisory was
reachable in our usage — both need an asymmetric key or ECDSA signing, and we
have neither — and PyJWT emits byte-identical tokens for this payload, so the
swap kept every live session valid. `tests/test_auth_token.py` locks the shape
in. PyJWT is a runtime dependency: it is named in `deploy.yml`'s explicit pip
install, which is not `pip install .`.

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
  - `POST /{id}/claim/domain/start` + `/claim/domain/check` (roadmap 1.11) —
    claim path for a `claim_status=pre_verified_unclaimed` entity (bulk-imported,
    see `/admin/entities/bulk-preverify` below): no owner check (the current
    owner is the system import account), gated on `claim_status` instead;
    reuses the same `domain_ownership` service as the normal
    `/verify/domain/*` flow. On success transfers `owner_id` to the caller,
    sets `claim_status=claimed`, writes `domain_verified` + `claimed`
    verification_events. `POST /businesses` itself 409s (with the existing
    entity's id/slug) instead of creating a duplicate when the slug already
    belongs to a `pre_verified_unclaimed` row.
  - `POST /{id}/opt-out?token=…` — unauthenticated one-click removal for a
    pre-verified profile (GTM Phase 2 guardrail: no form, no login). Token is
    the `opt_out_token` minted at import time; on match, unpublishes
    (`is_published=is_public=false`, `claim_status=opted_out`) rather than
    deleting.
  - `POST /{id}/publish` no longer gates on registry verification (entities are
    already published at creation).
  - `verification_level` (`none|registry|email|domain|partial|full`) is derived
    on read from `registry_status` + `verification_events`, not stored.
  - `GET /businesses/{id}/preview` (agent JSON), `GET /businesses/{id}/proof`,
    `GET /businesses/by-slug/{slug}/public` (published+public only, public
    blocks only — powers `/e/[slug]`; includes the `legal_entity` disclosure).
  - `GET /businesses/{id}` adds `blocks_used`/`blocks_allowance` (1.30/1.32),
    **owner view only** — `None` for every other caller. `/preview`, `/proof`
    and `by-slug/public` use separate response shapes that never carry these
    fields at all, so there's no leak path to close there.
- `routes/blocks.py` — block CRUD, owner-checked via parent business.
  **Block quota (1.30/1.32)**: `POST /businesses/{id}/blocks` enforces an
  allowance — `settings.included_block_allowance` (10) + sum of the
  business's `block_entitlements` grants (docs/database.md). At/over the
  allowance it's `402` with a machine-readable body
  (`{message, used, allowance, next_pack}`), never a bare 403. PATCH/DELETE
  /reorder are not gated — only creation counts against the quota. Helpers
  `block_usage`/`block_allowance` in this module are reused by
  `GET /businesses/{id}` (owner view) and the admin grant endpoint. No Stripe
  here — packs are granted via `POST /admin/entities/{id}/grant-blocks`
  until session 1.31 wires real payment.
- `routes/media.py` — `/media/upload` (JWT), `/media/device-upload` (api_key),
  local storage under `UPLOAD_DIR`, served at `/media/local/{id}/{name}`.
  `c2pa_verified` is gated behind `settings.c2pa_verification_enabled` (default
  `False`, `app/core/config.py`) — until real C2PA manifest verification
  exists, `verify_pi_camera_signature()` is only a substring match on a
  client-supplied `manifest_json` field and proves nothing, so every upload
  path and every public read (`by-slug/public`, `/preview`, `/proof`,
  `/media/{id}/verify`, `verification_level`) forces the flag `False`
  regardless of the stored column (security.md S-27, known-issues §6.8).
  Separately, `device_signature_verified` (1.29) is a real, independently
  checked signal — the device's own ECDSA P-256 key signed the uploaded
  file's content (`content_signature`/`signature_alg` form fields,
  `app/services/device_signature.py`) — and is not gated or blended with
  `c2pa_verified`; see the infra `docs/api.md` contract section for the full
  write-up. `POST /devices/register` now rejects a non-P-256-SPKI
  `device_public_key` with `400`.

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

## Universal Tag — `routes/tag.py` (12.5b, `docs/universal-tag.md`, `docs/security.md` B5)
Anonymous, unauthenticated, public — not under `/api/v1` (registered without a
prefix, same as `badge.router`).
- `POST /v1/tag-ping` — beacon fired once per page load by `tag.js` (Part A).
  Body `{entity_id, page_url, page_title?, referrer?}`. Always `204`, even for
  an unknown `entity_id` — never discloses whether an entity exists. In-memory
  IP rate limiter (same pattern as `routes/badge.py`; Redis migration for
  multi-worker tracked as S-10). On a known published+public entity, appends
  `page_url` (+ title) to a Redis sorted set `tag_pages:{business_id}`, capped
  at 200 members via `ZREMRANGEBYRANK`, and increments
  `tag_impressions:{business_id}` — no new Postgres table, no per-hit DB row
  (12.5b storage-shape decision: Redis, not a DB table; same (a)/(b) choice as
  2.4).
- `GET /wk/{entity_id}/agent.json` — schema.org JSON-LD, same shape `tag.js`
  injects client-side. Public+published entities only, `404` otherwise.
- `GET /wk/{entity_id}/agent-card.json` — richer agent-facing payload (trust
  level, proof/profile URLs, public blocks), mirrors
  `/businesses/{id}/preview`.
- `GET /wk/{entity_id}/llms.txt` — plain-text summary, `llms.txt` convention.
- All three `wk` routes: `Cache-Control: public, max-age=300` (spec: "cache
  ~5 min"). Reverse-proxied from the entity's own domain via
  `verify.tetapi.dev` once 12.5c ships the nginx vhost + DNS-record checker.

## Admin (back office) — `routes/admin.py`, all `require_admin` + audited
`GET /admin/stats`, `GET /admin/analytics` (GoatCounter traffic bridge, see
`docs/analytics.md`), `GET /admin/product-metrics` (growth trends, entity_type
mix, claim→verified funnel — see `docs/analytics.md`), `/admin/users`
(search/filter/paginate), `/admin/users/{id}`, `/admin/users/{id}/export`
(GDPR), `POST /admin/users/{id}/anonymize`, `GET /admin/users/{id}/flags`
(disposable email / dup registry_id / country mismatch), `POST
/admin/entities/{id}/validate` (re-check registry → append-only event),
`/admin/claims`, `/admin/entities`, `/admin/audit-log`.

`POST /admin/entities/{id}/grant-blocks` (1.30/1.32) — body
`{granted_blocks, source, note?}`, `source` one of `included | pack_10 |
pack_100 | pack_1000 | admin`. Writes one append-only `block_entitlements`
row plus the mandatory `admin_audit_log` entry (action
`entities.grant_blocks`); no payment call here — manual/support comp path
until Stripe (session 1.31) wires the real packs. Returns the entity's
updated `blocks_used`/`blocks_allowance`.

`POST /admin/entities/bulk-preverify` (roadmap 1.11, GTM Phase 2 blocker) —
body `{items: [{name, entity_type?, country?, description?, domain?,
github_org?, npm_package?}]}`. Each item needs at least one public anchor
(`domain`/`github_org`/`npm_package`) — never creates an entity from a bare
name. Creates `claim_status=pre_verified_unclaimed` rows owned by the system
account `bulk-import@tetapi.dev` (created lazily), reuses
`routes/businesses.py::_slugify` and skips (doesn't duplicate) an existing
slug. Writes a `pre_verified_imported` verification_event per row plus one
`admin_audit_log` entry for the whole batch. Returns per-item
`profile_url`/`opt_out_url`/`badge_url` (real links, not the placeholders
`scripts/gtm/outreach_queue.py` in `teta-pi/infra` has been building until
now) so the outreach queue's `approve` command can stop refusing them.
Base domains come from `settings.app_url` (`https://app.tetapi.dev` —
profile + opt-out page) and `settings.api_url` (`https://api.tetapi.dev` —
`/badge/{slug}`), not literals (1.23; the original 1.11 pointed all three at
the bare landing domain, which 404s). The same two settings back every other
outbound link the API mints (`tag.py` well-known files, `intent.py`/
`intent_graph/resolver.py` `proof_url`, `auth.py` magic link). Note the
opt-out *page* `app.tetapi.dev/e/{slug}/opt-out` is a frontend route that
does not exist yet (boot 3 dependency) — the backend it must call is
`POST /businesses/{business_id}/opt-out?token=…`, resolving the slug via
`GET /businesses/by-slug/{slug}/public`.

## Services (`api/app/services/`)
`ai.py` (OpenAI embeddings + categories), `bitcoin.py` (OpenTimestamps, not
OP_RETURN), `c2pa.py`, `email.py` (Resend: verification codes, claim confirmation),
`registry/` (verifiers + router), `verification/` (`email_control.py` — business
email control, reuses `email.py`'s Resend sender; `domain_ownership.py` — DNS
TXT via DNS-over-HTTPS + `.well-known` file check; both used by
`routes/businesses.py`'s `/verify/*` endpoints, no changes to `routes/auth.py`
needed).

## Conventions
- The Handelsregister lock is still **in-memory** → correct only under
  `uvicorn --workers 1`.
- Errors: raise `HTTPException`; owner checks compare `owner_id == current_user.id`.

## Rate limiting (S-10, `app/core/ratelimit.py`)
One shared Redis-backed helper (fixed-window INCR+EXPIRE, atomic via Lua)
replaced the five per-process `dict` limiters that used to undercount by a
factor of `worker_count`. Limits/windows stay in `app/core/config.py`, values
unchanged by this migration.

Failure mode is chosen per endpoint group and passed as `fail_open`:
- `fail_open=True` — badge (`routes/badge.py`), tag-ping (`routes/tag.py`),
  claim (`routes/claims.py`), verify-endpoint (`routes/endpoint_verification.py`).
  These are public/cosmetic; a Redis blip must never turn into a user-facing
  outage, so it serves unlimited instead.
- `fail_open=False` — the email-code cooldown + attempts counters
  (`routes/auth.py`, `services/verification/email_control.py`). These exist
  specifically to stop brute-forcing a verification code; going unlimited on
  a Redis outage would open exactly the window the limiter is there to close,
  so it raises `503` instead.

Either way a Redis outage is never an unhandled `500` — `ratelimit.increment`
catches `RedisError` and only re-raises as a deliberate `503` for the
fail-closed groups.
