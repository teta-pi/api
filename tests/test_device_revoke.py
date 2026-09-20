"""1.25 — Pi CAM device key revocation (known-issues 6.6b, security S-21).

Before this, `Device.api_key` (`X-Device-Api-Key`) was valid forever: no
owner, device or admin path could kill it. These are unit-level like
`test_blocks_is_public.py` — route functions are called directly with a stub
session (no Postgres/Redis), so they run on the 5.7 CI runner. The live
end-to-end (pair → upload 200 → revoke → upload 401) is done on prod after
deploy; the standing regression net is `infra/scripts/security/probe.py`
(`check_s21_device_revoked`).

Covered:
  * a revoked (or inactive / keyless) device is rejected by `_get_device`,
    the dependency behind `POST /media/device-upload` → 401;
  * the owner can revoke their own device, another owner gets 404 (no
    existence oracle), and the call is idempotent (one event, one timestamp);
  * the device can revoke only itself (`POST /devices/self-revoke`);
  * the admin path writes an `admin_audit_log` row every time;
  * `GET /devices` reports revoked rows with `revoked_at` and `paired=False`.
"""

import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.api.routes import admin as admin_route
from app.api.routes import media as media_route
from app.models.audit_log import AdminAuditLog
from app.models.device import Device
from app.models.verification_event import VerificationEvent


class _Result:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row

    def one_or_none(self):
        return self._row

    def scalars(self):
        return self

    def all(self):
        return self._row or []


class _StubSession:
    """Just enough AsyncSession surface: every execute() returns the queued
    rows in order; add() is recorded; commit/flush are no-ops."""

    def __init__(self, *rows) -> None:
        self.rows = list(rows)
        self.added: list = []

    async def execute(self, _stmt):
        return _Result(self.rows.pop(0) if self.rows else None)

    def add(self, obj) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        pass


class _User:
    def __init__(self, role: str = "user") -> None:
        self.id = uuid.uuid4()
        self.email = f"{self.id.hex[:8]}@example.test"
        self.role = role


def _device(owner_business: uuid.UUID | None = None, **kw) -> Device:
    d = Device(
        id=uuid.uuid4(),
        business_id=owner_business or uuid.uuid4(),
        label="Pi CAM",
        device_fingerprint=f"fp-{uuid.uuid4().hex}",
        device_public_key="-----BEGIN PUBLIC KEY-----",
        api_key=f"pk_live_{uuid.uuid4().hex}",
        is_active=True,
        registered_at=datetime.now(timezone.utc),
        revoked_at=None,
    )
    for k, v in kw.items():
        setattr(d, k, v)
    return d


def _events(db: _StubSession) -> list[VerificationEvent]:
    return [o for o in db.added if isinstance(o, VerificationEvent)]


# ── _get_device: the gate in front of /media/device-upload ────────────────────


@pytest.mark.asyncio
async def test_live_device_key_is_accepted() -> None:
    dev = _device()
    got = await media_route._get_device(x_device_api_key=dev.api_key, db=_StubSession(dev))
    assert got is dev


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    [
        {"revoked_at": datetime.now(timezone.utc)},
        {"is_active": False},
        {"revoked_at": datetime.now(timezone.utc), "api_key": None, "is_active": False},
    ],
)
async def test_revoked_device_key_is_401(state: dict) -> None:
    dev = _device(**state)
    with pytest.raises(HTTPException) as exc:
        await media_route._get_device(x_device_api_key="pk_live_whatever", db=_StubSession(dev))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_unknown_device_key_is_401() -> None:
    with pytest.raises(HTTPException) as exc:
        await media_route._get_device(x_device_api_key="pk_live_nope", db=_StubSession(None))
    assert exc.value.status_code == 401


# ── _revoke_device: the shared primitive ──────────────────────────────────────


def test_revoke_erases_key_and_writes_one_event() -> None:
    dev = _device()
    db = _StubSession()
    assert media_route._revoke_device(db, dev, source="owner") is True
    assert dev.api_key is None
    assert dev.is_active is False
    assert dev.revoked_at is not None
    first = dev.revoked_at
    (ev,) = _events(db)
    assert ev.event_type == "device_revoked"
    assert ev.level == 0
    assert ev.source == "owner"
    assert ev.entity_id == dev.business_id
    assert len(ev.payload_hash) == 32

    # Idempotent: second call is a no-op — same timestamp, no second event.
    assert media_route._revoke_device(db, dev, source="admin") is False
    assert dev.revoked_at == first
    assert len(_events(db)) == 1


# ── Owner path: DELETE /devices/{id} ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_owner_can_revoke_own_device() -> None:
    owner = _User()
    dev = _device()
    db = _StubSession((dev, owner.id))
    out = await media_route.revoke_device(device_id=dev.id, db=db, current_user=owner)
    assert out["device_id"] == dev.id
    assert out["revoked_at"] == dev.revoked_at is not None
    assert dev.api_key is None
    assert [e.source for e in _events(db)] == ["owner"]


@pytest.mark.asyncio
async def test_foreign_device_is_404_not_403() -> None:
    dev = _device()
    db = _StubSession((dev, uuid.uuid4()))  # owned by somebody else
    with pytest.raises(HTTPException) as exc:
        await media_route.revoke_device(device_id=dev.id, db=db, current_user=_User())
    assert exc.value.status_code == 404
    assert dev.api_key is not None  # untouched
    assert _events(db) == []


@pytest.mark.asyncio
async def test_missing_device_is_404() -> None:
    with pytest.raises(HTTPException) as exc:
        await media_route.revoke_device(
            device_id=uuid.uuid4(), db=_StubSession(None), current_user=_User()
        )
    assert exc.value.status_code == 404


# ── Device path: POST /devices/self-revoke ────────────────────────────────────


@pytest.mark.asyncio
async def test_device_self_revoke_kills_only_itself() -> None:
    # The dependency resolves the caller from its own key, so the only device
    # that can ever be touched is the one that authenticated.
    dev = _device()
    db = _StubSession(dev)
    resolved = await media_route._get_device(x_device_api_key=dev.api_key, db=db)
    out = await media_route.self_revoke_device(device=resolved, db=db)
    assert out["device_id"] == dev.id
    assert dev.api_key is None and dev.revoked_at is not None
    assert [e.source for e in _events(db)] == ["device"]

    # …and the same key is now dead for uploads.
    with pytest.raises(HTTPException) as exc:
        await media_route._get_device(x_device_api_key="pk_live_dead", db=_StubSession(dev))
    assert exc.value.status_code == 401


# ── Admin path: DELETE /admin/devices/{id} ────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_revoke_writes_audit_log() -> None:
    admin = _User(role="admin")
    dev = _device()
    db = _StubSession(dev)
    out = await admin_route.admin_revoke_device(device_id=dev.id, admin=admin, db=db)
    assert out["already_revoked"] is False
    assert dev.api_key is None
    audits = [o for o in db.added if isinstance(o, AdminAuditLog)]
    assert len(audits) == 1
    assert audits[0].action == "devices.revoke"
    assert audits[0].actor_id == admin.id
    assert audits[0].target_id == str(dev.id)
    assert audits[0].detail["already_revoked"] is False
    assert [e.source for e in _events(db)] == ["admin"]

    # Second admin call: no new event, but STILL an audit row (every admin
    # action is logged, CLAUDE.md rule).
    db2 = _StubSession(dev)
    out2 = await admin_route.admin_revoke_device(device_id=dev.id, admin=admin, db=db2)
    assert out2["already_revoked"] is True
    assert len([o for o in db2.added if isinstance(o, AdminAuditLog)]) == 1
    assert _events(db2) == []


@pytest.mark.asyncio
async def test_admin_revoke_missing_device_404() -> None:
    with pytest.raises(HTTPException) as exc:
        await admin_route.admin_revoke_device(
            device_id=uuid.uuid4(), admin=_User(role="admin"), db=_StubSession(None)
        )
    assert exc.value.status_code == 404


# ── GET /devices reflects revocation ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_devices_reports_revoked_at() -> None:
    owner = _User()
    biz_id = uuid.uuid4()

    class _Biz:
        id = biz_id

    live = _device(biz_id)
    dead = _device(biz_id, api_key=None, is_active=False, revoked_at=datetime.now(timezone.utc))
    db = _StubSession(_Biz(), [live, dead])
    out = await media_route.list_devices(db=db, current_user=owner)
    assert out["paired"] is True
    by_id = {d["id"]: d for d in out["devices"]}
    assert by_id[live.id]["revoked_at"] is None
    assert by_id[dead.id]["revoked_at"] == dead.revoked_at

    # Only revoked devices left → not paired.
    db = _StubSession(_Biz(), [dead])
    out = await media_route.list_devices(db=db, current_user=owner)
    assert out["paired"] is False
    assert len(out["devices"]) == 1
