"""1.30 — block allowance enforcement (free: 10 included + one-time packs).

Unit-level like test_blocks_is_public.py / test_device_revoke.py: route
functions are called directly with a stub session (no Postgres), so this
runs on the CI runner with no DB. The append-only trigger on
block_entitlements (migration 017) can only be proven against a real
Postgres — verified on prod after deploy via tetapi-psql, same as every
other append-only table (docs/database.md).
"""

import uuid

import pytest
from fastapi import HTTPException

from app.api.routes import admin as admin_route
from app.api.routes import blocks as blocks_route
from app.core.config import settings
from app.models.block_entitlement import BlockEntitlement


class _Scalar:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value


class _StubSession:
    """Queued results per execute() call, in call order."""

    def __init__(self, *rows) -> None:
        self.rows = list(rows)
        self.added: list = []

    async def execute(self, _stmt):
        return _Scalar(self.rows.pop(0) if self.rows else None)

    def add(self, obj) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        pass

    async def refresh(self, obj, attrs=None) -> None:
        pass

    async def commit(self) -> None:
        pass


class _User:
    def __init__(self, role: str = "admin") -> None:
        self.id = uuid.uuid4()
        self.email = f"{self.id.hex[:8]}@example.test"
        self.role = role


# ── block_allowance / block_usage ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_allowance_is_included_plus_grants() -> None:
    business_id = uuid.uuid4()
    db = _StubSession(100)  # sum(granted_blocks)
    allowance = await blocks_route.block_allowance(db, business_id)
    assert allowance == settings.included_block_allowance + 100


@pytest.mark.asyncio
async def test_allowance_with_no_grants_is_just_included() -> None:
    db = _StubSession(0)
    allowance = await blocks_route.block_allowance(db, uuid.uuid4())
    assert allowance == settings.included_block_allowance


# ── add_block enforcement ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tenth_block_ok_eleventh_is_402(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.schemas.block import BlockCreate

    monkeypatch.setattr(blocks_route, "_get_owned_business", _async_return(object()))
    monkeypatch.setattr(blocks_route, "generate_embedding", _async_return(None))

    business_id = uuid.uuid4()

    # 10th block: used=9, allowance=10 (0 grants) -> allowed.
    db_ok = _StubSession(9, 0)
    block = await blocks_route.add_block(
        business_id=business_id,
        payload=BlockCreate(title="block 10"),
        db=db_ok,  # type: ignore[arg-type]
        current_user=object(),  # type: ignore[arg-type]
    )
    assert block.business_id == business_id

    # 11th block: used=10, allowance=10 -> 402 with a machine-readable body.
    db_over = _StubSession(10, 0)
    with pytest.raises(HTTPException) as exc:
        await blocks_route.add_block(
            business_id=business_id,
            payload=BlockCreate(title="block 11"),
            db=db_over,  # type: ignore[arg-type]
            current_user=object(),  # type: ignore[arg-type]
        )
    assert exc.value.status_code == 402
    body = exc.value.detail
    assert body["used"] == 10
    assert body["allowance"] == 10
    assert body["next_pack"]["blocks"] == 10


@pytest.mark.asyncio
async def test_pack_100_grant_allows_111th_block_then_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.schemas.block import BlockCreate

    monkeypatch.setattr(blocks_route, "_get_owned_business", _async_return(object()))
    monkeypatch.setattr(blocks_route, "generate_embedding", _async_return(None))

    business_id = uuid.uuid4()

    # 110th block: used=109, allowance=10+100=110 -> allowed.
    db_ok = _StubSession(109, 100)
    block = await blocks_route.add_block(
        business_id=business_id,
        payload=BlockCreate(title="block 110"),
        db=db_ok,  # type: ignore[arg-type]
        current_user=object(),  # type: ignore[arg-type]
    )
    assert block is not None

    # 111th block: used=110, allowance=110 -> 402.
    db_over = _StubSession(110, 100)
    with pytest.raises(HTTPException) as exc:
        await blocks_route.add_block(
            business_id=business_id,
            payload=BlockCreate(title="block 111"),
            db=db_over,  # type: ignore[arg-type]
            current_user=object(),  # type: ignore[arg-type]
        )
    assert exc.value.status_code == 402
    assert exc.value.detail["allowance"] == 110


def _async_return(value):
    async def _inner(*_args, **_kwargs):
        return value

    return _inner


# ── PATCH/DELETE/reorder are unaffected by quota ────────────────────────────


@pytest.mark.asyncio
async def test_update_block_ignores_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.models.block import Block
    from app.schemas.block import BlockUpdate

    block = Block(id=uuid.uuid4(), business_id=uuid.uuid4(), title="old", media=[])
    monkeypatch.setattr(blocks_route, "_get_owned_business", _async_return(object()))
    monkeypatch.setattr(blocks_route, "generate_embedding", _async_return(None))
    # Only one execute() call expected (the block lookup) — no allowance/usage
    # queries at all, proving quota isn't consulted on update.
    db = _StubSession(block)
    out = await blocks_route.update_block(
        block_id=block.id,
        payload=BlockUpdate(title="new"),
        db=db,  # type: ignore[arg-type]
        current_user=object(),  # type: ignore[arg-type]
    )
    assert out.title == "new"


@pytest.mark.asyncio
async def test_delete_block_ignores_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.models.block import Block

    block = Block(id=uuid.uuid4(), business_id=uuid.uuid4(), title="x")
    monkeypatch.setattr(blocks_route, "_get_owned_business", _async_return(object()))

    class _DelSession(_StubSession):
        async def delete(self, obj) -> None:
            self.deleted = obj

    db = _DelSession(block)
    await blocks_route.delete_block(
        block_id=block.id, db=db, current_user=object()  # type: ignore[arg-type]
    )
    assert db.deleted is block


@pytest.mark.asyncio
async def test_reorder_ignores_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.models.block import Block

    block = Block(id=uuid.uuid4(), business_id=uuid.uuid4(), title="x", order=5)
    monkeypatch.setattr(blocks_route, "_get_owned_business", _async_return(object()))
    db = _StubSession(block)
    from app.schemas.block import BlockReorder

    await blocks_route.reorder_blocks(
        payload=BlockReorder(block_ids=[block.id]),
        db=db,  # type: ignore[arg-type]
        current_user=object(),  # type: ignore[arg-type]
    )
    assert block.order == 0


# ── Public payloads never carry quota fields ────────────────────────────────


def test_business_out_quota_fields_default_none() -> None:
    from app.schemas.business import BusinessOut

    assert BusinessOut.model_fields["blocks_used"].default is None
    assert BusinessOut.model_fields["blocks_allowance"].default is None


# ── Admin grant path ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_grant_blocks_writes_entitlement_and_audit_row() -> None:
    from app.models.business import Business
    from app.api.routes.admin import GrantBlocksRequest

    business = Business(id=uuid.uuid4())
    admin = _User()
    # execute() call order: entity lookup, then block_usage, then block_allowance.
    db = _StubSession(business, 0, 100)
    out = await admin_route.grant_blocks(
        entity_id=business.id,
        payload=GrantBlocksRequest(granted_blocks=100, source="pack_100", note="support comp"),
        admin=admin,  # type: ignore[arg-type]
        db=db,  # type: ignore[arg-type]
    )
    (grant,) = [o for o in db.added if isinstance(o, BlockEntitlement)]
    assert grant.granted_blocks == 100
    assert grant.source == "pack_100"
    assert grant.granted_by == admin.email
    assert out["blocks_allowance"] == settings.included_block_allowance + 100


@pytest.mark.asyncio
async def test_grant_blocks_rejects_unknown_source() -> None:
    from app.api.routes.admin import GrantBlocksRequest

    with pytest.raises(HTTPException) as exc:
        await admin_route.grant_blocks(
            entity_id=uuid.uuid4(),
            payload=GrantBlocksRequest(granted_blocks=10, source="bogus"),
            admin=_User(),  # type: ignore[arg-type]
            db=_StubSession(),  # type: ignore[arg-type]
        )
    assert exc.value.status_code == 422
