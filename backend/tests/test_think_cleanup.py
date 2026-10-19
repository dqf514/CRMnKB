"""<think> 泄漏修复测试：共享 strip_think、占位任务过滤、存量数据幂等清理。

- strip_think / parse_todos 为纯函数，直接测
- _cleanup_think_artifacts 用 aiosqlite 内存库真实 SQL（建表子集 + JSONB→JSON swap，
  手法同 test_permissions_acl_real.py）
"""
import pytest
from sqlalchemy import JSON, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import _cleanup_think_artifacts
from app.models.base import Base
from app.models.customer import Customer
from app.models.follow_up import FollowUpRecord
from app.models.task import Task
from app.models.tenant import Tenant
from app.models.user import User
from app.services.ai_tasks import parse_todos
from app.services.llm import strip_think

# ---------------------------------------------------------------------------
# strip_think 纯函数
# ---------------------------------------------------------------------------

def test_strip_think_block():
    assert strip_think("<think>想了很多</think>正式答案") == "正式答案"
    assert strip_think("<thinking>reasoning</thinking>结果") == "结果"
    assert strip_think("前缀<think>中间</think>后缀") == "前缀后缀"


def test_strip_think_unclosed_tail():
    """输出截断导致 <think> 未闭合：从标签起全剥。"""
    assert strip_think("答案开始<think>没写完的思考") == "答案开始"


def test_strip_think_edge_cases():
    assert strip_think("") == ""
    assert strip_think(None) == ""
    assert strip_think("  没有思考块  ") == "没有思考块"


# ---------------------------------------------------------------------------
# parse_todos 占位标题过滤
# ---------------------------------------------------------------------------

def test_parse_todos_filters_placeholder():
    raw = '[{"title": "待办标题", "due_date": "2026-10-20"}, {"title": "回访张总"}]'
    todos = parse_todos(raw)
    assert len(todos) == 1 and todos[0]["title"] == "回访张总"


# ---------------------------------------------------------------------------
# init_db 存量清理
# ---------------------------------------------------------------------------

_TABLES = [
    Tenant.__table__,
    User.__table__,
    Customer.__table__,
    FollowUpRecord.__table__,
    Task.__table__,
]


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
            session.add(Customer(id=1, tenant_id=1, name="测试客户", owner_id=1))
            await session.commit()
            yield session
    finally:
        await engine.dispose()
        for col, col_type in swapped:
            col.type = col_type
        for table, indexes in saved_indexes.items():
            table.indexes.update(indexes)


async def test_cleanup_strips_think_from_stored_texts(db):
    customer = await db.get(Customer, 1)
    customer.ai_brief = "<think>推理过程</think>## 当前阶段判断\n资料不足"
    customer.profile = "<thinking>长思维链</thinking>## 基本情况\n测试客户"
    db.add(FollowUpRecord(
        id=1, customer_id=1, user_id=1, type="call", content="电话沟通",
        ai_summary='<think>The user asks...</think>2026/04/05在LinkedIn发送25条信息',
    ))
    await db.commit()

    await _cleanup_think_artifacts(db)

    db.expire_all()
    customer = await db.get(Customer, 1)
    assert customer.ai_brief == "## 当前阶段判断\n资料不足"
    assert customer.profile == "## 基本情况\n测试客户"
    fup = await db.get(FollowUpRecord, 1)
    assert fup.ai_summary == "2026/04/05在LinkedIn发送25条信息"


async def test_cleanup_sets_null_when_empty_after_strip(db):
    customer = await db.get(Customer, 1)
    customer.ai_brief = "<think>全是思考，正文为空</think>"
    await db.commit()
    await _cleanup_think_artifacts(db)
    db.expire_all()
    assert (await db.get(Customer, 1)).ai_brief is None


async def test_cleanup_deletes_placeholder_tasks(db):
    db.add_all([
        Task(id=1, tenant_id=1, user_id=1, title="待办标题", type="follow_up",
             status="pending", ai_generated=True, source="ai_analysis"),
        Task(id=2, tenant_id=1, user_id=1, title="待办事项", type="follow_up",
             status="pending", ai_generated=True, source="ai_analysis"),
        Task(id=3, tenant_id=1, user_id=1, title="回访张总", type="follow_up",
             status="pending", ai_generated=True, source="ai_analysis"),
    ])
    await db.commit()
    await _cleanup_think_artifacts(db)
    remaining = (await db.execute(select(Task.title))).scalars().all()
    assert remaining == ["回访张总"]


async def test_cleanup_idempotent_on_clean_data(db):
    """干净库跑两遍无变化、不报错。"""
    customer = await db.get(Customer, 1)
    customer.ai_brief = "## 正常简报"
    await db.commit()
    await _cleanup_think_artifacts(db)
    await _cleanup_think_artifacts(db)
    db.expire_all()
    assert (await db.get(Customer, 1)).ai_brief == "## 正常简报"
