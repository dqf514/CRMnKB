"""每日工作台 / 日报 / 晨报 聚合服务。

- today_overview：今日任务、逾期任务、久未跟进客户、今日新增、未读通知、团队动态
- daily_report：数据驱动日报（跟进/完成任务/上传/笔记/新增客户）
- send_morning_briefs：每日晨报通知（8:30 后每人一条，当日去重）
"""
import logging
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.customer import Customer
from app.models.follow_up import FollowUpRecord
from app.models.library_file import LibraryFile
from app.models.notebook import Notebook, NotebookNote
from app.models.notification import Notification
from app.models.task import Task
from app.models.user import User

logger = logging.getLogger(__name__)

STALE_DAYS = 7  # 超过 N 天无跟进视为"久未跟进"


def _today_range() -> tuple[datetime, datetime]:
    """今日 00:00 ~ 23:59（naive UTC，与库中 TIMESTAMP 列一致）。"""
    today = datetime.now(timezone.utc).date()
    return datetime.combine(today, time.min), datetime.combine(today, time.max)


async def today_overview(db: AsyncSession, user: User) -> dict:
    start, end = _today_range()
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # 今日到期任务（待办，分配给我）
    today_tasks = (
        await db.execute(
            select(Task)
            .where(
                Task.tenant_id == user.tenant_id,
                Task.user_id == user.id,
                Task.status == "pending",
                Task.due_date.between(start, end),
            )
            .order_by(Task.due_date.asc())
        )
    ).scalars().all()

    # 逾期任务（待办，到期日已过）
    overdue_tasks = (
        await db.execute(
            select(Task)
            .where(
                Task.tenant_id == user.tenant_id,
                Task.user_id == user.id,
                Task.status == "pending",
                Task.due_date < start,
            )
            .order_by(Task.due_date.asc())
        )
    ).scalars().all()

    # 久未跟进客户（全租户共享客户池）：最近一次跟进 > STALE_DAYS 或从未跟进
    last_fu = (
        select(
            FollowUpRecord.customer_id,
            func.max(FollowUpRecord.created_at).label("last_at"),
        )
        .group_by(FollowUpRecord.customer_id)
        .subquery()
    )
    stale_rows = (
        await db.execute(
            select(Customer, last_fu.c.last_at)
            .outerjoin(last_fu, last_fu.c.customer_id == Customer.id)
            .where(Customer.tenant_id == user.tenant_id, Customer.deleted_at.is_(None))
        )
    ).all()
    stale_customers = []
    for c, last_at in stale_rows:
        days = (now - last_at).days if last_at else (now - c.created_at).days
        if last_at is None or days >= STALE_DAYS:
            stale_customers.append({
                "id": c.id, "name": c.name, "company": c.company,
                "days": days, "never": last_at is None,
            })
    stale_customers.sort(key=lambda x: x["days"], reverse=True)

    # 今日新增计数
    async def _count(model, *filters):
        return await db.scalar(
            select(func.count()).select_from(model).where(*filters)
        ) or 0

    today_new = {
        "files": await _count(
            LibraryFile, LibraryFile.tenant_id == user.tenant_id,
            LibraryFile.created_at.between(start, end), LibraryFile.deleted_at.is_(None),
        ),
        "followups": await _count(
            FollowUpRecord, FollowUpRecord.user_id == user.id,
            FollowUpRecord.created_at.between(start, end),
        ),
        "customers": await _count(
            Customer, Customer.tenant_id == user.tenant_id,
            Customer.created_at.between(start, end), Customer.deleted_at.is_(None),
        ),
        "notes": await _count(
            NotebookNote, NotebookNote.tenant_id == user.tenant_id,
            NotebookNote.created_at.between(start, end),
        ),
    }

    unread = await _count(
        Notification, Notification.tenant_id == user.tenant_id,
        Notification.user_id == user.id, Notification.is_read.is_(False),
    )

    # 团队今日动态（审计日志，去掉登录噪声）
    activity_rows = (
        await db.execute(
            select(AuditLog, User.name, User.username)
            .outerjoin(User, User.id == AuditLog.user_id)
            .where(
                AuditLog.tenant_id == user.tenant_id,
                AuditLog.created_at.between(start, end),
                AuditLog.action != "login",
            )
            .order_by(AuditLog.created_at.desc())
            .limit(15)
        )
    ).all()
    activity = [
        {
            "user": name or username or "系统",
            "action": a.action,
            "resource_type": a.resource_type,
            "detail": a.detail or {},
            "created_at": a.created_at,
        }
        for a, name, username in activity_rows
    ]

    def _task_out(t: Task) -> dict:
        return {
            "id": t.id, "title": t.title, "priority": t.priority,
            "due_date": t.due_date, "customer_id": t.customer_id, "type": t.type,
        }

    return {
        "today_tasks": [_task_out(t) for t in today_tasks],
        "overdue_tasks": [_task_out(t) for t in overdue_tasks],
        "stale_customers": stale_customers[:8],
        "today_new": today_new,
        "unread_notifications": unread,
        "activity": activity,
    }


async def daily_report(
    db: AsyncSession, user: User, day: str | None, team: bool
) -> dict:
    """日报：指定日期（默认今天）每人 跟进/完成任务/上传/笔记/新增客户。
    team=True 仅 admin 可用，返回全员；否则只返回本人。"""
    if day:
        d = datetime.fromisoformat(day).date()
    else:
        d = datetime.now(timezone.utc).date()
    start = datetime.combine(d, time.min)
    end = datetime.combine(d, time.max)

    if team and user.role != "admin":
        team = False

    if team:
        users = (
            await db.execute(
                select(User).where(
                    User.tenant_id == user.tenant_id,
                    User.status == 1,
                )
            )
        ).scalars().all()
    else:
        users = [user]

    async def _user_digest(u: User) -> dict:
        followups = await db.scalar(
            select(func.count()).select_from(FollowUpRecord).where(
                FollowUpRecord.user_id == u.id, FollowUpRecord.created_at.between(start, end)
            )
        ) or 0
        tasks_done = await db.scalar(
            select(func.count()).select_from(Task).where(
                Task.tenant_id == u.tenant_id, Task.user_id == u.id,
                Task.status == "done", Task.completed_at.between(start, end),
            )
        ) or 0
        files = await db.scalar(
            select(func.count()).select_from(LibraryFile).where(
                LibraryFile.tenant_id == u.tenant_id, LibraryFile.owner_id == u.id,
                LibraryFile.created_at.between(start, end),
            )
        ) or 0
        notes = await db.scalar(
            select(func.count()).select_from(NotebookNote)
            .join(Notebook, Notebook.id == NotebookNote.notebook_id)
            .where(
                Notebook.tenant_id == u.tenant_id, Notebook.created_by == u.id,
                NotebookNote.created_at.between(start, end),
            )
        ) or 0
        customers_new = await db.scalar(
            select(func.count()).select_from(AuditLog).where(
                AuditLog.tenant_id == u.tenant_id, AuditLog.user_id == u.id,
                AuditLog.action == "create", AuditLog.resource_type == "customer",
                AuditLog.created_at.between(start, end),
            )
        ) or 0
        return {
            "user_id": u.id,
            "user_name": u.name or u.username,
            "followups": followups,
            "tasks_done": tasks_done,
            "files": files,
            "notes": notes,
            "customers_new": customers_new,
        }

    digests = [await _user_digest(u) for u in users]
    return {"date": str(d), "team": team, "items": digests}


async def send_morning_briefs() -> int:
    """每日晨报：服务器本地时间 8:30 后给每个启用用户发一条当日概览通知（按本地日期去重）。返回发送数。"""
    from app.database import AsyncSessionLocal

    now_local = datetime.now()  # 服务器本地时间（部署地时区）
    if (now_local.hour, now_local.minute) < (8, 30):
        return 0
    today_local = now_local.date()
    start, end = _today_range()
    sent = 0
    async with AsyncSessionLocal() as db:
        users = (
            await db.execute(select(User).where(User.status == 1))
        ).scalars().all()
        for u in users:
            # 去重：该用户最近一次晨报的本地日期 = 今天则跳过
            last = (
                await db.execute(
                    select(Notification.created_at)
                    .where(Notification.user_id == u.id, Notification.type == "morning_brief")
                    .order_by(Notification.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if last is not None:
                last_local = last.replace(tzinfo=timezone.utc).astimezone().date()
                if last_local == today_local:
                    continue
            due_today = await db.scalar(
                select(func.count()).select_from(Task).where(
                    Task.tenant_id == u.tenant_id, Task.user_id == u.id,
                    Task.status == "pending", Task.due_date.between(start, end),
                )
            ) or 0
            overdue = await db.scalar(
                select(func.count()).select_from(Task).where(
                    Task.tenant_id == u.tenant_id, Task.user_id == u.id,
                    Task.status == "pending", Task.due_date < start,
                )
            ) or 0
            db.add(Notification(
                tenant_id=u.tenant_id,
                user_id=u.id,
                title="早安，今日工作概览",
                content=f"今日到期任务 {due_today} 项，逾期任务 {overdue} 项。打开工作台开始今天的工作。",
                type="morning_brief",
            ))
            sent += 1
        await db.commit()
    if sent:
        logger.info("晨报已发送: %d 人", sent)
    return sent
