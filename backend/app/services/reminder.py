import logging
import re
from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.notification import Notification
from app.models.reminder_rule import ReminderRule
from app.models.task import Task
from app.models.user import User

logger = logging.getLogger(__name__)

# 已终结的商机阶段（不再产生"停滞"提醒）。
# 与商机阶段枚举对齐：prospecting/qualification/proposal/negotiation/closed_won/closed_lost
CLOSED_STAGES = {"closed_won", "closed_lost"}

DEFAULT_TEMPLATES = {
    "days_since_last_followup": "客户 {{customer_name}} 已 {{days}} 天未跟进，请尽快联系",
    "opportunity_stagnant": "商机 {{opportunity_name}}（客户：{{customer_name}}）已 {{days}} 天未更新，请跟进",
    "task_due_soon": "任务即将到期：{{title}}",
}


def render_template(template: str, context: dict) -> str:
    """渲染 {{placeholder}} 占位符，未知占位符原样保留。纯函数。"""
    return re.sub(
        r"\{\{\s*(\w+)\s*\}\}",
        lambda m: str(context[m.group(1)]) if m.group(1) in context else m.group(0),
        template,
    )


def match_inactive_customers(
    rows: list[dict], threshold_days: int, now: datetime
) -> list[dict]:
    """超过 threshold_days 天未跟进的客户（无跟进记录则按客户创建时间算）。纯函数。"""
    matched = []
    for r in rows:
        ref = r.get("last_followup_at") or r.get("customer_created_at")
        if ref is None:
            continue
        days = (now - ref).days
        if days >= threshold_days:
            matched.append({**r, "days": days})
    return matched


def match_stagnant_opportunities(
    rows: list[dict], threshold_days: int, now: datetime
) -> list[dict]:
    """超过 threshold_days 天未更新且未终结的商机。纯函数。"""
    matched = []
    for r in rows:
        if r.get("stage") in CLOSED_STAGES:
            continue
        updated = r.get("updated_at")
        if updated is None:
            continue
        days = (now - updated).days
        if days >= threshold_days:
            matched.append({**r, "days": days})
    return matched


def match_due_soon_tasks(
    rows: list[dict], threshold_hours: int, now: datetime
) -> list[dict]:
    """threshold_hours 小时内到期且未完成的现有任务。纯函数。"""
    matched = []
    for r in rows:
        if r.get("status") not in ("pending", "in_progress"):
            continue
        due = r.get("due_date")
        if due is None:
            continue
        seconds_left = (due - now).total_seconds()
        if 0 <= seconds_left <= threshold_hours * 3600:
            matched.append({**r, "hours_left": round(seconds_left / 3600, 1)})
    return matched


def _aware(dt: datetime | None) -> datetime | None:
    """数据库 TIMESTAMP 列为 naive，统一按 UTC 转为 aware。"""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def _fallback_user_id(session: AsyncSession, tenant_id: int) -> int | None:
    # 只分配给启用（status=1）用户，避免把任务/通知派给停用账号
    return await session.scalar(
        select(User.id)
        .where(User.tenant_id == tenant_id, User.status == 1)
        .order_by(User.id)
        .limit(1)
    )


async def _open_rule_task_exists(
    session: AsyncSession, tenant_id: int, customer_id: int | None, title: str
) -> bool:
    stmt = (
        select(func.count())
        .select_from(Task)
        .where(
            Task.tenant_id == tenant_id,
            Task.customer_id == customer_id,
            Task.title == title,
            Task.source == "rule",
            Task.status.in_(["pending", "in_progress"]),
        )
    )
    return (await session.scalar(stmt)) > 0


async def _notification_exists(
    session: AsyncSession, tenant_id: int, task_id: int, title: str
) -> bool:
    stmt = (
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.tenant_id == tenant_id,
            Notification.task_id == task_id,
            Notification.title == title,
        )
    )
    return (await session.scalar(stmt)) > 0


async def _create_task_and_notification(
    session: AsyncSession,
    tenant_id: int,
    user_id: int | None,
    customer_id: int | None,
    title: str,
) -> tuple[int, int]:
    """去重后创建规则任务 + 通知，返回 (tasks_created, notifications_created)。"""
    if user_id is None:
        user_id = await _fallback_user_id(session, tenant_id)
    if user_id is None:
        return (0, 0)
    if await _open_rule_task_exists(session, tenant_id, customer_id, title):
        return (0, 0)
    task = Task(
        tenant_id=tenant_id,
        customer_id=customer_id,
        user_id=user_id,
        title=title,
        type="follow_up",
        priority="medium",
        status="pending",
        ai_generated=False,
        source="rule",
    )
    session.add(task)
    await session.flush()  # 取 task.id
    session.add(
        Notification(
            tenant_id=tenant_id,
            user_id=user_id,
            task_id=task.id,
            title=title,
            content=title,
            type="reminder",
            is_read=False,
        )
    )
    return (1, 1)


async def _run_days_since_last_followup(
    session: AsyncSession, rule: ReminderRule, now: datetime
) -> tuple[int, int]:
    threshold = int(rule.trigger_config.get("threshold_days", 7))
    template = rule.action_config.get(
        "template", DEFAULT_TEMPLATES["days_since_last_followup"]
    )
    stmt = text(
        """
        SELECT c.id AS customer_id, c.name AS customer_name, c.owner_id,
               c.created_at AS customer_created_at,
               MAX(f.created_at) AS last_followup_at
        FROM customers c
        LEFT JOIN follow_up_records f ON f.customer_id = c.id
        WHERE c.tenant_id = :tid AND c.deleted_at IS NULL
        GROUP BY c.id, c.name, c.owner_id, c.created_at
        """
    )
    result = await session.execute(stmt, {"tid": rule.tenant_id})
    rows = [
        {
            **dict(r),
            "customer_created_at": _aware(r["customer_created_at"]),
            "last_followup_at": _aware(r["last_followup_at"]),
        }
        for r in result.mappings().all()
    ]
    created = (0, 0)
    for m in match_inactive_customers(rows, threshold, now):
        title = render_template(
            template, {"customer_name": m["customer_name"], "days": m["days"]}
        )
        t, n = await _create_task_and_notification(
            session, rule.tenant_id, m["owner_id"], m["customer_id"], title
        )
        created = (created[0] + t, created[1] + n)
    return created


async def _run_opportunity_stagnant(
    session: AsyncSession, rule: ReminderRule, now: datetime
) -> tuple[int, int]:
    threshold = int(rule.trigger_config.get("threshold_days", 7))
    template = rule.action_config.get("template", DEFAULT_TEMPLATES["opportunity_stagnant"])
    stmt = text(
        """
        SELECT o.id AS opportunity_id, o.name AS opportunity_name, o.stage,
               o.updated_at, o.owner_id,
               c.id AS customer_id, c.name AS customer_name
        FROM opportunities o
        JOIN customers c ON c.id = o.customer_id
        WHERE c.tenant_id = :tid AND c.deleted_at IS NULL
        """
    )
    result = await session.execute(stmt, {"tid": rule.tenant_id})
    rows = [
        {**dict(r), "updated_at": _aware(r["updated_at"])}
        for r in result.mappings().all()
    ]
    created = (0, 0)
    for m in match_stagnant_opportunities(rows, threshold, now):
        title = render_template(
            template,
            {
                "opportunity_name": m["opportunity_name"],
                "customer_name": m["customer_name"],
                "days": m["days"],
            },
        )
        t, n = await _create_task_and_notification(
            session, rule.tenant_id, m["owner_id"], m["customer_id"], title
        )
        created = (created[0] + t, created[1] + n)
    return created


async def _run_task_due_soon(
    session: AsyncSession, rule: ReminderRule, now: datetime
) -> tuple[int, int]:
    threshold = int(rule.trigger_config.get("threshold_hours", 24))
    template = rule.action_config.get("template", DEFAULT_TEMPLATES["task_due_soon"])
    stmt = select(Task).where(
        Task.tenant_id == rule.tenant_id,
        Task.status.in_(["pending", "in_progress"]),
        Task.due_date.isnot(None),
    )
    result = await session.execute(stmt)
    rows = [
        {
            "task_id": t.id,
            "title": t.title,
            "user_id": t.user_id,
            "due_date": _aware(t.due_date),
            "status": t.status,
        }
        for t in result.scalars().all()
    ]
    notifications_created = 0
    for m in match_due_soon_tasks(rows, threshold, now):
        title = render_template(template, {"title": m["title"], "hours_left": m["hours_left"]})
        if await _notification_exists(session, rule.tenant_id, m["task_id"], title):
            continue
        session.add(
            Notification(
                tenant_id=rule.tenant_id,
                user_id=m["user_id"],
                task_id=m["task_id"],
                title=title,
                content=f"任务「{m['title']}」将于 {m['due_date']} 到期",
                type="reminder",
                is_read=False,
            )
        )
        notifications_created += 1
    return (0, notifications_created)


_DISPATCH = {
    "days_since_last_followup": _run_days_since_last_followup,
    "opportunity_stagnant": _run_opportunity_stagnant,
    "task_due_soon": _run_task_due_soon,
}


async def _run_rule(session: AsyncSession, rule: ReminderRule, now: datetime) -> tuple[int, int]:
    handler = _DISPATCH.get(rule.trigger_type)
    if handler is None:
        logger.warning("未知触发器类型: %s（规则 %s）", rule.trigger_type, rule.id)
        return (0, 0)
    return await handler(session, rule, now)


async def run_all_rules(tenant_id: int | None = None) -> dict:
    """跑一轮启用的提醒规则。tenant_id 限定单租户（手动触发）；None 跑全部（调度循环）。"""
    now = datetime.now(timezone.utc)
    tasks_created = 0
    notifications_created = 0
    async with AsyncSessionLocal() as session:
        stmt = select(ReminderRule).where(ReminderRule.enabled.is_(True))
        if tenant_id is not None:
            stmt = stmt.where(ReminderRule.tenant_id == tenant_id)
        rules = (await session.execute(stmt)).scalars().all()
        for rule in rules:
            try:
                # SAVEPOINT：单条失败只回滚本条，不丢弃本轮前面已成功规则的结果
                async with session.begin_nested():
                    t, n = await _run_rule(session, rule, now)
                tasks_created += t
                notifications_created += n
            except Exception:
                logger.exception("提醒规则 %s(%s) 执行失败，跳过", rule.id, rule.name)
        await session.commit()
    return {"tasks_created": tasks_created, "notifications_created": notifications_created}
