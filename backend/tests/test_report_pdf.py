"""报告 PDF 导出测试：格式/状态/租户约束、503 降级文案。

真实 Chromium 渲染不作为 pytest 用例（CI 无浏览器），单独冒烟验证。
"""
import sys
from datetime import datetime
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import deps
from app.database import get_db
from app.main import app
from app.models.audit_log import AuditLog


class _FakeSession:
    def __init__(self):
        self._get_queue = []
        self.added = []

    def queue_get(self, value):
        self._get_queue.append(value)

    async def get(self, model, ident):
        return self._get_queue.pop(0) if self._get_queue else None

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _override(db, tenant_id=1):
    async def _fake_user():
        return SimpleNamespace(id=1, tenant_id=tenant_id, username="u", name="用户", role="admin", status=1)

    async def _fake_db():
        yield db

    app.dependency_overrides[deps.get_current_user] = _fake_user
    app.dependency_overrides[get_db] = _fake_db


def _report_ns(**kw):
    base = dict(
        id=9, tenant_id=1, user_id=1, type="custom", title="自定义报告…",
        status="ready", content="<html><body><h1>报告</h1></body></html>",
        params={"format": "html"}, format="html", error=None,
        created_at=datetime(2026, 8, 17),
    )
    base.update(kw)
    return SimpleNamespace(**base)


async def test_pdf_rejects_markdown_report(client):
    db = _FakeSession()
    _override(db)
    db.queue_get(_report_ns(type="sales_weekly", format="markdown", params={}))
    resp = await client.get("/api/v1/reports/9/pdf")
    assert resp.status_code == 400
    assert "仅 HTML 报告" in resp.json()["detail"]


async def test_pdf_rejects_not_ready(client):
    db = _FakeSession()
    _override(db)
    db.queue_get(_report_ns(status="generating"))
    resp = await client.get("/api/v1/reports/9/pdf")
    assert resp.status_code == 409


async def test_pdf_cross_tenant_404(client):
    db = _FakeSession()
    _override(db, tenant_id=1)
    db.queue_get(_report_ns(tenant_id=2))
    resp = await client.get("/api/v1/reports/9/pdf")
    assert resp.status_code == 404


async def test_pdf_503_when_playwright_missing(client, monkeypatch):
    db = _FakeSession()
    _override(db)
    db.queue_get(_report_ns())

    def _boom(html):
        raise RuntimeError("PDF 导出未安装：pip install playwright && python -m playwright install chromium")

    monkeypatch.setattr("app.api.reports.render_pdf_from_html", _boom)
    resp = await client.get("/api/v1/reports/9/pdf")
    assert resp.status_code == 503
    assert "playwright" in resp.json()["detail"]


async def test_pdf_success(client, monkeypatch):
    db = _FakeSession()
    _override(db)
    db.queue_get(_report_ns())

    monkeypatch.setattr(
        "app.api.reports.render_pdf_from_html", lambda html: b"%PDF-1.4 fake-bytes"
    )
    resp = await client.get("/api/v1/reports/9/pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content == b"%PDF-1.4 fake-bytes"
    disposition = resp.headers["content-disposition"]
    assert disposition.startswith("attachment; filename*=UTF-8''")
    assert quote("自定义报告…") in disposition
    assert disposition.endswith(".pdf")
    assert any(
        isinstance(a, AuditLog) and a.action == "export" and a.resource_type == "report"
        for a in db.added
    )


# ---------------------------------------------------------------------------
# render_pdf_from_html 降级分支（不触发真实浏览器）
# ---------------------------------------------------------------------------

def test_render_raises_install_hint_without_playwright(monkeypatch):
    from app.services.pdf import INSTALL_HINT, render_pdf_from_html

    # sys.modules 中置 None 会让 import playwright 抛 ImportError
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    with pytest.raises(RuntimeError, match="pip install playwright"):
        render_pdf_from_html("<html></html>")
    assert "install chromium" in INSTALL_HINT


def test_render_raises_install_hint_when_browser_missing(monkeypatch):
    from app.services.pdf import render_pdf_from_html

    class _RaisingCtx:
        def __enter__(self):
            raise Exception("Executable doesn't exist ... chromium")

        def __exit__(self, *args):
            return False

    import playwright.sync_api

    monkeypatch.setattr(playwright.sync_api, "sync_playwright", lambda: _RaisingCtx())
    with pytest.raises(RuntimeError, match="install chromium"):
        render_pdf_from_html("<html></html>")
