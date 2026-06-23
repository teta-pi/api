import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.business import Business
from app.models.block import Block
from app.models.user import User
from app.schemas.block import BlockCreate, BlockOut, BlockReorder, BlockUpdate

router = APIRouter(prefix="/businesses/{business_id}/blocks", tags=["blocks"])
blocks_router = APIRouter(prefix="/blocks", tags=["blocks"])


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
    db.add(block)
    await db.flush()
    await db.refresh(block, ["media"])
    return block


@router.get("", response_model=list[BlockOut])
async def list_blocks(
    business_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[Block]:
    result = await db.execute(
        select(Block)
        .where(Block.business_id == business_id)
        .options(selectinload(Block.media))
        .order_by(Block.order)
    )
    return list(result.scalars().all())


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
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(block, field, value)
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


@blocks_router.patch("/reorder", status_code=200)
async def reorder_blocks(
    payload: BlockReorder,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    for i, block_id in enumerate(payload.block_ids):
        result = await db.execute(select(Block).where(Block.id == block_id))
        block = result.scalar_one_or_none()
        if block:
            block.order = i
    return {"ok": True}
