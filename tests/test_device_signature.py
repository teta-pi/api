"""1.29 — real device content-signature verification (14.12/1.29 contract,
docs/api.md). `c2pa_verified` stays gated off (1.28, known-issues §6.8); this
is the independent, actually-cryptographic signal: did the device's own
ECDSA P-256 key sign this exact file's content?

Contract: the device signs the **hex-encoded SHA-256 hash of the file's raw
bytes** (not the file, not the manifest) and sends the DER signature
base64-encoded as `content_signature` + `signature_alg`. Unit-level like
`test_device_revoke.py`/`test_c2pa_gating.py` — no Postgres/Redis.
"""

import base64
import hashlib
import uuid
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException

from app.api.routes import media as media_route
from app.models.device import Device
from app.services import device_signature as device_signature_service

ALG = "ecdsa-with-SHA256"


def _keypair() -> tuple[ec.EllipticCurvePrivateKey, str]:
    priv = ec.generate_private_key(ec.SECP256R1())
    pub_pem = priv.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return priv, pub_pem


def _sign(priv: ec.EllipticCurvePrivateKey, file_bytes: bytes) -> str:
    content_hash_hex = hashlib.sha256(file_bytes).hexdigest()
    der_sig = priv.sign(content_hash_hex.encode(), ec.ECDSA(hashes.SHA256()))
    return base64.b64encode(der_sig).decode()


# ── is_valid_device_public_key ─────────────────────────────────────────────


def test_real_p256_spki_pem_is_valid() -> None:
    _, pub_pem = _keypair()
    assert device_signature_service.is_valid_device_public_key(pub_pem) is True


def test_legacy_hex_string_is_not_a_valid_key() -> None:
    # Prod has literal "testpubkey" and bare 64-char hex strings from before
    # this check existed.
    assert device_signature_service.is_valid_device_public_key("testpubkey") is False
    assert device_signature_service.is_valid_device_public_key("a" * 64) is False


def test_wrong_curve_is_rejected() -> None:
    priv = ec.generate_private_key(ec.SECP384R1())
    pub_pem = priv.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    assert device_signature_service.is_valid_device_public_key(pub_pem) is False


# ── verify_content_signature ───────────────────────────────────────────────


def test_valid_signature_verifies() -> None:
    priv, pub_pem = _keypair()
    content = b"a real jpeg's worth of bytes"
    sig = _sign(priv, content)
    assert device_signature_service.verify_content_signature(content, sig, ALG, pub_pem) is True


def test_forged_signature_does_not_verify() -> None:
    _, pub_pem = _keypair()
    content = b"a real jpeg's worth of bytes"
    assert (
        device_signature_service.verify_content_signature(
            content, base64.b64encode(b"not a real signature").decode(), ALG, pub_pem
        )
        is False
    )


def test_signature_from_a_different_device_key_does_not_verify() -> None:
    signer_priv, _ = _keypair()
    _, registered_pub_pem = _keypair()  # a different device's registered key
    content = b"a real jpeg's worth of bytes"
    sig = _sign(signer_priv, content)
    assert (
        device_signature_service.verify_content_signature(content, sig, ALG, registered_pub_pem)
        is False
    )


def test_signature_over_a_different_file_does_not_verify() -> None:
    priv, pub_pem = _keypair()
    sig = _sign(priv, b"the file that was actually signed")
    assert (
        device_signature_service.verify_content_signature(
            b"a swapped-in different file", sig, ALG, pub_pem
        )
        is False
    )


@pytest.mark.parametrize(
    "content_signature,signature_alg",
    [(None, ALG), ("", ALG), ("c2lnbmF0dXJl", None), ("c2lnbmF0dXJl", "")],
)
def test_missing_fields_are_unverified_not_an_error(content_signature, signature_alg) -> None:
    _, pub_pem = _keypair()
    assert (
        device_signature_service.verify_content_signature(
            b"content", content_signature, signature_alg, pub_pem
        )
        is False
    )


def test_unsupported_alg_is_unverified() -> None:
    priv, pub_pem = _keypair()
    content = b"content"
    sig = _sign(priv, content)
    assert (
        device_signature_service.verify_content_signature(content, sig, "hmac-sha256", pub_pem)
        is False
    )


def test_legacy_invalid_pem_registered_key_is_unverified_not_an_error() -> None:
    # A pre-14.12 device re-pairs with no valid key at all (e.g. "testpubkey")
    # — verification must fail honestly, never 500.
    assert (
        device_signature_service.verify_content_signature(
            b"content", "c2lnbmF0dXJl", ALG, "testpubkey"
        )
        is False
    )


# ── POST /devices/register rejects a non-P256-SPKI key (400) ──────────────


class _StubRedis:
    def __init__(self, payload: str | None) -> None:
        self._payload = payload
        self.deleted: list[str] = []

    async def get(self, _key: str):
        return self._payload

    async def delete(self, key: str) -> None:
        self.deleted.append(key)


class _StubSession:
    def __init__(self, *rows) -> None:
        self.rows = list(rows)
        self.added: list = []

    async def execute(self, _stmt):
        class _Result:
            def __init__(self, row):
                self._row = row

            def scalar_one_or_none(self):
                return self._row

        return _Result(self.rows.pop(0) if self.rows else None)

    def add(self, obj) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        pass


@pytest.mark.asyncio
async def test_register_device_rejects_invalid_public_key() -> None:
    from app.schemas.media import DeviceRegisterRequest

    payload = DeviceRegisterRequest(
        registration_token="tok",
        device_fingerprint=f"fp-{uuid.uuid4().hex}",
        device_public_key="testpubkey",
        label="Pi CAM",
    )
    with pytest.raises(HTTPException) as exc:
        await media_route.register_device(
            payload=payload, db=_StubSession(), redis=_StubRedis(None)
        )
    assert exc.value.status_code == 400
    assert "P-256" in exc.value.detail or "SPKI" in exc.value.detail


@pytest.mark.asyncio
async def test_register_device_accepts_real_p256_key() -> None:
    import json

    from app.schemas.media import DeviceRegisterRequest

    _, pub_pem = _keypair()
    business_id = uuid.uuid4()
    token_data = json.dumps({
        "business_id": str(business_id),
        "entity_name": "Test Biz",
        "entity_slug": "test-biz",
        "user_id": str(uuid.uuid4()),
    })
    payload = DeviceRegisterRequest(
        registration_token="tok",
        device_fingerprint=f"fp-{uuid.uuid4().hex}",
        device_public_key=pub_pem,
        label="Pi CAM",
    )
    out = await media_route.register_device(
        payload=payload, db=_StubSession(None), redis=_StubRedis(token_data)
    )
    assert out["entity_name"] == "Test Biz"


# ── device_upload_media wiring (forged vs. real signature) ────────────────


class _FakeUpload:
    def __init__(self, content: bytes) -> None:
        self._content = content
        self.filename = "capture.jpg"
        self.content_type = "image/jpeg"

    async def read(self) -> bytes:
        return self._content


def _device(public_key_pem: str) -> Device:
    return Device(
        id=uuid.uuid4(),
        business_id=uuid.uuid4(),
        label="Pi CAM",
        device_fingerprint=f"fp-{uuid.uuid4().hex}",
        device_public_key=public_key_pem,
        api_key=f"pk_live_{uuid.uuid4().hex}",
        is_active=True,
        registered_at=datetime.now(timezone.utc),
        revoked_at=None,
    )


@pytest.mark.asyncio
async def test_device_upload_with_valid_signature_sets_device_signature_verified(monkeypatch) -> None:
    monkeypatch.setattr(media_route.submit_bitcoin_timestamp, "delay", lambda *a, **k: None)
    monkeypatch.setattr(media_route, "_save_local", lambda content, filename: "local://x")

    priv, pub_pem = _keypair()
    content = b"\xff\xd8 a real jpeg's worth of bytes"
    sig = _sign(priv, content)
    dev = _device(pub_pem)

    out = await media_route.device_upload_media(
        file=_FakeUpload(content),
        manifest_json=None,
        captured_at=None,
        content_signature=sig,
        signature_alg=ALG,
        device=dev,
        db=_StubSession(None),
    )
    assert out["device_signature_verified"] is True


@pytest.mark.asyncio
async def test_device_upload_with_forged_signature_is_unverified(monkeypatch) -> None:
    monkeypatch.setattr(media_route.submit_bitcoin_timestamp, "delay", lambda *a, **k: None)
    monkeypatch.setattr(media_route, "_save_local", lambda content, filename: "local://x")

    _, pub_pem = _keypair()
    content = b"\xff\xd8 a real jpeg's worth of bytes"
    dev = _device(pub_pem)

    out = await media_route.device_upload_media(
        file=_FakeUpload(content),
        manifest_json=None,
        captured_at=None,
        content_signature=base64.b64encode(b"forged").decode(),
        signature_alg=ALG,
        device=dev,
        db=_StubSession(None),
    )
    assert out["device_signature_verified"] is False
