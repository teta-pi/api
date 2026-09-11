# Database

PostgreSQL 16 + pgvector + pg_trgm. Async access via SQLAlchemy 2 (asyncpg).
Migrations via Alembic in `api/alembic/versions/`. On the server the DB runs in
Docker container `tetapi-postgres` (image `pgvector/pgvector:pg16`).

## Migrations (apply in order; CI runs `alembic upgrade head`)
| Rev | What |
|---|---|
| 001 | initial schema (users, businesses, blocks, media, devices) |
| 002 | entity_type, privacy flags, agent endpoints |
| 003 | device media nullable block |
| 004 | FK indexes, registry status, trigram search indexes |
| 005 | `claims` table + `claim_stats` view (waitlist) |
| 006 | entity extension + Temporal Moat: EntityType→12, segment, blocks.c2pa_manifest/ots_proof/embedding vector(1536)+HNSW, `verification_events` (append-only trigger), `endpoint_probes` |
| 007 | `users.role`, `admin_audit_log` (append-only trigger), seed admins |
| 008 | `users.full_name` → Text (holds Fernet ciphertext) |
| 009 | `users.token_version` |
| 010 | `users.avatar_url` |
| 011 | `businesses.legal_entity_id` (nullable self-FK, brand→legal entity link); asserts append-only trigger from 006 is still attached |
| 012 | `claims.ops_status` + `ops_status_updated_at` (backoffice outreach tracking) |
| 013 | `businesses.claim_status` + `pre_verified_source` (bulk pre-verification import, roadmap 1.11) |

## Core tables
- **users** — id, email (unique, plaintext for login/index), `full_name`
  (EncryptedString), hashed_password?, auth_provider, `role`, is_active, is_agent,
  api_key?, `token_version`, `avatar_url?`, timestamps.
- **businesses** (the entity table; name is historical) — id, owner_id→users,
  `entity_type` (12-value enum), `segment` (builder|operator|consumer), name, slug,
  description, country, registry_id, registry_status, registry_data(jsonb),
  verification_level, agent_endpoint(+verified), is_public, is_published,
  `t_score`, `p_score`, `legal_entity_id?` (self-FK — brand→verified legal
  entity link, e.g. "Google" brand → "Alphabet Inc." legal entity; publicly
  disclosed on profile), `claim_status`, `pre_verified_source?`, timestamps.
  `claim_status` (013, roadmap 1.11): `self_registered` (default, normal
  `POST /businesses`) | `pre_verified_unclaimed` (bulk-imported by
  `routes/admin.py::bulk_preverify_entities`, owned by the system account
  `bulk-import@tetapi.dev`) | `claimed` (real owner proved domain ownership
  via `routes/businesses.py`'s `/claim/domain/start`+`/check`) | `opted_out`
  (owner used the one-click `/opt-out` link — unpublished, kept for audit,
  not deleted). Independent of `verification_level`/`registry_status` —
  pre-verified is a provenance flag, not a verification-chain result;
  surfaced on the public page (`by-slug/{slug}/public`'s `claim_status` +
  `pre_verified_unclaimed` bool) so it's never mistaken for a self-claim.
  `pre_verified_source` (jsonb, nullable) — `{github_org?, domain?,
  npm_package?, pulled_from, imported_at, opt_out_token}`, set at import time.
  Since the verification rework (1.3, `docs/verification-rework.md`):
  creation no longer calls the registry — `registry_status` defaults to
  `unverified` and every entity is `is_published=is_public=true` immediately.
  `registry_status` values: `unverified | verified | not_found |
  multiple_matches | failed`. `verification_level` is computed on read
  (`routes/businesses.py::_compute_verification_level`), not written at create
  time — it reads `registry_status` plus `verification_events` for
  `email_verified`/`domain_verified` rows, so no new columns were needed for
  the two new methods.
- **blocks** — id, business_id→businesses, title, description, order,
  verification_status, is_public, `c2pa_manifest`(jsonb), `ots_proof`(bytea),
  `embedding` vector(1536) + HNSW index.
- **media** — block_id, type, c2pa_verified/signer, bitcoin_confirmed/block, …
- **claims** — email(unique), entity_type, ready_to_pay, source(jsonb),
  `position` (identity), created_at. View `claim_stats` = total/pay_ready/pct.
- **verification_events** (Temporal Moat, append-only) — entity_id, event_type,
  level, source, payload_hash(sha256), ots_proof?, ots_status
  (pending→anchored→confirmed), btc_block?, created_at. Trigger blocks DELETE and
  any UPDATE except the OTS lifecycle columns. `event_type` is a plain
  String(50), no DB-level enum/check constraint — allowed values are
  documented on the model: `registered | level_up | block_signed |
  endpoint_verified | reverified | email_verified | domain_verified |
  document_verified | pre_verified_imported | claimed | opted_out` (the
  `*_verified` trio added 011 for the verification rework, see
  `docs/verification-rework.md`; `document_verified` is type-only for now,
  no backend/upload endpoint until file-upload risk is handled; the last
  three added 013/roadmap 1.11 — audit trail only, they don't feed
  `_compute_verification_level`).
- **endpoint_probes** — entity_id, ok(bool), at. Feeds TWIRA uptime.
- **admin_audit_log** (append-only) — actor_id/email, action, target_type/id,
  detail(jsonb), created_at. Trigger blocks all UPDATE/DELETE.

## Conventions
- UUID PKs (`gen_random_uuid()`), timezone-aware timestamps, `func.now()` defaults.
- Privacy: `is_public=false` → excluded from search/public URL, still verifiable by
  authorized direct query. `is_published` gates the public page.
- New migration: add columns with defaults + backfill; never rewrite append-only
  tables. Bump the revision, set `down_revision` to the previous head.

## Access on server
```
docker exec tetapi-postgres psql -U tetapi -d tetapi -c "SELECT …;"
```
