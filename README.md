# TETA+PI API

FastAPI backend for **TETA+PI** — Trust Infrastructure for Digital Entities.
Live at [`api.tetapi.dev`](https://api.tetapi.dev), docs UI at `/docs`.

People, companies, brands, domains, APIs, AI models, MCP servers, and agents
register as verified entities here. Verification is a set of independent,
owner-triggered methods — not a single gate:

- **Registry** — cross-checked against official registries (Handelsregister, GLEIF, EU VAT, DE-ÖKO, …)
- **Email control** — 6-digit code to an address on the entity's own domain
- **Domain ownership** — DNS TXT record or `.well-known` file token
- Each method writes its own append-only `verification_events` row

On top of that: **C2PA**-signed content blocks and **Bitcoin OpenTimestamps**
anchoring give a permanent, first-verified-at proof chain. Ranking for agent
search (**TWIRA** = `α·T + β·I + γ·P`) is earned through verification history,
not purchased.

## Stack
FastAPI · async SQLAlchemy (asyncpg) · PostgreSQL 16 + pgvector · Alembic ·
Redis · Celery (OTS lifecycle, endpoint probes, TWIRA recompute) · Python.

## Docs
Canonical docs — routes, database schema, registry verifiers, architecture —
live in [`teta-pi/infra`](https://github.com/teta-pi/infra): see
`docs/api.md`, `docs/database.md`, `docs/registries.md`.

## License
MIT © 2026 TETA+PI · tetapi.dev
