"""1.22 — `POST /businesses/{id}/blocks` must honour `BlockCreate.is_public`.

Regression for the bug found in passing during 15.3 (docs/known-issues.md,
S-8 entry): `add_block` built the `Block` row without reading
`payload.is_public`, so every new block was public no matter what the caller
sent. This lives here, not in infra's `scripts/security/probe.py`, because
the probe is read-only by design (security.md §6.2) and asserting this needs
a write (creating a block).

Unit-level: `add_block` is called directly with a stub session and the
owner check + embedding patched out, so no Postgres is needed.
"""

import uuid
from unittest.mock import AsyncMock

import pytest

from app.api.routes import blocks as blocks_route
from app.models.block import Block
from app.schemas.block import BlockCreate


class _StubSession:
    """Just enough AsyncSession surface for add_block."""

    def __init__(self) -> None:
        self.added: list[Block] = []

    def add(self, obj: Block) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        pass

    async def refresh(self, obj: Block, attrs: list[str] | None = None) -> None:
        pass


@pytest.fixture
def stub_env(monkeypatch: pytest.MonkeyPatch) -> _StubSession:
    monkeypatch.setattr(blocks_route, "_get_owned_business", AsyncMock(return_value=object()))
    monkeypatch.setattr(blocks_route, "generate_embedding", AsyncMock(return_value=None))
    return _StubSession()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (BlockCreate(title="private", is_public=False), False),
        (BlockCreate(title="public", is_public=True), True),
        # Callers that don't send the field (the web app today) keep the old
        # behaviour: public.
        (BlockCreate(title="default"), True),
    ],
)
async def test_add_block_honours_is_public(
    stub_env: _StubSession, payload: BlockCreate, expected: bool
) -> None:
    block = await blocks_route.add_block(
        business_id=uuid.uuid4(),
        payload=payload,
        db=stub_env,  # type: ignore[arg-type]
        current_user=object(),  # type: ignore[arg-type]
    )
    assert block.is_public is expected
    assert stub_env.added == [block]


def test_block_create_schema_defaults_public() -> None:
    assert BlockCreate.model_fields["is_public"].default is True
    assert BlockCreate.model_validate({"title": "x"}).is_public is True
    assert BlockCreate.model_validate({"title": "x", "is_public": False}).is_public is False
