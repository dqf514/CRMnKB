from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.customer import Customer
from app.models.opportunity import Opportunity
from app.models.user import User
from app.schemas.opportunity import OpportunityCreate, OpportunityOut, OpportunityUpdate

router = APIRouter(prefix="/opportunities", tags=["opportunities"])


@router.get("", response_model=list[OpportunityOut])
async def list_opportunities(
    customer_id: int | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = (
        select(Opportunity)
        .join(Customer, Customer.id == Opportunity.customer_id)
        .where(Customer.tenant_id == user.tenant_id, Customer.deleted_at.is_(None))
        .order_by(Opportunity.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    if customer_id is not None:
        stmt = stmt.where(Opportunity.customer_id == customer_id)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("", response_model=OpportunityOut, status_code=201)
async def create_opportunity(
    body: OpportunityCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    customer = await db.get(Customer, body.customer_id)
    if customer is None or customer.tenant_id != user.tenant_id or customer.deleted_at is not None:
        raise HTTPException(status_code=404, detail="客户不存在")
    opportunity = Opportunity(owner_id=user.id, **body.model_dump())
    db.add(opportunity)
    await db.commit()
    await db.refresh(opportunity)
    return opportunity


async def _get_opportunity_or_404(db: AsyncSession, tenant_id: int, opp_id: int) -> Opportunity:
    stmt = (
        select(Opportunity)
        .join(Customer, Customer.id == Opportunity.customer_id)
        .where(Opportunity.id == opp_id, Customer.tenant_id == tenant_id, Customer.deleted_at.is_(None))
    )
    opportunity = (await db.execute(stmt)).scalar_one_or_none()
    if opportunity is None:
        raise HTTPException(status_code=404, detail="商机不存在")
    return opportunity


@router.put("/{opp_id}", response_model=OpportunityOut)
async def update_opportunity(
    opp_id: int,
    body: OpportunityUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    opportunity = await _get_opportunity_or_404(db, user.tenant_id, opp_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(opportunity, field, value)
    await db.commit()
    await db.refresh(opportunity)
    return opportunity


@router.delete("/{opp_id}", status_code=204)
async def delete_opportunity(
    opp_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    opportunity = await _get_opportunity_or_404(db, user.tenant_id, opp_id)
    await db.delete(opportunity)
    await db.commit()
