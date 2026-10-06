"""日历模块：任务到期 / 商机预计成交日 / 客户生日三类事件的聚合查询与 ICS 订阅源。

时间口径（项目约定）：DB TIMESTAMP 列存 naive UTC，展示按服务器本地日期；
商机 expected_close_date 与客户 birthday 本身是 date 列，不做时区换算。
可见性口径与任务列表一致（api/tasks.py）：租户全员可见（仅按 tenant_id 过滤）。
"""
import jwt
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.security import ICS_TOKEN_TYPE, create_ics_token, decode_token
from app.models.customer import Customer
from app.models.opportunity import Opportunity
from app.models.task import Task
from app.models.user import User

router = APIRouter(prefix="/calendar", tags=["calendar"])

# ICS 订阅源的时间窗口：过去 30 天 + 未来 90 天（按服务器本地日期）
_FEED_PAST_DAYS = 30
_FEED_FUTURE_DAYS = 90


def _to_local_date(dt: datetime) -> date:
    """naive UTC datetime → 服务器本地日期。"""
    return dt.replace(tzinfo=timezone.utc).astimezone().date()


def _local_today() -> date:
    return datetime.now(timezone.utc).astimezone().date()


def _birthday_occurrences(birthday: date, start: date, end: date) -> list[date]:
    """生日在 [start, end] 区间内的每一次 occurrence（按月日匹配区间内每一年）。"""
    occurrences = []
    for year in range(start.year, end.year + 1):
        try:
            occ = date(year, birthday.month, birthday.day)
        except ValueError:
            continue  # 2 月 29 日生日遇非闰年：当年跳过
        if start <= occ <= end:
            occurrences.append(occ)
    return occurrences


async def _collect_events(
    db: AsyncSession, tenant_id: int, start: date, end: date
) -> list[dict]:
    """聚合租户在 [start, end]（本地日期，含边界）内的三类日历事件，按日期排序。"""
    events: list[dict] = []

    # 1) 任务到期：due_date 为 naive UTC，查库放宽一天、再按本地日期精确过滤
    stmt = (
        select(Task, Customer.name)
        .outerjoin(Customer, Customer.id == Task.customer_id)
        .where(
            Task.tenant_id == tenant_id,
            Task.due_date.is_not(None),
            Task.due_date >= datetime.combine(start - timedelta(days=1), time.min),
            Task.due_date < datetime.combine(end + timedelta(days=2), time.min),
        )
    )
    for task, customer_name in (await db.execute(stmt)).all():
        local_date = _to_local_date(task.due_date)
        if not (start <= local_date <= end):
            continue
        events.append({
            "id": f"task:{task.id}",
            "type": "task",
            "title": task.title,
            "date": local_date.isoformat(),
            "customer_id": task.customer_id,
            "customer_name": customer_name,
            "done": task.status == "completed",
            "url": "/tasks",
        })

    # 2) 商机预计成交日：expected_close_date 为 date 列，直接按区间过滤
    stmt = (
        select(Opportunity, Customer.name)
        .join(Customer, Customer.id == Opportunity.customer_id)
        .where(
            Customer.tenant_id == tenant_id,
            Customer.deleted_at.is_(None),
            Opportunity.expected_close_date.is_not(None),
            Opportunity.expected_close_date >= start,
            Opportunity.expected_close_date <= end,
        )
    )
    for opp, customer_name in (await db.execute(stmt)).all():
        events.append({
            "id": f"opp:{opp.id}",
            "type": "opportunity",
            "title": f"{opp.name} 预计成交",
            "date": opp.expected_close_date.isoformat(),
            "customer_id": opp.customer_id,
            "customer_name": customer_name,
            "done": False,
            "url": f"/customers/{opp.customer_id}",
        })

    # 3) 客户生日：按月日匹配区间内每一年的 occurrence
    stmt = select(Customer).where(
        Customer.tenant_id == tenant_id,
        Customer.deleted_at.is_(None),
        Customer.birthday.is_not(None),
    )
    for customer in (await db.execute(stmt)).scalars().all():
        for occ in _birthday_occurrences(customer.birthday, start, end):
            events.append({
                "id": f"birthday:{customer.id}",
                "type": "birthday",
                "title": f"{customer.name} 生日",
                "date": occ.isoformat(),
                "customer_id": customer.id,
                "customer_name": customer.name,
                "done": False,
                "url": f"/customers/{customer.id}",
            })

    events.sort(key=lambda e: (e["date"], e["id"]))
    return events


@router.get("/events")
async def list_events(
    start: date = Query(..., description="起始日期 YYYY-MM-DD（服务器本地日期，含）"),
    end: date = Query(..., description="结束日期 YYYY-MM-DD（服务器本地日期，含）"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if end < start:
        raise HTTPException(status_code=422, detail="end 不能早于 start")
    return {"events": await _collect_events(db, user.tenant_id, start, end)}


@router.get("/feed-token")
async def get_feed_token(user: User = Depends(get_current_user)):
    """签发 ICS 订阅令牌（180 天），前端拼 origin 后展示给用户订阅。"""
    token = create_ics_token(user.id)
    return {"token": token, "path": f"/api/v1/calendar/feed.ics?token={token}"}


def _ics_escape(value: str) -> str:
    """ICS 文本转义：反斜杠 / 分号 / 逗号 / 换行。"""
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _build_ics(events: list[dict]) -> str:
    """手写 ICS（RFC 5545 子集）：全天事件（DTSTART;VALUE=DATE 本地日期），
    生日带 RRULE:FREQ=YEARLY；CRLF 行尾，中文 UTF-8 直出。"""
    dtstamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//kbcrm//calendar//ZH-CN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    for event in events:
        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{event['id']}@kbcrm")
        lines.append(f"DTSTAMP:{dtstamp}")
        lines.append(f"DTSTART;VALUE=DATE:{event['date'].replace('-', '')}")
        if event["type"] == "birthday":
            lines.append("RRULE:FREQ=YEARLY")
        lines.append(f"SUMMARY:{_ics_escape(event['title'])}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


@router.get("/feed.ics")
async def calendar_feed(
    token: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """ICS 订阅源（text/calendar）：未来 90 天 + 过去 30 天的三类事件。

    日历客户端订阅不能携带 Authorization 头，故用 ?token= 专用订阅令牌
    （typ=ics，无法当登录 JWT 用，见 api/deps.py 的 typ 拒绝逻辑）。
    """
    if not token:
        raise HTTPException(status_code=401, detail="未提供订阅令牌")
    try:
        payload = decode_token(token)
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="订阅令牌无效或已过期")
    if payload.get("typ") != ICS_TOKEN_TYPE:
        raise HTTPException(status_code=403, detail="令牌类型不符，仅接受 ICS 订阅令牌")
    user = await db.get(User, user_id)
    if user is None or getattr(user, "status", 1) == 0:
        raise HTTPException(status_code=401, detail="用户不存在或已停用")

    today = _local_today()
    events = await _collect_events(
        db,
        user.tenant_id,
        today - timedelta(days=_FEED_PAST_DAYS),
        today + timedelta(days=_FEED_FUTURE_DAYS),
    )
    return PlainTextResponse(_build_ics(events), media_type="text/calendar")
