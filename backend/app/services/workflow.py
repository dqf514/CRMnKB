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

# run_workflow 客户扫描分批大小（keyset 分页，避免租户客户全表一次性载入内存）
_CUSTOMER_BATCH_SIZE = 500

# conditions DSL 白名单（作用于 customers 表字段，防注入）
CONDITION_FIELDS = {"name", "industry", "status", "source", "phone", "email"}
CONDITION_OPS = {"eq", "ne", "contains", "gt", "lt"}


def _parse_hhmm(value: str) -> time:
    hour, minute = str(value).split(":")[:2]
    return time(int(hour), int(minute))


def _to_local(now: datetime) -> datetime:
    """统一时区基准：aware 直转服务器本地时间；naive 按项目约定视为 UTC 再转本地。"""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone()


def _local_day_start_utc(now: datetime) -> datetime:
    """服务器本地"今日"00:00 对应的 naive UTC 时间（与库中 naive TIMESTAMP 列比较用）。"""
    local = _to_local(now)
    start = datetime.combine(local.date(), time.min, tzinfo=local.tzinfo)
    return start.astimezone(timezone.utc).replace(tzinfo=None)


def should_run(
    trigger_type: str, trigger_config: dict, last_run_at: datetime | None, now: datetime
) -> bool:
    """判断工作流本轮是否该触发。纯函数。

    时区基准：daily/weekly 的 time/weekday 按**服务器本地时间**解释（配置 09:00
    即本地 09:00 触发，与晨报口径一致）；now/last_run_at 可为 naive（视为 UTC）
    或 aware。interval 只比较时间差，与时区无关。"""
    if trigger_type == "interval":
        minutes = int(trigger_config.get("interval_minutes", 60))
        return last_run_at is None or (now - last_run_at) >= timedelta(minutes=minutes)
    if trigger_type == "daily":
        t = _parse_hhmm(trigger_config.get("time", "09:00"))
        now_local = _to_local(now)
        last_local = _to_local(last_run_at).date() if last_run_at is not None else None
        return now_local.time() >= t and (last_local is None or last_local < now_local.date())
    if trigger_type == "weekly":
        weekday = int(trigger_config.get("weekday", 1))  # 1-7（本地时间的星期）
        t = _parse_hhmm(trigger_config.get("time", "09:00"))
        now_local = _to_local(now)
        last_local = _to_local(last_run_at).date() if last_run_at is not None else None
        return (
            now_local.isoweekday() == weekday
            and now_local.time() >= t
            and (last_local is None or last_local < now_local.date())
        )
    if trigger_type == "birthday":
        # 每天最多评估一次（当天生日的客户在当天触发；"当天"按本地日期）
        return last_run_at is None or _to_local(last_run_at).date() < _to_local(now).date()
    if trigger_type == "condition":
        # 每轮调度都评估，重复动作由 action 层去重
        return True
    return False


def _cmp_pair(actual, expected):
    """数值感知比较：两边都能转 float 按数值比，否则按字符串比（字典序）。"""
    try:
        return float(actual), float(expected)
    except (TypeError, ValueError):
        return str(actual), str(expected)


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
    if op in ("gt", "lt"):
        if actual is None:
            return False
        a, b = _cmp_pair(actual, expected)
        return a > b if op == "gt" else a < b
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
    now_local = _to_local(now)  # 生日/"今日"口径与触发时间一致：服务器本地日期
    detail_lines: list[str] = []
    status = "success"
    matched_count = 0
    try:
        if wf.action_type == "send_email" and not settings.SMTP_HOST:
            raise RuntimeError("SMTP 未配置")
        # 批量预取去重标记（一次性查询），消除逐客户 N+1（原每客户 LIKE 全表扫 + 两条 count）
        # "今日"按本地日期解释，换算成 naive UTC 下界与 created_at（naive UTC TIMESTAMP）比较
        day_start = _local_day_start_utc(now)
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
        # 客户按 id keyset 分页逐批扫描执行，不再一次性全表加载
        last_id = 0
        while True:
            batch = (
                (
                    await session.execute(
                        select(Customer)
                        .where(
                            Customer.tenant_id == wf.tenant_id,
                            Customer.deleted_at.is_(None),
                            Customer.id > last_id,
                        )
                        .order_by(Customer.id)
                        .limit(_CUSTOMER_BATCH_SIZE)
                    )
                )
                .scalars()
                .all()
            )
            if not batch:
                break
            for customer in batch:
                last_id = customer.id
                ctx = _customer_context(customer)
                if wf.trigger_type == "birthday" and not is_birthday_today(customer.birthday, now_local):
                    continue
                if not eval_conditions(wf.conditions or [], ctx):
                    continue
                matched_count += 1
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
