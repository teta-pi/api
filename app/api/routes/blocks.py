import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.business import Business
from app.models.block import Block
from app.models.user import User
from app.schemas.block import BlockCreate, BlockOut, BlockReorder, BlockUpdate
from app.services.ai import block_embedding_text, generate_embedding

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/businesses/{business_id}/blocks", tags=["blocks"])
blocks_router = APIRouter(prefix="/blocks", tags=["blocks"])

# Optional bearer: lets anonymous/agent readers through while still identifying
# the owner. auto_error=False → no Authorization header yields None, not a 403.
_optional_security = HTTPBearer(auto_error=False)


async def _get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_optional_security),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Resolve the caller if a valid token is present, else None.

    Reuses get_current_user's full logic (API keys, token version) so anonymous
    and invalid-token requests fall through to the public view instead of 401.
    """
    if credentials is None:
        return None
    try:
        return await get_current_user(credentials, db)
    except HTTPException:
        return None


async def _get_owned_business(
    business_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
) -> Business:
    result = await db.execute(select(Business).where(Business.id == business_id))
    business = result.scalar_one_or_none()
    if not business:
        raise HTTPException(status_code=404, detail="Business not found")
    if business.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your business")
    return business


@router.post("", response_model=BlockOut, status_code=201)
async def add_block(
    business_id: uuid.UUID,
    payload: BlockCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Block:
    await _get_owned_business(business_id, current_user, db)

    block = Block(
        business_id=business_id,
        title=payload.title,
        description=payload.description,
        order=payload.order,
    )
    # Semantic vector for TWIRA I / pgvector search; no-op when no embedding key.
    # Embedding is best-effort: a provider outage/quota error must not block block creation.
    try:
        emb = await generate_embedding(block_embedding_text(payload.title, payload.description))
    except Exception as e:
        logger.warning("Embedding generation failed for new block on %s: %s", business_id, e)
        emb = None
    if emb:
        block.embedding = emb
    db.add(block)
    await db.flush()
    await db.refresh(block, ["media"])
    return block


@router.get("", response_model=list[BlockOut])
async def list_blocks(
    business_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(_get_optional_user),
) -> list[Block]:
    # The owner sees every block (the profile edit page needs all of them);
    # non-owners and anonymous callers only see is_public blocks.
    result = await db.execute(select(Business).where(Business.id == business_id))
    business = result.scalar_one_or_none()
    is_owner = (
        business is not None
        and current_user is not None
        and business.owner_id == current_user.id
    )

    query = (
        select(Block)
        .where(Block.business_id == business_id)
        .options(selectinload(Block.media))
        .order_by(Block.order)
    )
    if not is_owner:
        query = query.where(Block.is_public.is_(True))
    result = await db.execute(query)
    return list(result.scalars().all())


@blocks_router.get("/{block_id}", response_model=BlockOut)
async def get_block(
    block_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(_get_optional_user),
) -> Block:
    """Public per-block permalink — addressable independent of the parent entity.

    Non-public blocks 404 for everyone except their owner, so this can't be
    used to enumerate private blocks by guessing IDs.
    """
    result = await db.execute(
        select(Block).where(Block.id == block_id).options(selectinload(Block.media))
    )
    block = result.scalar_one_or_none()
    if not block:
        raise HTTPException(status_code=404, detail="Block not found")

    if not block.is_public:
        business = (
            await db.execute(select(Business).where(Business.id == block.business_id))
        ).scalar_one_or_none()
        is_owner = (
            business is not None
            and current_user is not None
            and business.owner_id == current_user.id
        )
        if not is_owner:
            raise HTTPException(status_code=404, detail="Block not found")

    return block


@blocks_router.patch("/reorder", status_code=200)
async def reorder_blocks(
    payload: BlockReorder,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    for i, block_id in enumerate(payload.block_ids):
        result = await db.execute(select(Block).where(Block.id == block_id))
        block = result.scalar_one_or_none()
        if not block:
            continue
        await _get_owned_business(block.business_id, current_user, db)
        block.order = i
    return {"ok": True}


@blocks_router.patch("/{block_id}", response_model=BlockOut)
async def update_block(
    block_id: uuid.UUID,
    payload: BlockUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Block:
    result = await db.execute(
        select(Block).where(Block.id == block_id).options(selectinload(Block.media))
    )
    block = result.scalar_one_or_none()
    if not block:
        raise HTTPException(status_code=404, detail="Block not found")

    await _get_owned_business(block.business_id, current_user, db)
    fields = payload.model_dump(exclude_none=True)
    for field, value in fields.items():
        setattr(block, field, value)
    # Re-embed when the semantic text changed; no-op when no embedding key.
    # Embedding is best-effort: a provider outage/quota error must not block the update.
    if "title" in fields or "description" in fields:
        try:
            emb = await generate_embedding(block_embedding_text(block.title, block.description))
        except Exception as e:
            logger.warning("Embedding generation failed for block %s: %s", block_id, e)
            emb = None
        if emb:
            block.embedding = emb
    return block


@blocks_router.delete("/{block_id}", status_code=204)
async def delete_block(
    block_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    result = await db.execute(select(Block).where(Block.id == block_id))
    block = result.scalar_one_or_none()
    if not block:
        raise HTTPException(status_code=404, detail="Block not found")
    await _get_owned_business(block.business_id, current_user, db)
    await db.delete(block)
