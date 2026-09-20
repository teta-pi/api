import hashlib
import json
import logging
import os
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.exc import IntegrityError

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Local filesystem storage fallback (used when S3/MinIO is not configured)
_UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "/opt/tetapi/uploads"))


def _save_local(content: bytes, filename: str) -> str:
    """Save file to local disk, return storage URL."""
    try:
        _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        file_id = uuid.uuid4().hex
        safe_name = Path(filename).name or "file"
        dest = _UPLOAD_DIR / file_id / safe_name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        return f"/media/local/{file_id}/{safe_name}"
    except Exception as e:
        logger.warning("Local storage failed: %s", e)
        return f"local://tetapi-media/{uuid.uuid4()}/{filename}"


from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.redis import get_redis
from app.models.block import Block
from app.models.business import Business
from app.models.device import Device
from app.models.media import Media
from app.models.user import User
from app.models.verification_event import VerificationEvent
from app.schemas.media import (
    DeviceListResponse,
    DeviceMediaUploadResponse,
    DeviceRegisterRequest,
    DeviceRegisterResponse,
    DeviceRevokeResponse,
    MediaUploadResponse,
    MediaVerifyResponse,
    QRTokenResponse,
)
from app.services import c2pa as c2pa_service
from app.workers.tasks.bitcoin import submit_bitcoin_timestamp

router = APIRouter(prefix="/media", tags=["media"])
devices_router = APIRouter(prefix="/devices", tags=["devices"])

_TOKEN_TTL = 900  # 15 minutes


# ── Device media upload (api_key auth) ───────────────────────────────────────


async def _get_device(
    x_device_api_key: str = Header(..., alias="X-Device-Api-Key"),
    db: AsyncSession = Depends(get_db),
) -> Device:
    result = await db.execute(select(Device).where(Device.api_key == x_device_api_key))
    device = result.scalar_one_or_none()
    # Revocation (1.25) is checked in Python, not only in the WHERE clause, so
    # a revoked/inactive row is rejected even if a future query regresses —
    # and so the unit test can assert it without Postgres.
    if not device or not device.is_active or device.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid device key")
    return device


def _event_payload_hash(payload: dict) -> bytes:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).digest()


def _revoke_device(db: AsyncSession, device: Device, source: str) -> bool:
    """Kill a device's upload credential (1.25 / known-issues 6.6b).

    Keeps the row (media provenance + fingerprint uniqueness for a later
    re-pair) but erases the secret: `api_key=NULL`, `is_active=False`,
    `revoked_at=now`. Appends a `device_revoked` verification_event (level 0,
    source 'owner' | 'device' | 'admin') as the append-only trail. Idempotent:
    an already-revoked device is left untouched and no second event is
    written. Returns True if this call did the revocation.
    """
    if device.revoked_at is not None:
        return False
    now = datetime.now(timezone.utc)
    device.api_key = None
    device.is_active = False
    device.revoked_at = now
    db.add(
        VerificationEvent(
            entity_id=device.business_id,
            event_type="device_revoked",
            level=0,
            source=source,
            payload_hash=_event_payload_hash({
                "device_id": str(device.id),
                "device_fingerprint": device.device_fingerprint,
                "source": source,
                "revoked_at": now.isoformat(),
            }),
        )
    )
    return True


@router.post("/device-upload", response_model=DeviceMediaUploadResponse)
async def device_upload_media(
    file: UploadFile = File(...),
    manifest_json: str | None = Form(None),
    captured_at: str | None = Form(None),
    device: Device = Depends(_get_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Upload media from a registered Pi CAM device using its api_key."""
    content = await file.read()
    original_hash = hashlib.sha256(content).hexdigest()
    mime_type = file.content_type or "application/octet-stream"

    manifest = c2pa_service.extract_c2pa_manifest(content, mime_type)
    if not manifest and manifest_json:
        manifest = c2pa_service.extract_c2pa_manifest_from_json(manifest_json)

    c2pa_verified = False
    c2pa_signer = None
    teta_pi_verified = False

    if manifest:
        c2pa_verified, c2pa_signer = c2pa_service.verify_pi_camera_signature(manifest)
        if c2pa_verified:
            manifest = c2pa_service.add_teta_pi_countersignature(manifest)
            teta_pi_verified = True

    storage_url = _save_local(content, file.filename or "upload")

    captured_dt = None
    if captured_at:
        try:
            captured_dt = datetime.fromisoformat(captured_at)
        except ValueError:
            pass

    # Find-or-create the default "Pi CAM Captures" block for this device's entity.
    # This makes uploads visible in /businesses/{id}/proof and /e/[slug].
    cam_block = (
        await db.execute(
            select(Block).where(
                Block.business_id == device.business_id,
                Block.title == "Pi CAM Captures",
            )
        )
    ).scalar_one_or_none()
    if not cam_block:
        cam_block = Block(
            business_id=device.business_id,
            title="Pi CAM Captures",
            is_public=True,
            order=999,
        )
        db.add(cam_block)
        await db.flush()

    media = Media(
        block_id=cam_block.id,
        type=mime_type.split("/")[0],
        storage_url=storage_url,
        original_hash=original_hash,
        c2pa_manifest=manifest,
        c2pa_verified=c2pa_verified,
        c2pa_signer=c2pa_signer,
        captured_at=captured_dt,
    )
    db.add(media)
    await db.flush()
    submit_bitcoin_timestamp.delay(str(media.id), original_hash)

    return {
        "media_id": media.id,
        "c2pa_verified": c2pa_verified,
        "c2pa_signer": c2pa_signer,
        "bitcoin_status": "pending",
        "teta_pi_verified": teta_pi_verified,
    }


# ── Standard user media upload (JWT auth) ────────────────────────────────────


@router.post("/upload", response_model=MediaUploadResponse)
async def upload_media(
    file: UploadFile = File(...),
    block_id: uuid.UUID = Form(...),
    type: str = Form(...),
    captured_at: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    content = await file.read()
    original_hash = hashlib.sha256(content).hexdigest()
    mime_type = file.content_type or "application/octet-stream"

    manifest = c2pa_service.extract_c2pa_manifest(content, mime_type)
    c2pa_verified = False
    c2pa_signer = None

    if manifest:
        c2pa_verified, c2pa_signer = c2pa_service.verify_pi_camera_signature(manifest)
        if c2pa_verified:
            manifest = c2pa_service.add_teta_pi_countersignature(manifest)

    storage_url = _save_local(content, file.filename or "upload")

    captured_dt = None
    if captured_at:
        try:
            captured_dt = datetime.fromisoformat(captured_at)
        except ValueError:
            pass

    media = Media(
        block_id=block_id,
        type=type,
        storage_url=storage_url,
        original_hash=original_hash,
        c2pa_manifest=manifest,
        c2pa_verified=c2pa_verified,
        c2pa_signer=c2pa_signer,
        captured_at=captured_dt,
    )
    db.add(media)
    await db.flush()
    submit_bitcoin_timestamp.delay(str(media.id), original_hash)

    return {
        "media_id": media.id,
        "storage_url": storage_url,
        "c2pa_verified": c2pa_verified,
        "c2pa_signer": c2pa_signer,
        "bitcoin_status": "pending",
        "estimated_confirmation": "~60 minutes",
    }


@router.get("/local/{file_id}/{filename}")
async def serve_local_media(file_id: str, filename: str) -> FileResponse:
    """Serve locally stored media files."""
    base = _UPLOAD_DIR.resolve()
    path = (base / file_id / filename).resolve()
    if not path.is_relative_to(base):
        raise HTTPException(status_code=404, detail="File not found")
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path)


@router.get("/{media_id}/verify", response_model=MediaVerifyResponse)
async def verify_media(
    media_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(Media).where(Media.id == media_id))
    media = result.scalar_one_or_none()
    if not media:
        raise HTTPException(status_code=404, detail="Media not found")

    return {
        "media_id": media.id,
        "c2pa_verified": media.c2pa_verified,
        "c2pa_verified_at": media.uploaded_at if media.c2pa_verified else None,
        "bitcoin_status": "confirmed" if media.bitcoin_confirmed else "pending",
        "bitcoin_block": media.bitcoin_block,
        "bitcoin_confirmed_at": None,
        "ots_proof_url": f"https://tetapi.dev/proofs/{media.id}.ots" if media.bitcoin_confirmed else None,
    }


@router.delete("/{media_id}", status_code=204)
async def delete_media(
    media_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    from app.models.block import Block

    result = await db.execute(
        select(Media)
        .join(Block, Media.block_id == Block.id)
        .join(Business, Block.business_id == Business.id)
        .where(Media.id == media_id)
    )
    media = result.scalar_one_or_none()
    if not media:
        raise HTTPException(status_code=404, detail="Media not found")

    block_result = await db.execute(
        select(Business.owner_id)
        .join(Block, Business.id == Block.business_id)
        .where(Block.id == media.block_id)
    )
    owner_id = block_result.scalar_one_or_none()
    if owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your media")

    await db.delete(media)
    await db.flush()


# ── Device registration (QR token flow) ──────────────────────────────────────


@devices_router.get("", response_model=DeviceListResponse)
async def list_devices(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Paired-status for the caller's business — what `/profile` and the
    `/claim` onboarding step poll to show "Camera linked" instead of always
    "Connect Camera", regardless of what actually happened in the Pi CAM app.
    """
    result = await db.execute(
        select(Business).where(Business.owner_id == current_user.id).limit(1)
    )
    business = result.scalar_one_or_none()
    if not business:
        return {"paired": False, "devices": []}

    # Revoked devices are returned too (with `revoked_at` set) so the UI can
    # show state honestly; only live ones count towards `paired`.
    devices = (
        await db.execute(
            select(Device)
            .where(Device.business_id == business.id)
            .order_by(Device.registered_at.desc())
        )
    ).scalars().all()

    return {
        "paired": any(d.is_active and d.revoked_at is None for d in devices),
        "devices": [
            {
                "id": d.id,
                "label": d.label,
                "registered_at": d.registered_at,
                "revoked_at": d.revoked_at,
            }
            for d in devices
        ],
    }


@devices_router.delete("/{device_id}", response_model=DeviceRevokeResponse)
async def revoke_device(
    device_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Owner-side revocation (1.25): kills the device's X-Device-Api-Key for good.
    Same owner check as `_get_owned_business` — a device that isn't yours is
    indistinguishable from one that doesn't exist (404, no existence oracle).
    Idempotent: revoking twice returns the original `revoked_at`.
    """
    result = await db.execute(
        select(Device, Business.owner_id)
        .join(Business, Device.business_id == Business.id)
        .where(Device.id == device_id)
    )
    row = result.one_or_none()
    if not row or row[1] != current_user.id:
        raise HTTPException(status_code=404, detail="Device not found")
    device = row[0]
    _revoke_device(db, device, source="owner")
    await db.flush()
    return {"device_id": device.id, "revoked_at": device.revoked_at}


@devices_router.post("/self-revoke", response_model=DeviceRevokeResponse)
async def self_revoke_device(
    device: Device = Depends(_get_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Device-side revocation (1.25) — what the Pi CAM app's "Unlink" calls
    before wiping its local keys. The phone has no owner JWT, only its own
    X-Device-Api-Key, and that key identifies exactly one device, so by
    construction it can only ever revoke itself (no id in the path or body
    to get wrong). Kept as its own route rather than a second auth mode on
    `DELETE /devices/{id}` so the owner route's auth model stays
    single-scheme and the device route stays symmetric with `/register`.
    """
    _revoke_device(db, device, source="device")
    await db.flush()
    return {"device_id": device.id, "revoked_at": device.revoked_at}


@devices_router.post("/generate-token", response_model=QRTokenResponse)
async def generate_registration_token(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    redis=Depends(get_redis),
) -> dict:
    """
    Called from tetapi.dev by an authenticated user.
    Returns a short-lived token that Pi CAM scans as a QR code.
    """
    result = await db.execute(
        select(Business).where(Business.owner_id == current_user.id).limit(1)
    )
    business = result.scalar_one_or_none()
    if not business:
        raise HTTPException(status_code=400, detail="No business found — create one first")

    token = secrets.token_urlsafe(32)
    payload = json.dumps({
        "business_id": str(business.id),
        "entity_name": business.name,
        "entity_slug": business.slug,
        "user_id": str(current_user.id),
    })
    await redis.setex(f"cam_reg:{token}", _TOKEN_TTL, payload)

    return {
        "token": token,
        "entity_id": str(business.id),
        "entity_name": business.name,
        "expires_in": _TOKEN_TTL,
    }


@devices_router.post("/register", response_model=DeviceRegisterResponse)
async def register_device(
    payload: DeviceRegisterRequest,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
) -> dict:
    """
    Called by Pi CAM after scanning the QR code.
    No JWT required — authenticated via short-lived registration token.
    """
    raw = await redis.get(f"cam_reg:{payload.registration_token}")
    if not raw:
        raise HTTPException(status_code=400, detail="Invalid or expired registration token")

    token_data = json.loads(raw)
    business_id = uuid.UUID(token_data["business_id"])
    entity_name = token_data["entity_name"]
    entity_slug = token_data.get("entity_slug")

    # Idempotent: same fingerprint → update public key and reactivate
    existing = await db.execute(
        select(Device).where(Device.device_fingerprint == payload.device_fingerprint)
    )
    device = existing.scalar_one_or_none()

    if device:
        device.device_public_key = payload.device_public_key
        device.is_active = True
        # A revoked device re-pairing (fresh QR from the owner's session) gets
        # a brand-new key — the old one was erased on revocation (1.25).
        if device.revoked_at is not None or not device.api_key:
            device.api_key = f"pk_live_{secrets.token_urlsafe(32)}"
            device.revoked_at = None
        await db.flush()
    else:
        api_key = f"pk_live_{secrets.token_urlsafe(32)}"
        device = Device(
            business_id=business_id,
            label=payload.label,
            device_fingerprint=payload.device_fingerprint,
            device_public_key=payload.device_public_key,
            api_key=api_key,
        )
        db.add(device)
        try:
            await db.flush()
        except IntegrityError:
            # Race condition: another request inserted same fingerprint first
            await db.rollback()
            result2 = await db.execute(
                select(Device).where(Device.device_fingerprint == payload.device_fingerprint)
            )
            device = result2.scalar_one()

    # One-time token — consume it
    await redis.delete(f"cam_reg:{payload.registration_token}")

    # Fetch slug if not in token (backward compat: old tokens lack entity_slug)
    if not entity_slug:
        biz = (await db.execute(select(Business).where(Business.id == business_id))).scalar_one_or_none()
        entity_slug = biz.slug if biz else None

    return {
        "device_id": device.id,
        "api_key": device.api_key,
        "entity_id": str(business_id),
        "entity_name": entity_name,
        "entity_slug": entity_slug,
        "registered_at": device.registered_at,
    }
