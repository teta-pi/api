# TETA+PI — api.tetapi.dev

This repo is one component of TETA+PI (Trust Infrastructure for Digital
Entities), split from the platform monorepo 2026-07-13 (see
[decisions.md](https://github.com/teta-pi/infra/blob/main/docs/decisions.md)
in `teta-pi/infra` for the split plan).

**Canonical docs, roadmap, changelog, and coding rules live in
[`teta-pi/infra`](https://github.com/teta-pi/infra)** — read
`docs/api.md, docs/database.md, docs/registries.md` there before touching this repo, plus `docs/overview.md` and
`docs/architecture.md` for cross-cutting context. This file is a thin
pointer only; do not duplicate rules here.

## Quick facts
- Deploys to `api.tetapi.dev` on push to `main` (see `.github/workflows/deploy.yml`).
- Commits: end message with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Never commit secrets. `DEPLOY_SSH_KEY` is a repo secret; nothing else belongs in this repo's git history.
