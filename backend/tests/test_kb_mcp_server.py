"""知识库 MCP server 测试：令牌解析/中间件鉴权 + 工具实现的 ACL 过滤。

风格与 test_api_smoke.py 一致：fake session + monkeypatch，不起真实 PG。
"""
from types import SimpleNamespace

import pytest

from app.core.security import create_access_token, create_mcp_token
from app.services import mcp_server
from app.services.mcp_server import (
    BearerMcpAuthMiddleware,
    McpAuthError,
    McpToolError,
    decode_mcp_authorization,
    kb_read_doc_impl,
    kb_search_impl,
)


def _user(**kw):
    base = {"id": 1, "tenant_id": 1, "username": "u1", "role": "member", "status": 1}
    base.update(kw)
    return SimpleNamespace(**base)


def _bearer(token: str) -> list[tuple[bytes, bytes]]:
    return [(b"authorization", f"Bearer {token}".encode())]


# ---------------------------------------------------------------------------
# 令牌解析
# ---------------------------------------------------------------------------


def test_decode_mcp_authorization_missing_header():
    with pytest.raises(McpAuthError):
        decode_mcp_authorization([])


def test_decode_mcp_authorization_garbage_token():
    with pytest.raises(McpAuthError):
        decode_mcp_authorization(_bearer("not-a-jwt"))


def test_decode_mcp_authorization_rejects_login_jwt():
    """普通登录 JWT（无 aud=dsh-mcp）不允许访问 /api/mcp。"""
    with pytest.raises(McpAuthError):
        decode_mcp_authorization(_bearer(create_access_token(1, "u1")))


def test_decode_mcp_authorization_accepts_mcp_token():
    payload = decode_mcp_authorization(_bearer(create_mcp_token(7, "u7", 60)))
    assert payload["sub"] == "7"
    assert payload["aud"] == "dsh-mcp"


async def test_login_side_rejects_mcp_token():
    """反向隔离：dsh MCP 令牌不能当登录令牌访问业务接口（deps._user_from_token）。"""
    from fastapi import HTTPException

    from app.api.deps import _user_from_token

    with pytest.raises(HTTPException) as exc_info:
        await _user_from_token(create_mcp_token(1, "u1", 60), db=None)
    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# ASGI 中间件
# ---------------------------------------------------------------------------


class _RecorderApp:
    def __init__(self):
        self.scope = None

    async def __call__(self, scope, receive, send):
        self.scope = scope
        from starlette.responses import JSONResponse

        await JSONResponse({"ok": True})(scope, receive, send)


async def _call_middleware(app, headers):
    scope = {"type": "http", "method": "POST", "path": "/api/mcp", "headers": headers}
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return sent


async def test_middleware_401_without_token():
    downstream = _RecorderApp()
    app = BearerMcpAuthMiddleware(downstream)
    sent = await _call_middleware(app, [])
    assert downstream.scope is None  # 未放行
    assert sent[0]["status"] == 401


async def test_middleware_passes_payload_into_scope():
    downstream = _RecorderApp()
    app = BearerMcpAuthMiddleware(downstream)
    sent = await _call_middleware(app, _bearer(create_mcp_token(3, "u3", 60)))
    assert sent[0]["status"] == 200
    assert downstream.scope["mcp.token_payload"]["sub"] == "3"


# ---------------------------------------------------------------------------
# kb_search：ACL 过滤 + 结果结构
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def all(self):
        return self._rows


class _FakeSession:
    """最小 fake session：execute 返回预设行，get 返回预设文档。"""

    def __init__(self, rows=(), doc=None):
        self._rows = rows
        self._doc = doc

    async def execute(self, stmt, *args, **kwargs):
        return _FakeResult(self._rows)

    async def get(self, model, ident):
        return self._doc


async def test_kb_search_acl_scopes_to_accessible_kbs(monkeypatch):
    user = _user()
    captured: dict = {}

    async def _accessible_ids(db, u, rtype):
        assert rtype == "kb"
        return [11, 22]  # 该用户只可读这两个 KB

    class _Embed:
        async def embed(self, texts):
            return [[0.1, 0.2]]

    async def _resolve_embed(caller=None, tenant_id=None):
        return _Embed()

    async def _blend(db, tenant_id, vec, question, limit, threshold, kb_ids, file_ids):
        captured["kb_ids"] = kb_ids
        return [
            {
                "chunk_id": 100,
                "doc_id": 5,
                "chunk_index": 3,
                "content": "命中切片原文",
                "doc_title": "退货政策",
                "score": 1.23456,
            }
        ]

    async def _expand(db, tenant_id, hits, window):
        return [
            {
                "doc_id": 5,
                "doc_title": "退货政策",
                "start": 2,
                "end": 4,
                "content": "扩展后的上下文块",
            }
        ]

    async def _attach(db, sources):
        for s in sources:
            s["file_id"] = None

    monkeypatch.setattr(mcp_server, "accessible_ids", _accessible_ids)
    monkeypatch.setattr(mcp_server, "resolve_embed_llm", _resolve_embed)
    monkeypatch.setattr(mcp_server, "search_chunks_blend", _blend)
    monkeypatch.setattr(mcp_server, "expand_contexts", _expand)
    monkeypatch.setattr(mcp_server, "attach_file_info", _attach)

    db = _FakeSession(rows=[SimpleNamespace(id=5, kb_id=11)])
    results = await kb_search_impl(db, user, "退货政策是什么", top_k=5)

    assert captured["kb_ids"] == [11, 22]  # 检索范围被 ACL 收敛
    assert len(results) == 1
    r = results[0]
    assert r["doc_id"] == 5 and r["kb_id"] == 11
    assert r["doc_title"] == "退货政策"
    assert r["content"] == "扩展后的上下文块"  # Small2Big 块优先于切片原文
    assert r["score"] == 1.2346


async def test_kb_search_empty_when_no_accessible_kb(monkeypatch):
    async def _accessible_ids(db, u, rtype):
        return []

    monkeypatch.setattr(mcp_server, "accessible_ids", _accessible_ids)
    results = await kb_search_impl(_FakeSession(), _user(), "任意问题")
    assert results == []


# ---------------------------------------------------------------------------
# kb_read_doc：ACL + 截断
# ---------------------------------------------------------------------------


def _doc(**kw):
    base = {"id": 5, "tenant_id": 1, "kb_id": 7, "title": "员工手册", "status": "ready"}
    base.update(kw)
    return SimpleNamespace(**base)


def _chunks(*contents):
    return [SimpleNamespace(chunk_index=i, content=c) for i, c in enumerate(contents)]


async def test_kb_read_doc_denied_without_permission(monkeypatch):
    async def _get_access(db, tenant_id, user_id, rtype, rid):
        assert (rtype, rid) == ("kb", 7)
        return None  # 无任何权限

    monkeypatch.setattr(mcp_server, "get_access", _get_access)
    with pytest.raises(McpToolError, match="访问权限"):
        await kb_read_doc_impl(_FakeSession(doc=_doc()), _user(), 5)


async def test_kb_read_doc_ok_with_read_permission(monkeypatch):
    async def _get_access(db, tenant_id, user_id, rtype, rid):
        return "read"

    monkeypatch.setattr(mcp_server, "get_access", _get_access)
    db = _FakeSession(doc=_doc(), rows=_chunks("第一段", "第二段", "第三段"))
    result = await kb_read_doc_impl(db, _user(), 5, max_chars=8000)
    assert result["content"] == "第一段\n第二段\n第三段"
    assert result["chunk_count"] == 3
    assert result["truncated"] is False
    assert result["kb_id"] == 7


async def test_kb_read_doc_truncates(monkeypatch):
    async def _get_access(db, tenant_id, user_id, rtype, rid):
        return "read"

    monkeypatch.setattr(mcp_server, "get_access", _get_access)
    db = _FakeSession(doc=_doc(), rows=_chunks("甲" * 500, "乙" * 500))
    result = await kb_read_doc_impl(db, _user(), 5, max_chars=600)
    assert result["truncated"] is True
    assert result["total_chars"] == 1001  # 500 + 换行 + 500
    assert len(result["content"]) == 600


async def test_kb_read_doc_admin_bypasses_acl():
    db = _FakeSession(doc=_doc(), rows=_chunks("内容"))
    result = await kb_read_doc_impl(db, _user(role="admin"), 5)
    assert result["content"] == "内容"


async def test_kb_read_doc_missing_doc():
    with pytest.raises(McpToolError, match="不存在"):
        await kb_read_doc_impl(_FakeSession(doc=None), _user(), 999)
