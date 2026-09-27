import logging
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.customer import Customer
from app.models.notification import Notification
from app.models.task import Task
from app.models.workflow import Workflow
from app.models.workflow_run import WorkflowRun
from app.services.email import build_email, send_email
from app.services.reminder import _fallback_user_id, render_template

logger = logging.getLogger(__name__)

TRIGGER_TYPES = {"interval", "daily", "weekly", "birthday", "condition"}
ACTION_TYPES = {"send_email", "create_task", "create_notification"}

# conditions DSL 白名单（作用于 customers 表字段，防注入）
CONDITION_FIELDS = {"name", "industry", "status", "source", "phone", "email"}
CONDITION_OPS = {"eq", "ne", "contains", "gt", "lt"}


def _parse_hhmm(value: str) -> time:
    hour, minute = str(value).split(":")[:2]
    return time(int(hour), int(minute))


def should_run(
    trigger_type: str, trigger_config: dict, last_run_at: datetime | None, now: datetime
) -> bool:
    """判断工作流本轮是否该触发。纯函数（now/last_run_at 需同为 aware 或同为 naive）。"""
    if trigger_type == "interval":
        minutes = int(trigger_config.get("interval_minutes", 60))
        return last_run_at is None or (now - last_run_at) >= timedelta(minutes=minutes)
    if trigger_type == "daily":
        t = _parse_hhmm(trigger_config.get("time", "09:00"))
        return now.time() >= t and (last_run_at is None or last_run_at.date() < now.date())
    if trigger_type == "weekly":
        weekday = int(trigger_config.get("weekday", 1))  # 1-7
        t = _parse_hhmm(trigger_config.get("time", "09:00"))
        return (
            now.isoweekday() == weekday
            and now.time() >= t
            and (last_run_at is None or last_run_at.date() < now.date())
        )
    if trigger_type == "birthday":
        # 每天最多评估一次（当天生日的客户在当天触发）
        return last_run_at is None or last_run_at.date() < now.date()
    if trigger_type == "condition":
        # 每轮调度都评估，重复动作由 action 层去重
        return True
    return False


def eval_condition(cond: dict, customer: dict) -> bool:
    """单条条件求值；字段/操作不在白名单内抛 ValueError。纯函数。"""
    field = cond.get("field")
    if field not in CONDITION_FIELDS:
        raise ValueError(f"条件字段不在白名单内: {field}")
    op = cond.get("op")
    if op not in CONDITION_OPS:
        raise ValueError(f"不支持的条件操作: {op}")
    actual = customer.get(field)
    expected = cond.get("value")
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op == "contains":
        return actual is not None and str(expected) in str(actual)
    if op == "gt":
        return actual is not None and str(actual) > str(expected)
    if op == "lt":
        return actual is not None and str(actual) < str(expected)
    return False


def eval_conditions(conditions: list, customer: dict) -> bool:
    """全部条件 AND。纯函数。"""
    return all(eval_condition(c, customer) for c in conditions)


def is_birthday_today(birthday: date | None, now: datetime) -> bool:
    """生日触发内置条件：月日相同即当天生日。纯函数。"""
    return (
        birthday is not None
        and (birthday.month, birthday.day) == (now.month, now.day)
    )


def _customer_context(customer: Customer) -> dict:
    ctx = {f: getattr(customer, f) for f in CONDITION_FIELDS}
    ctx["customer_name"] = customer.name
    ctx["customer_id"] = customer.id
    return ctx


async def _execute_action(
    session: AsyncSession, wf: Workflow, customer: Customer, ctx: dict, now: datetime,
    emailed_markers: set[str],
    open_rule_tasks: set[tuple[int | None, str]],
    notified_keys: set[tuple[int | None, str]],
) -> str:
    """对单个匹配客户执行动作，返回 detail 行（含 customer:{id} 标记），绝不抛出。

    去重用调用方预取的集合判定，避免逐客户查询（原 N+1：每客户 LIKE 全表扫）。
    """
    cfg = wf.action_config or {}
    prefix = f"customer:{customer.id}"
    try:
        if wf.action_type == "send_email":
            if not customer.email:
                return f"{prefix} skipped: 无邮箱"
            if any(f"customer:{customer.id} emailed" in m for m in emailed_markers):
                return f"{prefix} skipped: 今日已发送"
            msg = build_email(
                cfg.get("subject", "来自 CRM 的问候"), cfg.get("template", ""), ctx
            )
            await send_email(customer.email, msg["subject"], msg["body"])
            return f"{prefix} emailed -> {customer.email}"

        if wf.action_type == "create_task":
            title = render_template(cfg.get("title", "跟进客户 {{customer_name}}"), ctx)
            if (customer.id, title) in open_rule_tasks:
                return f"{prefix} skipped: 已存在相同未完成任务"
            due = None
            if cfg.get("due_in_days") is not None:
                due = now + timedelta(days=int(cfg["due_in_days"]))
            user_id = customer.owner_id or await _fallback_user_id(session, wf.tenant_id)
            if user_id is None:
                return f"{prefix} failed: 无可分配用户"
            session.add(
                Task(
                    tenant_id=wf.tenant_id,
                    customer_id=customer.id,
                    user_id=user_id,
                    title=title,
                    type=cfg.get("type", "follow_up"),
                    priority=cfg.get("priority", "medium"),
                    due_date=due,
                    status="pending",
                    ai_generated=False,
                    source="rule",
                )
            )
            return f"{prefix} task created: {title}"

        if wf.action_type == "create_notification":
            title = render_template(cfg.get("title", "客户提醒"), ctx)
            user_id = customer.owner_id or await _fallback_user_id(session, wf.tenant_id)
            if user_id is None:
                return f"{prefix} failed: 无可分配用户"
            if (user_id, title) in notified_keys:
                return f"{prefix} skipped: 今日已通知"
            session.add(
                Notification(
                    tenant_id=wf.tenant_id,
                    user_id=user_id,
                    task_id=None,
                    title=title,
                    content=render_template(cfg.get("template", title), ctx),
                    type="workflow",
                    is_read=False,
                )
            )
            return f"{prefix} notified: {title}"

        return f"{prefix} failed: 未知动作类型 {wf.action_type}"
    except Exception as exc:
        return f"{prefix} failed: {exc}"


async def run_workflow(
    session: AsyncSession, wf: Workflow, now: datetime | None = None
) -> WorkflowRun:
    """执行一次工作流：匹配客户 → 逐客户执行动作 → 写 workflow_runs。
    失败只记 failed，绝不抛出。调用方负责 commit。"""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    detail_lines: list[str] = []
    status = "success"
    matched_count = 0
    try:
        if wf.action_type == "send_email" and not settings.SMTP_HOST:
            raise RuntimeError("SMTP 未配置")
        customers = (
            (
                await session.execute(
                    select(Customer).where(Customer.tenant_id == wf.tenant_id, Customer.deleted_at.is_(None))
                )
            )
            .scalars()
            .all()
        )
        matched = []
        for customer in customers:
            ctx = _customer_context(customer)
            if wf.trigger_type == "birthday" and not is_birthday_today(customer.birthday, now):
                continue
            if not eval_conditions(wf.conditions or [], ctx):
                continue
            matched.append((customer, ctx))
        matched_count = len(matched)
        # 批量预取去重标记（一次性查询），消除逐客户 N+1（原每客户 LIKE 全表扫 + 两条 count）
        day_start = datetime.combine(now.date(), time.min)  # created_at 为 naive TIMESTAMP
        emailed_markers: set[str] = set(
            (
                await session.execute(
                    select(WorkflowRun.detail).where(
                        WorkflowRun.workflow_id == wf.id,
                        WorkflowRun.created_at >= day_start,
                        WorkflowRun.detail.like("% emailed%"),
                    )
                )
            )
            .scalars()
            .all()
        )
        open_rule_tasks: set[tuple[int | None, str]] = {
            (cid, title)
            for cid, title in (
                await session.execute(
                    select(Task.customer_id, Task.title).where(
                        Task.tenant_id == wf.tenant_id,
                        Task.source == "rule",
                        Task.status.in_(["pending", "in_progress"]),
                    )
                )
            ).all()
        }
        notified_keys: set[tuple[int | None, str]] = {
            (uid, title)
            for uid, title in (
                await session.execute(
                    select(Notification.user_id, Notification.title).where(
                        Notification.tenant_id == wf.tenant_id,
                        Notification.created_at >= day_start,
                    )
                )
            ).all()
        }
        for customer, ctx in matched:
            line = await _execute_action(
                session, wf, customer, ctx, now,
                emailed_markers, open_rule_tasks, notified_keys,
            )
            detail_lines.append(line)
            if " failed" in line or ": failed" in line or "failed:" in line:
                status = "failed"
    except Exception as exc:
        status = "failed"
        detail_lines.append(f"error: {exc}")

    wf.last_run_at = now
    run = WorkflowRun(
        workflow_id=wf.id,
        status=status,
        matched_count=matched_count,
        detail="\n".join(detail_lines) or None,
    )
    session.add(run)
    await session.flush()  # 取 run.id
    return run


async def run_due_workflows() -> dict:
    """跑一轮所有到期工作流（按各自触发语义判断）。供调度循环使用。"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    executed = 0
    async with AsyncSessionLocal() as session:
        workflows = (
            (
                await session.execute(select(Workflow).where(Workflow.enabled.is_(True)))
            )
            .scalars()
            .all()
        )
        for wf in workflows:
            if not should_run(
                wf.trigger_type, wf.trigger_config or {}, wf.last_run_at, now
            ):
                continue
            try:
                # SAVEPOINT：单条失败只回滚本条，不丢弃本轮前面已成功工作流的结果
                async with session.begin_nested():
                    await run_workflow(session, wf, now)
                executed += 1
            except Exception:
                # run_workflow 内部已基本不抛；此处兜底防单条失败影响整轮
                logger.exception("工作流 %s(%s) 调度失败，跳过", wf.id, wf.name)
        await session.commit()
    return {"executed": executed}
