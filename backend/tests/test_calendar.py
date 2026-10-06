"""日历模块测试：事件聚合（fake session）、ICS 订阅源与订阅令牌隔离、
跨租户隔离（aiosqlite 内存库跑真实 SQL，先例见 test_permissions_acl_real.py）。

时区注意：任务 due_date 统一取正午 12:00 UTC，保证在 UTC±12 任意机器时区下
换算出的本地日期仍是同一天，测试与运行环境时区无关。
"""
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import deps
from app.api.calendar import _collect_events
from app.core.security import (
    ICS_TOKEN_TYPE,
    create_access_token,
    create_file_token,
    create_ics_token,
    decode_token,
)
from app.database import get_db
from app.main import app
from app.models.base import Base
from app.models.customer import Customer
from app.models.opportunity import Opportunity
from app.models.task import Task
from app.models.tenant import Tenant
from app.models.user import User


class _FakeResult:
    def __init__(self, value=None):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._value if self._value is not None else []


class _FakeSession:
    def __init__(self):
        self._execute_queue = []
        self._get_queue = []

    def queue_execute(self, value):
        self._execute_queue.append(value)

    def queue_get(self, value):
        self._get_queue.append(value)

    async def execute(self, stmt, params=None):
        return _FakeResult(self._execute_queue.pop(0) if self._execute_queue else None)

    async def get(self, model, ident):
        return self._get_queue.pop(0) if self._get_queue else None


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _override_user(db, user=None):
    user = user or SimpleNamespace(
        id=1, tenant_id=1, username="u", name="用户", role="user", status=1
    )

    async def _fake_user():
        return user

    async def _fake_db():
        yield db

    app.dependency_overrides[deps.get_current_user] = _fake_user
    app.dependency_overrides[get_db] = _fake_db
    return user


def _task(tid, due, *, status="pending", title=None, customer_id=1, tenant_id=1):
    return SimpleNamespace(
        id=tid,
        tenant_id=tenant_id,
        customer_id=customer_id,
        title=title or f"任务{tid}",
        due_date=due,
        status=status,
    )


# ---------------------------------------------------------------------------
# 事件聚合
# ---------------------------------------------------------------------------

async def test_events_aggregation(client):
    """三类事件聚合：区间边界含首尾、done 标记、生日 occurrence、区间外剔除。"""
    db = _FakeSession()
    _override_user(db)
    # 任务：12 边界（start 当天）、11 completed、13 远在区间外（python 侧按本地日期剔除）
    db.queue_execute([
        (_task(11, datetime(2026, 3, 5, 12, 0), status="completed"), "客户甲"),
        (_task(12, datetime(2026, 3, 1, 12, 0), title="边界任务"), "客户甲"),
        (_task(13, datetime(2026, 5, 1, 12, 0), title="区间外任务"), None),
    ])
    # 商机：区间内一个
    db.queue_execute([
        (SimpleNamespace(id=5, customer_id=1, name="大单", expected_close_date=date(2026, 3, 10)), "客户甲"),
    ])
    # 客户生日：3 号生日落区间、6 号生日在区间外、闰日生日遇非闰年跳过
    db.queue_execute([
        SimpleNamespace(id=3, name="客户丙", birthday=date(1990, 3, 15)),
        SimpleNamespace(id=4, name="客户丁", birthday=date(1990, 6, 1)),
        SimpleNamespace(id=5, name="客户戊", birthday=date(2000, 2, 29)),
    ])

    resp = await client.get("/api/v1/calendar/events?start=2026-03-01&end=2026-03-31")
    assert resp.status_code == 200
    events = resp.json()["events"]
    by_id = {e["id"]: e for e in events}
    assert set(by_id) == {"task:11", "task:12", "opp:5", "birthday:3"}

    t = by_id["task:11"]
    assert t["type"] == "task" and t["done"] is True and t["date"] == "2026-03-05"
    assert t["customer_id"] == 1 and t["customer_name"] == "客户甲" and t["url"] == "/tasks"
    assert by_id["task:12"]["done"] is False
    assert by_id["task:12"]["date"] == "2026-03-01"  # 边界包含

    o = by_id["opp:5"]
    assert o["type"] == "opportunity" and o["done"] is False
    assert o["date"] == "2026-03-10" and o["url"] == "/customers/1"
    assert "大单" in o["title"]

    b = by_id["birthday:3"]
    assert b["type"] == "birthday" and b["date"] == "2026-03-15"
    assert b["customer_name"] == "客户丙" and b["url"] == "/customers/3"

    # 按日期排序
    assert [e["date"] for e in events] == sorted(e["date"] for e in events)


async def test_events_birthday_spans_years(client):
    """跨年区间：生日在区间内每一年各出现一次。"""
    db = _FakeSession()
    _override_user(db)
    db.queue_execute([])  # 任务
    db.queue_execute([])  # 商机
    db.queue_execute([SimpleNamespace(id=3, name="客户丙", birthday=date(1990, 1, 10))])

    resp = await client.get("/api/v1/calendar/events?start=2025-12-01&end=2027-02-01")
    dates = [e["date"] for e in resp.json()["events"] if e["type"] == "birthday"]
    assert dates == ["2026-01-10", "2027-01-10"]


async def test_events_end_before_start_422(client):
    db = _FakeSession()
    _override_user(db)
    resp = await client.get("/api/v1/calendar/events?start=2026-03-31&end=2026-03-01")
    assert resp.status_code == 422


async def test_events_requires_auth(client):
    resp = await client.get("/api/v1/calendar/events?start=2026-03-01&end=2026-03-31")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# ICS 订阅源
# ---------------------------------------------------------------------------

def _queue_feed_db(db):
    """feed.ics 的 fake session：先 queue_get 用户，再按序 queue_execute 任务/商机/客户。"""
    today = date.today()
    db.queue_get(SimpleNamespace(id=1, tenant_id=1, username="u", name="用户", role="user", status=1))
    db.queue_execute([
        (_task(11, datetime.combine(today + timedelta(days=3), datetime.min.time()).replace(hour=12)), "客户甲"),
    ])
    db.queue_execute([
        (SimpleNamespace(id=5, customer_id=1, name="大单", expected_close_date=today + timedelta(days=10)), "客户甲"),
    ])
    db.queue_execute([
        SimpleNamespace(id=3, name="客户丙", birthday=date(1990, today.month, today.day)),
    ])
    return today


async def test_feed_ics_output(client):
    """有效订阅令牌 → 200 text/calendar：VEVENT 结构、全天事件、生日 RRULE、CRLF 行尾。"""
    db = _FakeSession()
    today = _queue_feed_db(db)

    async def _fake_db():
        yield db

    app.dependency_overrides[get_db] = _fake_db
    token = create_ics_token(1)
    resp = await client.get(f"/api/v1/calendar/feed.ics?token={token}")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/calendar")

    body = resp.text
    assert body.startswith("BEGIN:VCALENDAR\r\n")
    assert body.endswith("END:VCALENDAR\r\n")
    assert "VERSION:2.0" in body
    assert body.count("BEGIN:VEVENT") == 3
    assert "UID:task:11@kbcrm" in body
    assert "UID:opp:5@kbcrm" in body
    assert "UID:birthday:3@kbcrm" in body
    # 全天事件：本地日期，无时间部分
    task_day = (today + timedelta(days=3)).strftime("%Y%m%d")
    assert f"DTSTART;VALUE=DATE:{task_day}" in body
    # 生日每年重复，其余事件不带 RRULE
    assert body.count("RRULE:FREQ=YEARLY") == 1
    # 中文 UTF-8 直出
    assert "SUMMARY:任务11" in body
    assert "客户丙 生日" in body


async def test_feed_rejects_wrong_typ_token(client):
    """登录 JWT / 文件令牌调订阅源 → 403（令牌有效但用途不符）。"""
    db = _FakeSession()

    async def _fake_db():
        yield db

    app.dependency_overrides[get_db] = _fake_db
    for bad in (create_access_token(1, "u"), create_file_token(1, 1)):
        resp = await client.get(f"/api/v1/calendar/feed.ics?token={bad}")
        assert resp.status_code == 403


async def test_feed_rejects_invalid_or_missing_token(client):
    db = _FakeSession()

    async def _fake_db():
        yield db

    app.dependency_overrides[get_db] = _fake_db
    assert (await client.get("/api/v1/calendar/feed.ics")).status_code == 401
    assert (await client.get("/api/v1/calendar/feed.ics?token=not-a-jwt")).status_code == 401


async def test_ics_token_rejected_as_bearer(client):
    """ICS 令牌当 Bearer 调普通业务端点 → 401（与文件令牌同一隔离机制）。"""
    token = create_ics_token(1)

    async def _fake_db():
        yield _FakeSession()

    app.dependency_overrides[get_db] = _fake_db
    resp = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


async def test_feed_token_endpoint(client):
    """feed-token：普通 JWT 鉴权，返回订阅令牌与订阅路径。"""
    db = _FakeSession()
    _override_user(db)
    resp = await client.get("/api/v1/calendar/feed-token")
    assert resp.status_code == 200
    data = resp.json()
    assert data["path"] == f"/api/v1/calendar/feed.ics?token={data['token']}"
    payload = decode_token(data["token"])
    assert payload["typ"] == ICS_TOKEN_TYPE and payload["sub"] == "1"


async def test_feed_token_requires_auth(client):
    resp = await client.get("/api/v1/calendar/feed-token")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 跨租户隔离（aiosqlite 内存库真实 SQL）
# ---------------------------------------------------------------------------

_TABLES = [Tenant.__table__, User.__table__, Customer.__table__, Task.__table__, Opportunity.__table__]


@pytest.fixture
async def real_db():
    """sqlite 内存库：建日历相关表子集；Customer 的 JSONB 列临时替换为通用 JSON。"""
    swapped_types: list[tuple] = []
    for table in _TABLES:
        for col in table.c:
            if isinstance(col.type, JSONB):
                swapped_types.append((col, col.type))
                col.type = JSON()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=_TABLES))
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            yield session
    finally:
        await engine.dispose()
        for col, col_type in swapped_types:
            col.type = col_type


async def test_events_tenant_isolation(real_db):
    """同区间下租户 1 只能看到租户 1 的三类事件；回收站客户的事件不出现。"""
    session = real_db
    session.add_all([
        Tenant(id=1, name="租户一"),
        Tenant(id=2, name="租户二"),
        Customer(id=1, tenant_id=1, name="甲", birthday=date(1990, 3, 15)),
        Customer(id=2, tenant_id=2, name="乙", birthday=date(1990, 3, 16)),
        Customer(id=3, tenant_id=1, name="丙(已删)", birthday=date(1990, 3, 17),
                 deleted_at=datetime(2026, 1, 1, 12, 0)),
        Task(id=1, tenant_id=1, customer_id=1, title="租户1任务",
             due_date=datetime(2026, 3, 5, 12, 0), status="pending"),
        Task(id=2, tenant_id=2, customer_id=2, title="租户2任务",
             due_date=datetime(2026, 3, 6, 12, 0), status="pending"),
        Opportunity(id=1, customer_id=1, name="租户1商机", expected_close_date=date(2026, 3, 10)),
        Opportunity(id=2, customer_id=2, name="租户2商机", expected_close_date=date(2026, 3, 11)),
        # 区间外数据：验证 SQL 区间过滤
        Opportunity(id=3, customer_id=1, name="区间外商机", expected_close_date=date(2026, 5, 1)),
        Task(id=4, tenant_id=1, customer_id=1, title="无到期日任务", due_date=None, status="pending"),
    ])
    await session.commit()

    events = await _collect_events(session, SimpleNamespace(id=1, tenant_id=1, role="admin"), date(2026, 3, 1), date(2026, 3, 31))
    assert {e["id"] for e in events} == {"task:1", "opp:1", "birthday:1"}

    events_t2 = await _collect_events(session, SimpleNamespace(id=2, tenant_id=2, role="admin"), date(2026, 3, 1), date(2026, 3, 31))
    assert {e["id"] for e in events_t2} == {"task:2", "opp:2", "birthday:2"}
