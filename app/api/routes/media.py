import hashlib
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.media import Media
from app.models.device import Device
from app.models.user import User
from app.schemas.media import DeviceRegisterRequest, DeviceRegisterResponse, MediaUploadResponse, MediaVerifyResponse
from app.services import c2pa as c2pa_service

router = APIRouter(prefix="/media", tags=["media"])
devices_router = APIRouter(prefix="/devices", tags=["devices"])


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

    # Extract and verify C2PA manifest (optional — non-C2PA media is accepted,
    # stored with c2pa_verified=False and still submitted to Bitcoin OTS)
    mime_type = file.content_type or "application/octet-stream"
    manifest = c2pa_service.extract_c2pa_manifest(content, mime_type)
    c2pa_verified = False
    c2pa_signer = None

    if manifest:
        c2pa_verified, c2pa_signer = c2pa_service.verify_pi_camera_signature(manifest)
        if c2pa_verified:
            manifest = c2pa_service.add_teta_pi_countersignature(manifest)

    # Store in S3/MinIO (stub — returns a local path for Sprint 1)
    storage_url = f"s3://tetapi-media/{uuid.uuid4()}/{file.filename}"

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

    # Submit hash to OpenTimestamps async
    from app.workers.tasks.bitcoin import submit_bitcoin_timestamp
    submit_bitcoin_timestamp.delay(str(media.id), content.hex())

    return {
        "media_id": media.id,
        "c2pa_verified": c2pa_verified,
        "c2pa_signer": c2pa_signer,
        "bitcoin_status": "pending",
        "estimated_confirmation": "~60 minutes",
    }


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
        "bitcoin_confirmed_at": None,  # TODO: store confirmation timestamp
        "ots_proof_url": f"https://teta-pi.io/proofs/{media.id}.ots" if media.bitcoin_confirmed else None,
    }


@router.delete("/{media_id}", status_code=204)
async def delete_media(
    media_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    result = await db.execute(select(Media).where(Media.id == media_id))
    media = result.scalar_one_or_none()
    if not media:
        raise HTTPException(status_code=404, detail="Media not found")
    await db.delete(media)


@devices_router.post("/register", response_model=DeviceRegisterResponse)
async def register_device(
    payload: DeviceRegisterRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    api_key = f"pk_live_{secrets.token_urlsafe(32)}"
    device = Device(
        business_id=payload.business_id,
        label=payload.label,
        device_fingerprint=payload.device_fingerprint,
        device_public_key=payload.device_public_key,
        api_key=api_key,
    )
    db.add(device)
    await db.flush()
    return {
        "device_id": device.id,
        "api_key": api_key,
        "registered_at": device.registered_at,
    }
