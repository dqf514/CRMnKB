"""报告 params（JSONB）持久化回归测试。

历史缺陷：演示版 HTML 落库时对 report.params 做「就地修改」（dict 子键赋值），
SQLAlchemy 对普通 JSON/JSONB 列感知不到就地变更，commit 静默丢失——
报告看似生成成功，演示版却永远打不开。

两层护栏：
1. 行为层：sqlite 内存库跑 generate_report 真实流程（LLM/聚合打桩），
   完成后【换一个全新会话】重新读库，断言 presentation_html 真的持久化
   （就地修改在这个测试下会丢 key，直接 fail）；
2. 静态层：AST 扫描 app/services/report.py，禁止对 report.params 做任何
   子键赋值 / update / setdefault / pop 等就地修改（必须整体重赋值）。
"""
import ast
from pathlib import Path

import pytest
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.services.report as report_svc
from app.models.base import Base
from app.models.notification import Notification
from app.models.report import Report
from app.models.user import User

# users：custom 分支会 session.get(User, report.user_id) 取属主
_TABLES = [Report.__table__, Notification.__table__, User.__table__]


@pytest.fixture
async def sqlite_session_factory():
    """reports + notifications 表的 sqlite 内存库；JSONB 临时换成通用 JSON。"""
    swapped: list[tuple] = []
    for table in _TABLES:
        for col in table.c:
            if isinstance(col.type, JSONB):
                swapped.append((col, col.type))
                col.type = JSON()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=_TABLES))
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        for col, col_type in swapped:
            col.type = col_type


class _FakeChat:
    """固定输出的 chat LLM：流式正文 + 一次性演示版 HTML。"""

    def __init__(self, body="# 报告正文", presentation="<html><body>演示版</body></html>"):
        self._body = body
        self._presentation = presentation

    async def chat_stream(self, messages, **kw):
        yield self._body

    async def chat(self, messages, **kw):
        return self._presentation


async def test_generate_report_persists_presentation_html(sqlite_session_factory, monkeypatch):
    """generate_report 落库后，换会话重读：params 必须含 presentation_html 与 sources。"""
    # 种子：一份 generating 状态的 custom 报告
    async with sqlite_session_factory() as session:
        session.add(Report(
            id=1, tenant_id=1, user_id=1, type="custom", title="自定义报告",
            status="generating", content=None,
            params={"prompt": "月度销售分析", "format": "html"},
        ))
        await session.commit()

    monkeypatch.setattr(report_svc, "AsyncSessionLocal", sqlite_session_factory)

    async def _aggregate(db, tenant_id, prompt, kb_ids=None, file_ids=None, user=None):
        return {"context": "资料内容", "sources": []}

    monkeypatch.setattr(report_svc, "aggregate_custom", _aggregate)

    async def _resolve_chat(**kw):
        return _FakeChat()

    monkeypatch.setattr(report_svc, "resolve_chat_llm", _resolve_chat)

    await report_svc.generate_report(1)

    # 全新会话重新读库：验证真实持久化（不是内存对象的脏状态）
    async with sqlite_session_factory() as session:
        report = await session.get(Report, 1)
        assert report.status == "ready"
        assert report.params is not None
        # 核心回归断言：演示版 HTML 键必须真的写进库
        assert report.params["presentation_html"] == "<html><body>演示版</body></html>"
        assert report.params["sources"] == []
        assert report.params["prompt"] == "月度销售分析"  # 原 params 键保留


async def test_presentation_failure_still_persists_empty_marker(sqlite_session_factory, monkeypatch):
    """演示版生成失败也要把 presentation_html="" 落库（前端据此隐藏演示按钮）。"""
    async with sqlite_session_factory() as session:
        session.add(Report(
            id=1, tenant_id=1, user_id=1, type="custom", title="自定义报告",
            status="generating", content=None,
            params={"prompt": "p", "format": "html"},
        ))
        await session.commit()

    monkeypatch.setattr(report_svc, "AsyncSessionLocal", sqlite_session_factory)

    async def _aggregate(db, tenant_id, prompt, kb_ids=None, file_ids=None, user=None):
        return {"context": "资料", "sources": []}

    monkeypatch.setattr(report_svc, "aggregate_custom", _aggregate)

    class _HalfFailChat(_FakeChat):
        async def chat(self, messages, **kw):
            raise RuntimeError("排版模型不可用")

    async def _resolve_chat(**kw):
        return _HalfFailChat()

    monkeypatch.setattr(report_svc, "resolve_chat_llm", _resolve_chat)

    await report_svc.generate_report(1)

    async with sqlite_session_factory() as session:
        report = await session.get(Report, 1)
        assert report.status == "ready"
        assert "presentation_html" in report.params
        assert report.params["presentation_html"] == ""


# ---------------------------------------------------------------------------
# 静态护栏：report.py 中禁止对 report.params 做就地修改
# ---------------------------------------------------------------------------

_INPLACE_METHODS = {"update", "setdefault", "pop", "clear"}


def _is_params_attr(node: ast.AST) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "params"


def test_report_params_never_mutated_in_place():
    """JSONB 列必须整体重赋值：禁止 report.params[...] = ... 与
    report.params.update/setdefault/pop/clear(...) 等就地修改写法。"""
    src = Path(report_svc.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        else:
            targets = []
        for target in targets:
            if isinstance(target, ast.Subscript) and _is_params_attr(target.value):
                offenders.append(f"第 {node.lineno} 行：report.params[...] 子键赋值")
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr in _INPLACE_METHODS
            and _is_params_attr(node.value.func.value)
        ):
            offenders.append(f"第 {node.lineno} 行：report.params.{node.value.func.attr}() 就地修改")
    assert not offenders, (
        "app/services/report.py 存在对 report.params 的就地修改"
        "（JSONB 列 SQLAlchemy 感知不到，commit 会静默丢更新）：\n" + "\n".join(offenders)
    )
