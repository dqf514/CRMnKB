"""可观测性：request-id 中间件 + /metrics 端点 + 热查询复合索引迁移。

不连真实数据库：/health 与 /metrics 的 DB ping 由 autouse fixture 打桩
（它们函数内 from app.database import AsyncSessionLocal，打模块属性即可生效）。
"""
import inspect
import logging

import pytest
from httpx import ASGITransport, AsyncClient

import app.database as database_module
import app.main as main_module
from app.core import observability
from app.core.observability import RequestIdFilter, request_id_var
from app.main import app


class _PingOkSession:
    """DB ping 打桩：execute 直接成功（/health 回 200、/metrics db_up=1）。"""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, stmt):
        return None


@pytest.fixture(autouse=True)
def _stub_db_ping(monkeypatch):
    """/health 与 /metrics 的 SELECT 1 ping 不触真实库。

    /health 在函数内 from app.database import AsyncSessionLocal（打 app.database 属性生效）；
    /metrics 用的是 main.py 模块级 from-import 绑定（必须打 app.main 属性），两处都打。
    """
    monkeypatch.setattr(database_module, "AsyncSessionLocal", lambda: _PingOkSession())
    monkeypatch.setattr(main_module, "AsyncSessionLocal", lambda: _PingOkSession())


@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# request-id 中间件
# ---------------------------------------------------------------------------

async def test_request_id_generated_and_returned(client):
    """未带 X-Request-ID 时自动生成，并回写响应头。"""
    resp = await client.get("/health")  # DB ping 已打桩，只验证响应头
    assert "x-request-id" in resp.headers
    assert resp.headers["x-request-id"]


async def test_request_id_passthrough(client):
    """请求带 X-Request-ID 时透传，响应头回写同一值。"""
    resp = await client.get("/health", headers={"X-Request-ID": "req-test-123"})
    assert resp.headers["x-request-id"] == "req-test-123"


async def test_request_id_on_error_response(client):
    """错误响应（404）同样回写 request-id。"""
    resp = await client.get("/api/v1/no-such-route")
    assert resp.status_code == 404
    assert "x-request-id" in resp.headers


# ---------------------------------------------------------------------------
# 日志 request_id 过滤器
# ---------------------------------------------------------------------------

def test_request_id_filter_injects_attribute():
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "msg", (), None)
    RequestIdFilter().filter(record)
    assert record.request_id == "-"  # 无请求上下文时的兜底值
    token = request_id_var.set("rid-1")
    try:
        RequestIdFilter().filter(record)
        assert record.request_id == "rid-1"
    finally:
        request_id_var.reset(token)


# ---------------------------------------------------------------------------
# /metrics 端点与指标采集
# ---------------------------------------------------------------------------

async def test_metrics_endpoint(client):
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text
    # HELP/TYPE 行始终渲染（即使尚无样本）
    assert "# TYPE http_requests_total counter" in body
    assert "# TYPE http_request_duration_seconds histogram" in body
    assert "# TYPE process_uptime_seconds gauge" in body
    assert "process_uptime_seconds" in body
    assert "db_up" in body


async def test_metrics_records_requests(client):
    """先打一个请求，再抓 /metrics：应有带路径标签的计数与直方图样本。"""
    await client.get("/health")
    resp = await client.get("/metrics")
    body = resp.text
    assert 'http_requests_total{method="GET",path="/health"' in body
    assert 'http_request_duration_seconds_bucket{method="GET",path="/health"' in body
    assert 'le="+Inf"' in body
    assert 'http_request_duration_seconds_count{method="GET",path="/health"' in body


def test_normalize_path_collapses_numeric_ids():
    """路径标签归一化：纯数字段折叠为 {id}，防高基数。"""
    assert (
        observability._normalize_path("/api/v1/customers/12/followups")
        == "/api/v1/customers/{id}/followups"
    )
    assert observability._normalize_path("/api/v1/auth/me") == "/api/v1/auth/me"


def test_render_metrics_escapes_labels():
    observability.record_request("GET", "/health", 200, 0.01)
    body = observability.render_metrics(db_up=True)
    assert "db_up 1" in body
    body = observability.render_metrics(db_up=False)
    assert "db_up 0" in body


# ---------------------------------------------------------------------------
# 热查询复合索引：模型声明 + init_db 幂等迁移语句
# ---------------------------------------------------------------------------

def test_hot_query_indexes_declared_on_models():
    from app.models.agent_approval import AgentApproval
    from app.models.chat_session import ChatSession
    from app.models.report import Report

    assert "ix_chat_sessions_user_updated" in {ix.name for ix in ChatSession.__table__.indexes}
    assert "ix_reports_tenant_created" in {ix.name for ix in Report.__table__.indexes}
    assert "ix_agent_approvals_tenant_status" in {ix.name for ix in AgentApproval.__table__.indexes}


def test_init_db_contains_idempotent_index_statements():
    """存量库升级走 init_db 幂等 CREATE INDEX（新库由 create_all 覆盖）。"""
    source = inspect.getsource(database_module.init_db)
    for name in (
        "ix_chat_sessions_user_updated",
        "ix_reports_tenant_created",
        "ix_agent_approvals_tenant_status",
    ):
        assert f"CREATE INDEX IF NOT EXISTS {name}" in source
