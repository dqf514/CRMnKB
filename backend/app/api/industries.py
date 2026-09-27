from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.industry import Industry
from app.models.user import User
from app.schemas.industry import IndustryCreate, IndustryOut, IndustryUpdate

router = APIRouter(prefix="/industries", tags=["industries"])


@router.get("", response_model=list[IndustryOut])
async def list_industries(
    enabled_only: bool = Query(True),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(Industry).where(Industry.tenant_id == user.tenant_id)
    if enabled_only:
        stmt = stmt.where(Industry.enabled.is_(True))
    stmt = stmt.order_by(Industry.sort, Industry.id)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("", response_model=IndustryOut, status_code=201)
async def create_industry(
    body: IndustryCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    exists = await db.scalar(
        select(func.count())
        .select_from(Industry)
        .where(Industry.tenant_id == user.tenant_id, Industry.name == body.name)
    )
    if exists:
        raise HTTPException(status_code=409, detail="行业已存在")
    industry = Industry(
        tenant_id=user.tenant_id, name=body.name, sort=body.sort, enabled=body.enabled
    )
    db.add(industry)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="行业已存在")
    await db.refresh(industry)
    return industry


async def _get_industry_or_404(db: AsyncSession, tenant_id: int, industry_id: int) -> Industry:
    industry = await db.get(Industry, industry_id)
    if industry is None or industry.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="行业不存在")
    return industry


@router.put("/{industry_id}", response_model=IndustryOut)
async def update_industry(
    industry_id: int,
    body: IndustryUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    industry = await _get_industry_or_404(db, user.tenant_id, industry_id)
    updates = body.model_dump(exclude_unset=True)
    if updates.get("name") is not None and updates["name"] != industry.name:
        exists = await db.scalar(
            select(func.count())
            .select_from(Industry)
            .where(
                Industry.tenant_id == user.tenant_id,
                Industry.name == updates["name"],
                Industry.id != industry_id,
            )
        )
        if exists:
            raise HTTPException(status_code=409, detail="行业已存在")
    for field, value in updates.items():
        setattr(industry, field, value)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="行业已存在")
    await db.refresh(industry)
    return industry


@router.delete("/{industry_id}", status_code=204)
async def delete_industry(
    industry_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # 不校验引用：客户 industries 中残留的文本由前端过滤展示
    industry = await _get_industry_or_404(db, user.tenant_id, industry_id)
    await db.delete(industry)
    await db.commit()
