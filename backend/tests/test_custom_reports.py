"""自定义报告（AI 工作台）测试：workbench 资料库、custom 生成、对话式修改。

API 层用 httpx ASGITransport + dependency_overrides + 队列式 fake 会话；
service 层直接 fake AsyncSessionLocal 与 LLM/检索函数，不触网不触库。
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

import app.services.report as report_svc
from app.api import deps
from app.database import get_db
from app.main import app
from app.models.audit_log import AuditLog
from app.services.rag import EmbeddingUnavailable


class _FakeResult:
    def __init__(self, value=None):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._value if self._value is not None else []

    def mappings(self):
        return self


class _FakeSession:
    def __init__(self):
        self._execute_queue = []
        self._get_queue = []
        self.added = []

    def queue_execute(self, value):
        self._execute_queue.append(value)

    def queue_get(self, value):
        self._get_queue.append(value)

    async def execute(self, stmt, params=None):
        return _FakeResult(self._execute_queue.pop(0) if self._execute_queue else None)

    async def get(self, model, ident):
        return self._get_queue.pop(0) if self._get_queue else None

    def add(self, obj):
        self.added.append(obj)

    async def _ensure_ids(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = 100

    async def flush(self):
        await self._ensure_ids()

    async def commit(self):
        await self._ensure_ids()

    async def refresh(self, obj):
        if getattr(obj, "created_at", None) is None:
            obj.created_at = datetime(2026, 8, 17)

    async def delete(self, obj):
        pass


class _FakeSessionCtx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *args):
        return False


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _override(db, tenant_id=1, user_id=1):
    async def _fake_user():
        return SimpleNamespace(id=user_id, tenant_id=tenant_id, username="u", name="用户", role="admin", status=1)

    async def _fake_db():
        yield db

    app.dependency_overrides[deps.get_current_user] = _fake_user
    app.dependency_overrides[get_db] = _fake_db


def _report_ns(**kw):
    base = dict(
        id=9, tenant_id=1, user_id=1, type="custom", title="自定义报告…",
        status="ready", content="<html><body>v1</body></html>",
        params={"prompt": "月度销售分析", "kb_ids": [], "file_ids": [], "format": "html"},
        format="html", revisions=[], error=None, created_at=datetime(2026, 8, 17),
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# workbench-kb
# ---------------------------------------------------------------------------

async def test_workbench_kb_auto_create_idempotent(client):
    db = _FakeSession()
    _override(db)
    db.queue_execute(None)  # 第一次：不存在 → 创建
    resp1 = await client.get("/api/v1/reports/workbench-kb")
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["type"] == "workbench"
    assert data1["name"] == "AI工作台资料库"

    existing = SimpleNamespace(
        id=data1["id"], tenant_id=1, name="AI工作台资料库",
        description="AI 工作台对话附件与资料", type="workbench",
        customer_id=None, deleted_at=None, created_at=datetime(2026, 8, 17),
    )
    db.queue_execute(existing)  # 第二次：已存在 → 直接返回
    resp2 = await client.get("/api/v1/reports/workbench-kb")
    assert resp2.status_code == 200
    assert resp2.json()["id"] == data1["id"]
    assert len(db.added) == 1  # 只创建过一次


async def test_workbench_kb_probe_without_create(client):
    """create=false：不存在时返回 null 且不创建（前端打开对话框仅探测用）。"""
    db = _FakeSession()
    _override(db)
    db.queue_execute(None)
    resp = await client.get("/api/v1/reports/workbench-kb?create=false")
    assert resp.status_code == 200
    assert resp.json() is None
    assert db.added == []


# ---------------------------------------------------------------------------
# custom 生成：参数校验
# ---------------------------------------------------------------------------

async def test_generate_custom_missing_prompt(client):
    db = _FakeSession()
    _override(db)
    resp = await client.post("/api/v1/reports/generate", json={"type": "custom"})
    assert resp.status_code == 400


async def test_generate_custom_prompt_too_long(client):
    db = _FakeSession()
    _override(db)
    resp = await client.post(
        "/api/v1/reports/generate", json={"type": "custom", "prompt": "长" * 2001}
    )
    assert resp.status_code == 422


async def test_generate_custom_kb_forbidden(client):
    db = _FakeSession()
    _override(db)
    db.queue_execute([])  # kb 归属查询返回空 → 越权
    resp = await client.post(
        "/api/v1/reports/generate",
        json={"type": "custom", "prompt": "分析", "kb_ids": [999]},
    )
    assert resp.status_code == 400


async def test_generate_custom_file_forbidden(client):
    db = _FakeSession()
    _override(db)
    db.queue_execute([3])  # kb 校验通过
    db.queue_execute([])  # file 校验返回空 → 越权
    resp = await client.post(
        "/api/v1/reports/generate",
        json={"type": "custom", "prompt": "分析", "kb_ids": [3], "file_ids": [888]},
    )
    assert resp.status_code == 400


async def test_generate_custom_success(client, monkeypatch):
    db = _FakeSession()
    _override(db)
    db.queue_execute([3])  # kb 校验
    db.queue_execute([5])  # file 校验

    async def _noop_generate(report_id):
        pass

    monkeypatch.setattr("app.api.reports.generate_report", _noop_generate)
    prompt = "生成一份关于新能源汽车行业的月度销售分析报告"
    resp = await client.post(
        "/api/v1/reports/generate",
        json={"type": "custom", "prompt": prompt, "kb_ids": [3], "file_ids": [5]},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["type"] == "custom"
    assert data["status"] == "generating"
    assert data["format"] == "html"
    assert data["title"] == prompt[:30] + "…"
    assert data["params"]["prompt"] == prompt
    assert data["params"]["kb_ids"] == [3]
    assert data["params"]["file_ids"] == [5]
    assert any(
        isinstance(a, AuditLog) and a.action == "create" and a.resource_type == "report"
        for a in db.added
    )


# ---------------------------------------------------------------------------
# service 层：generate_report custom 分支
# ---------------------------------------------------------------------------

async def _run_service(report, monkeypatch, chat_text=None, aggregate=None, chat_exc=None):
    session = _FakeSession()
    session.queue_get(report)
    monkeypatch.setattr(report_svc, "AsyncSessionLocal", lambda: _FakeSessionCtx(session))

    async def _noop_log(*a, **kw):
        pass

    monkeypatch.setattr("app.services.error_log.log_error", _noop_log)

    if aggregate is not None:
        async def _aggregate(db, tenant_id, prompt, kb_ids=None, file_ids=None):
            return aggregate

        monkeypatch.setattr(report_svc, "aggregate_custom", _aggregate)

    class _FakeChat:
        async def chat(self, messages, **kw):
            if chat_exc is not None:
                raise chat_exc
            return chat_text

        async def chat_stream(self, messages, **kw):
            if chat_exc is not None:
                raise chat_exc
            yield chat_text

    async def _resolve_chat(**kw):
        return _FakeChat()

    monkeypatch.setattr(report_svc, "resolve_chat_llm", _resolve_chat)
    return session


async def test_generate_report_custom_success(monkeypatch):
    report = SimpleNamespace(
        id=1, tenant_id=1, user_id=1, type="custom",
        params={"prompt": "月度销售分析", "kb_ids": [], "format": "html"},
        status="generating", content=None, error=None,
    )
    sources = [{"chunk_id": 1, "doc_id": 2, "doc_title": "资料", "score": 0.9,
                "excerpt": "摘录", "file_id": 5, "file_name": "f.pdf",
                "file_type": "pdf", "file_size": 100}]
    session = await _run_service(
        report, monkeypatch,
        chat_text="```html\n<html><body><h1>报告</h1></body></html>\n```",
        aggregate={"context": "资料内容", "sources": sources},
    )

    await report_svc.generate_report(1)

    assert report.status == "ready"
    assert report.content == "<html><body><h1>报告</h1></body></html>"  # 围栏已剥离
    assert report.params["sources"] == sources
    assert report.params["prompt"] == "月度销售分析"  # 原 params 保留


async def test_generate_report_custom_embed_unavailable(monkeypatch):
    report = SimpleNamespace(
        id=2, tenant_id=1, user_id=1, type="custom",
        params={"prompt": "分析", "kb_ids": [], "format": "html"},
        status="generating", content=None, error=None,
    )

    async def _agg_fail(db, tenant_id, prompt, kb_ids=None, file_ids=None):
        raise EmbeddingUnavailable()

    monkeypatch.setattr(report_svc, "aggregate_custom", _agg_fail)
    await _run_service(report, monkeypatch, chat_text="x")

    await report_svc.generate_report(2)  # 不抛出

    assert report.status == "failed"
    assert report.error


# ---------------------------------------------------------------------------
# service 层：aggregate_custom 检索
# ---------------------------------------------------------------------------

async def test_aggregate_custom_merges_sources(monkeypatch):
    db = _FakeSession()
    hit = {"chunk_id": 1, "doc_id": 2, "chunk_index": 0,
           "content": "新能源销量数据", "doc_title": "行业资料", "score": 0.9}

    class _FakeEmbed:
        async def embed(self, texts):
            return [[0.1, 0.2]]

    async def _resolve_embed(**kw):
        return _FakeEmbed()

    async def _vec(db_, tid, vec, limit, kb_ids=None, file_ids=None):
        return [hit]

    async def _kw(db_, tid, q, limit, kb_ids=None, file_ids=None):
        return [hit]

    monkeypatch.setattr(report_svc, "resolve_embed_llm", _resolve_embed)
    monkeypatch.setattr(report_svc, "search_chunks_vector", _vec)
    monkeypatch.setattr(report_svc, "search_chunks_keyword", _kw)
    # expand_contexts 的批量查询
    db.queue_execute([SimpleNamespace(document_id=2, chunk_index=0, content="新能源销量数据")])
    # attach_file_info 的文档查询（file_id 为 NULL → 不再查文件表）
    db.queue_execute([SimpleNamespace(id=2, file_id=None, file_name="行业资料.pdf", file_type="pdf")])

    result = await report_svc.aggregate_custom(db, 1, "新能源销量")

    assert "行业资料" in result["context"]
    assert "新能源销量数据" in result["context"]
    assert result["sources"][0]["doc_title"] == "行业资料"
    assert result["sources"][0]["file_id"] is None


# ---------------------------------------------------------------------------
# 对话式修改
# ---------------------------------------------------------------------------

async def test_revise_report_flow(monkeypatch):
    report = SimpleNamespace(
        id=3, tenant_id=1, user_id=1, type="custom", status="revising",
        content="<html><body>v1</body></html>",
        params={"prompt": "分析", "format": "html"}, error=None,
    )
    await _run_service(report, monkeypatch, chat_text="<html><body>v2</body></html>")

    await report_svc.revise_report(3, "把标题改成红色")

    assert report.status == "ready"
    assert report.content == "<html><body>v2</body></html>"
    revisions = report.params["revisions"]
    assert len(revisions) == 1
    assert revisions[0]["instruction"] == "把标题改成红色"
    assert revisions[0]["content"] == "<html><body>v1</body></html>"  # 旧版本入历史
    assert revisions[0]["created_at"]


async def test_revise_report_revisions_cap(monkeypatch):
    old_revisions = [
        {"instruction": f"改{i}", "content": f"<html>v{i}</html>", "created_at": "2026-08-01"}
        for i in range(20)
    ]
    report = SimpleNamespace(
        id=4, tenant_id=1, user_id=1, type="custom", status="revising",
        content="<html>v20</html>",
        params={"prompt": "分析", "format": "html", "revisions": old_revisions},
        error=None,
    )
    await _run_service(report, monkeypatch, chat_text="<html>v21</html>")

    await report_svc.revise_report(4, "再改一次")

    revisions = report.params["revisions"]
    assert len(revisions) == 20  # 超出 cap 丢最旧
    assert revisions[0]["instruction"] == "改1"
    assert revisions[-1]["instruction"] == "再改一次"


async def test_revise_report_failure_keeps_old(monkeypatch):
    report = SimpleNamespace(
        id=5, tenant_id=1, user_id=1, type="custom", status="revising",
        content="<html>v1</html>", params={"prompt": "分析", "format": "html"}, error=None,
    )
    await _run_service(report, monkeypatch, chat_exc=RuntimeError("LLM 超时"))

    await report_svc.revise_report(5, "改")  # 不抛出

    assert report.status == "ready"  # 回 ready 保留旧版可读
    assert report.content == "<html>v1</html>"
    assert "LLM 超时" in report.error
    assert "revisions" not in report.params  # 失败不产生历史版本


# ---------------------------------------------------------------------------
# revise API：类型/状态/租户约束
# ---------------------------------------------------------------------------

async def test_revise_rejects_non_custom(client, monkeypatch):
    db = _FakeSession()
    _override(db)
    db.queue_get(_report_ns(type="sales_weekly", format="markdown", params={}))

    async def _noop(report_id, instruction):
        pass

    monkeypatch.setattr("app.api.reports.revise_report", _noop)
    resp = await client.post("/api/v1/reports/9/revise", json={"instruction": "改"})
    assert resp.status_code == 400


async def test_revise_rejects_not_ready(client, monkeypatch):
    db = _FakeSession()
    _override(db)
    db.queue_get(_report_ns(status="generating"))

    async def _noop(report_id, instruction):
        pass

    monkeypatch.setattr("app.api.reports.revise_report", _noop)
    resp = await client.post("/api/v1/reports/9/revise", json={"instruction": "改"})
    assert resp.status_code == 409


async def test_revise_cross_tenant_404(client, monkeypatch):
    db = _FakeSession()
    _override(db, tenant_id=1)
    db.queue_get(_report_ns(tenant_id=2))  # 其他租户的报告

    async def _noop(report_id, instruction):
        pass

    monkeypatch.setattr("app.api.reports.revise_report", _noop)
    resp = await client.post("/api/v1/reports/9/revise", json={"instruction": "改"})
    assert resp.status_code == 404


async def test_revise_success_sets_revising(client, monkeypatch):
    db = _FakeSession()
    _override(db)
    db.queue_get(_report_ns())

    async def _noop(report_id, instruction):
        pass

    monkeypatch.setattr("app.api.reports.revise_report", _noop)
    resp = await client.post("/api/v1/reports/9/revise", json={"instruction": "把标题改大"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "revising"
    assert data["format"] == "html"
    assert any(
        isinstance(a, AuditLog) and a.action == "update" and a.resource_type == "report"
        for a in db.added
    )


# ---------------------------------------------------------------------------
# 推理块剥离：<think>…</think> 不应污染报告正文/HTML
# ---------------------------------------------------------------------------

def test_strip_think_removes_reasoning_block():
    from app.services.report import _strip_think

    raw = "<think>\n用户只是问候，应友好回应。\n</think>\n\n<html><body><h1>报告</h1></body></html>"
    out = _strip_think(raw)
    assert "<think>" not in out
    assert out.startswith("<html>")
    assert out == "<html><body><h1>报告</h1></body></html>"


def test_strip_think_variant_tag_and_multiline():
    from app.services.report import _strip_think

    raw = "<thinking>第一行\n第二行\n</thinking>\n正文"
    assert _strip_think(raw) == "正文"


def test_strip_think_noop_when_absent():
    from app.services.report import _strip_think

    assert _strip_think("正文内容") == "正文内容"
    assert _strip_think("") == ""


# ========== Agent 模式任务指令（纯函数） ==========


def test_agent_task_prompt_custom_includes_tools_and_scope():
    text = report_svc.build_agent_task_prompt(
        "custom",
        prompt="基于产品资料写竞品分析",
        kb_names=["产品库", "竞品库"],
        extra_context="【附件】\n某文件全文",
        language="zh",
    )
    assert "kb_search" in text and "kb_read_doc" in text  # 告知可用检索工具
    assert "产品库、竞品库" in text  # 知识库范围提示
    assert "某文件全文" in text  # 指定文件全文内联
    assert "报告需求：基于产品资料写竞品分析" in text
    assert "只输出报告正文" in text


def test_agent_task_prompt_data_report_inlines_aggregated_data():
    text = report_svc.build_agent_task_prompt(
        "customer_analysis",
        data={"客户资料": {"姓名": "张三"}, "商机列表": ["商机A（金额 100）"]},
        language="zh",
    )
    assert "客户分析报告" in text
    assert "张三" in text and "商机A" in text  # 聚合数据直接内联，不要求检索
    assert "kb_search" not in text


def test_looks_degenerate():
    assert report_svc.looks_degenerate("") is True
    assert report_svc.looks_degenerate("短") is True
    assert report_svc.looks_degenerate("!" * 251) is True  # 实测出现过的刷屏退化
    assert report_svc.looks_degenerate("# 报告\n" + "正常正文内容。" * 20) is False


def test_agent_task_prompt_custom_asks_markdown_not_html():
    # 长上下文后直接吐完整 HTML 容易触发模型退化：agent 只产 Markdown，HTML 排版走单独调用
    text = report_svc.build_agent_task_prompt("custom", prompt="写竞品分析", language="zh")
    assert "Markdown" in text
    assert "独立的 HTML" not in text


def test_html_from_markdown_prompt():
    msgs = report_svc.build_html_from_markdown_prompt("# 标题\n正文", "zh")
    assert msgs[0]["role"] == "system" and "HTML" in msgs[0]["content"]
    assert "# 标题" in msgs[1]["content"]
