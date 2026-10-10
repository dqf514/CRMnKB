"""随手记提醒意图识别测试（services/capture_reminder.py）。

- 门控正则与 LLM 应答解析为纯函数，直接测
- maybe_create_capture_reminder 用 aiosqlite 内存库真实建 Task/ReminderRule，
  LLM 经 monkeypatch 打桩（模块级 import 的 resolve_chat_llm）
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import JSON, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.reminder_rule import ReminderRule
from app.models.task import Task
from app.models.tenant import Tenant
from app.models.user import User
from app.services import capture_reminder as cr

_TABLES = [Tenant.__table__, User.__table__, Task.__table__, ReminderRule.__table__]


@pytest.fixture
async def db():
    swapped: list[tuple] = []
    saved_indexes: dict = {}
    for table in _TABLES:
        for col in table.c:
            if isinstance(col.type, JSONB):
                swapped.append((col, col.type))
                col.type = JSON()
        saved_indexes[table] = set(table.indexes)
        table.indexes.clear()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=_TABLES))
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            session.add(Tenant(id=1, name="租户一"))
            session.add(User(id=1, tenant_id=1, username="alice", password_hash="x", name="甲", role="member"))
            await session.commit()
            yield session
    finally:
        await engine.dispose()
        for col, col_type in swapped:
            col.type = col_type
        for table, indexes in saved_indexes.items():
            table.indexes.update(indexes)


class _StubLLM:
    def __init__(self, reply: str | Exception):
        self.reply = reply
        self.calls = 0

    async def chat(self, messages, **kw):
        self.calls += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


# ---------------------------------------------------------------------------
# 门控正则
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "明天记得提醒我给沈巍发合同",
    "下周一上午10点提醒我开项目周会",
    "3月14日要交报告，记得提醒我",
    "提醒我后天下午3点打电话",
    "今晚8点提醒我吃药",
])
def test_gate_positive(text):
    assert cr.has_reminder_intent(text) is True


@pytest.mark.parametrize("text", [
    "今天天气不错",                      # 无时间词+无意向词
    "3月14日的会议纪要已整理",            # 有时间词无意向词
    "记得那个客户说过预算有限",           # 有意向词无时间词（"记得"但无时间）
    "随手记一段想法",
])
def test_gate_negative(text):
    assert cr.has_reminder_intent(text) is False


# ---------------------------------------------------------------------------
# LLM 应答解析
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 10, 9, 10, 0, tzinfo=timezone(timedelta(hours=8)))


def test_parse_valid_response():
    raw = '好的，分析结果是：{"has_reminder": true, "remind_at": "2026-10-10 15:00", "title": "给沈巍发合同"}'
    parsed = cr.parse_extract_response(raw, _NOW)
    assert parsed is not None
    assert parsed["title"] == "给沈巍发合同"
    assert parsed["remind_at"] == datetime(2026, 10, 10, 15, 0, tzinfo=_NOW.tzinfo)


def test_parse_no_reminder():
    assert cr.parse_extract_response('{"has_reminder": false}', _NOW) is None


def test_parse_past_time_rejected():
    raw = '{"has_reminder": true, "remind_at": "2026-10-01 09:00", "title": "过期事项"}'
    assert cr.parse_extract_response(raw, _NOW) is None


def test_parse_garbage():
    assert cr.parse_extract_response("完全不是 JSON", _NOW) is None
    assert cr.parse_extract_response('{"has_reminder": true, "remind_at": "下周吧", "title": "x"}', _NOW) is None
    assert cr.parse_extract_response("", _NOW) is None


def test_parse_date_only_defaults():
    parsed = cr.parse_extract_response(
        '{"has_reminder": true, "remind_at": "2026-12-01", "title": "月初复盘"}', _NOW
    )
    assert parsed is not None and parsed["remind_at"].day == 1


# ---------------------------------------------------------------------------
# 服务层（真实建任务 + 规则补种）
# ---------------------------------------------------------------------------

def _tomorrow_15() -> str:
    tomorrow = datetime.now(timezone.utc).astimezone() + timedelta(days=1)
    return f"{tomorrow:%Y-%m-%d} 15:00"


async def test_creates_task_and_default_rule(db, monkeypatch):
    remind_at = _tomorrow_15()
    stub = _StubLLM(f'{{"has_reminder": true, "remind_at": "{remind_at}", "title": "给沈巍发合同"}}')
    monkeypatch.setattr(cr, "resolve_chat_llm", lambda **kw: _async_ret(stub))

    user = await db.get(User, 1)
    result = await cr.maybe_create_capture_reminder(db, user, "明天下午3点记得提醒我给沈巍发合同", customer_id=None)
    await db.commit()

    assert result is not None
    assert result["title"] == "给沈巍发合同"
    task = await db.get(Task, result["task_id"])
    assert task.type == "todo" and task.source == "capture" and task.status == "pending"
    # due_date 落库为 naive UTC = 本地 15:00 减 8 小时（本机非 +8 时区时按实际换算断言）
    assert task.due_date is not None
    # 幂等补种默认 task_due_soon 规则
    rule = (await db.execute(select(ReminderRule).where(ReminderRule.tenant_id == 1))).scalar_one()
    assert rule.trigger_type == "task_due_soon" and rule.enabled is True
    # 再次创建不重复补种
    result2 = await cr.maybe_create_capture_reminder(db, user, "后天上午提醒我复盘")
    await db.commit()
    assert result2 is not None
    rules = (await db.execute(select(ReminderRule).where(ReminderRule.tenant_id == 1))).scalars().all()
    assert len(rules) == 1


async def _async_ret(value):
    return value


async def test_no_intent_skips_llm(db, monkeypatch):
    stub = _StubLLM('{"has_reminder": true, "remind_at": "2099-01-01 09:00", "title": "x"}')
    monkeypatch.setattr(cr, "resolve_chat_llm", lambda **kw: _async_ret(stub))
    user = await db.get(User, 1)
    assert await cr.maybe_create_capture_reminder(db, user, "今天天气不错") is None
    assert stub.calls == 0  # 门控未命中，不打扰 LLM


async def test_llm_says_no_reminder(db, monkeypatch):
    stub = _StubLLM('{"has_reminder": false}')
    monkeypatch.setattr(cr, "resolve_chat_llm", lambda **kw: _async_ret(stub))
    user = await db.get(User, 1)
    assert await cr.maybe_create_capture_reminder(db, user, "明天记得提醒我拿快递") is None
    assert (await db.execute(select(Task))).scalars().all() == []


async def test_llm_failure_silent(db, monkeypatch):
    stub = _StubLLM(RuntimeError("LLM 不可用"))
    monkeypatch.setattr(cr, "resolve_chat_llm", lambda **kw: _async_ret(stub))
    user = await db.get(User, 1)
    # 异常被吞掉返回 None，不抛给主流程
    assert await cr.maybe_create_capture_reminder(db, user, "明天记得提醒我拿快递") is None
