"""1.28 — honest c2pa_verified (known-issues §6.8, security.md S-26).

`verify_pi_camera_signature()` is a substring match on a client-supplied
`manifest_json` form field — it cannot prove anything about the uploaded
file. Before this fix, a forged manifest on a `.txt` upload got
`c2pa_verified: true`. These tests assert `device_upload_media` never sets
it True while `settings.c2pa_verification_enabled` is False (the default),
regardless of what the caller sends. Unit-level like `test_device_revoke.py`
— route function called directly with a stub session, no Postgres/Redis.
"""

import uuid
from datetime import datetime, timezone

import pytest

from app.api.routes import media as media_route
from app.core.config import settings
from app.models.block import Block
from app.models.device import Device


class _Result:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _StubSession:
    """Queues rows for execute() in call order; add()/flush() recorded/no-op."""

    def __init__(self, *rows) -> None:
        self.rows = list(rows)
        self.added: list = []

    async def execute(self, _stmt):
        return _Result(self.rows.pop(0) if self.rows else None)

    def add(self, obj) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        pass


class _FakeUpload:
    def __init__(self, content: bytes, filename: str, content_type: str) -> None:
        self._content = content
        self.filename = filename
        self.content_type = content_type

    async def read(self) -> bytes:
        return self._content


def _device() -> Device:
    return Device(
        id=uuid.uuid4(),
        business_id=uuid.uuid4(),
        label="Pi CAM",
        device_fingerprint=f"fp-{uuid.uuid4().hex}",
        device_public_key="-----BEGIN PUBLIC KEY-----",
        api_key=f"pk_live_{uuid.uuid4().hex}",
        is_active=True,
        registered_at=datetime.now(timezone.utc),
        revoked_at=None,
    )


FORGED_MANIFEST = (
    '{"claim_generator":"PiCam","signature_info":{"issuer":"device key"}}'
)


@pytest.mark.asyncio
async def test_forged_manifest_does_not_verify_while_flag_disabled(monkeypatch) -> None:
    assert settings.c2pa_verification_enabled is False  # the documented default

    monkeypatch.setattr(media_route.submit_bitcoin_timestamp, "delay", lambda *a, **k: None)
    monkeypatch.setattr(media_route, "_save_local", lambda content, filename: "local://x")

    dev = _device()
    db = _StubSession(None)  # no existing "Pi CAM Captures" block → one is created
    upload = _FakeUpload(b"not a real image", "evidence.txt", "text/plain")

    out = await media_route.device_upload_media(
        file=upload,
        manifest_json=FORGED_MANIFEST,
        captured_at=None,
        device=dev,
        db=db,
    )

    assert out["c2pa_verified"] is False
    assert out["c2pa_signer"] is None
    assert out["teta_pi_verified"] is False

    (block,) = [o for o in db.added if isinstance(o, Block)]
    assert block.title == "Pi CAM Captures"


@pytest.mark.asyncio
async def test_forged_manifest_verifies_once_flag_enabled(monkeypatch) -> None:
    """Sanity check that the gate — not something else — is what blocks it:
    flipping the flag on reproduces the pre-fix (forgeable) behaviour, which
    is exactly why it must stay False until real verification (task B)
    ships."""
    monkeypatch.setattr(settings, "c2pa_verification_enabled", True)
    monkeypatch.setattr(media_route.submit_bitcoin_timestamp, "delay", lambda *a, **k: None)
    monkeypatch.setattr(media_route, "_save_local", lambda content, filename: "local://x")

    dev = _device()
    db = _StubSession(None)
    upload = _FakeUpload(b"not a real image", "evidence.txt", "text/plain")

    out = await media_route.device_upload_media(
        file=upload,
        manifest_json=FORGED_MANIFEST,
        captured_at=None,
        device=dev,
        db=db,
    )

    assert out["c2pa_verified"] is True  # proves the gate, not the crypto, was fixed
