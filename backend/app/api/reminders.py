from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.reminder_rule import ReminderRule
from app.models.user import User
from app.schemas.reminder import (
    ReminderRuleCreate,
    ReminderRuleOut,
    ReminderRuleUpdate,
    ReminderRunOut,
)
from app.services.reminder import _DISPATCH, run_all_rules

router = APIRouter(prefix="/reminder-rules", tags=["reminder-rules"])


@router.get("", response_model=list[ReminderRuleOut])
async def list_rules(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = (
        select(ReminderRule)
        .where(ReminderRule.tenant_id == user.tenant_id)
        .order_by(ReminderRule.created_at.desc())
    )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("", response_model=ReminderRuleOut, status_code=201)
async def create_rule(
    body: ReminderRuleCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if body.trigger_type not in _DISPATCH:
        raise HTTPException(status_code=400, detail=f"不支持的触发器类型: {body.trigger_type}")
    rule = ReminderRule(tenant_id=user.tenant_id, **body.model_dump())
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


async def _get_rule_or_404(db: AsyncSession, tenant_id: int, rule_id: int) -> ReminderRule:
    rule = await db.get(ReminderRule, rule_id)
    if rule is None or rule.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="提醒规则不存在")
    return rule


@router.put("/{rule_id}", response_model=ReminderRuleOut)
async def update_rule(
    rule_id: int,
    body: ReminderRuleUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rule = await _get_rule_or_404(db, user.tenant_id, rule_id)
    updates = body.model_dump(exclude_unset=True)
    if "trigger_type" in updates and updates["trigger_type"] not in _DISPATCH:
        raise HTTPException(status_code=400, detail=f"不支持的触发器类型: {updates['trigger_type']}")
    for field, value in updates.items():
        setattr(rule, field, value)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/{rule_id}", status_code=204)
async def delete_rule(
    rule_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rule = await _get_rule_or_404(db, user.tenant_id, rule_id)
    await db.delete(rule)
    await db.commit()


@router.post("/run", response_model=ReminderRunOut)
async def run_rules_manually(user: User = Depends(get_current_user)):
    """手动跑一轮本租户启用的提醒规则（调度循环仍跑全部租户）。"""
    return await run_all_rules(tenant_id=user.tenant_id)
