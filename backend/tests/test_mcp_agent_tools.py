"""dsh 基座阶段 2 新增 MCP 工具测试：kb_list / CRM 只读工具 / 审批制写工具。

风格与 test_kb_mcp_server.py 一致：fake session + monkeypatch，不起真实 PG。
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.models.agent_approval import AgentApproval
from app.models.customer import Customer
from app.models.follow_up import FollowUpRecord
from app.services import mcp_server
from app.services.mcp_server import (
    McpToolError,
    crm_add_followup_impl,
    crm_get_customer_impl,
    crm_search_customers_impl,
    kb_list_impl,
    mail_draft_create_impl,
)


def _user(uid=1, **kw):
    base = {"id": uid, "tenant_id": 1, "username": f"u{uid}", "name": f"用户{uid}", "role": "member", "status": 1}
    base.update(kw)
    return SimpleNamespace(**base)


def _customer(cid=5, **kw):
    base = {
        "id": cid, "tenant_id": 1, "name": "甲公司", "company": "甲公司",
        "position": "总监", "phone": "138", "email": "a@b.com", "status": "intention",
        "industries": ["制造"], "tags": ["重点"], "wechat": None, "address": None,
        "source": "展会", "birthday": None, "profile": "画像文本", "deleted_at": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


class _FakeResult:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    """队列式 fake session：execute 按序弹出预设结果；add 收集；flush 分配自增 id。"""

    def __init__(self, results=(), get_map=None):
        self._results = list(results)
        self._get_map = dict(get_map or {})
        self.added: list = []
        self.statements: list = []
        self.committed = False
        self._next_id = 100

    async def execute(self, stmt, *args, **kwargs):
        self.statements.append(stmt)
        return _FakeResult(self._results.pop(0) if self._results else [])

    async def get(self, model, ident):
        return self._get_map.get((model, ident))

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = self._next_id
                self._next_id += 1

    async def commit(self):
        self.committed = True


# ---------------------------------------------------------------------------
# kb_list
# ---------------------------------------------------------------------------


async def test_kb_list_scopes_by_acl(monkeypatch):
    captured: dict = {}

    async def _accessible_ids(db, user, rtype):
        captured["rtype"] = rtype
        return [11, 22]  # 该用户只可读这两个 KB

    monkeypatch.setattr(mcp_server, "accessible_ids", _accessible_ids)
    kbs = [
        SimpleNamespace(id=11, name="产品资料", type="general"),
        SimpleNamespace(id=22, name="甲公司-专属知识库", type="customer"),
    ]
    db = _FakeSession(results=[kbs, [(11, 3)]])  # 第二次 execute：文档计数行
    result = await kb_list_impl(db, _user())
    assert captured["rtype"] == "kb"
    assert result == [
        {"kb_id": 11, "name": "产品资料", "type": "general", "doc_count": 3},
        {"kb_id": 22, "name": "甲公司-专属知识库", "type": "customer", "doc_count": 0},
    ]


async def test_kb_list_empty_when_no_accessible_kb(monkeypatch):
    async def _accessible_ids(db, user, rtype):
        return []

    monkeypatch.setattr(mcp_server, "accessible_ids", _accessible_ids)
    assert await kb_list_impl(_FakeSession(), _user()) == []


# ---------------------------------------------------------------------------
# crm_search_customers
# ---------------------------------------------------------------------------


async def test_crm_search_customers_tenant_scoped():
    db = _FakeSession(results=[[_customer(5), _customer(6, name="乙公司")]])
    result = await crm_search_customers_impl(db, _user(), "甲", limit=10)
    stmt = str(db.statements[0].whereclause)
    assert "tenant_id" in stmt  # 租户隔离（CRM 无内容级 ACL，见 mcp_server 注释）
    assert "deleted_at" in stmt  # 排除软删
    assert len(result) == 2
    r = result[0]
    assert r["customer_id"] == 5
    assert r["name"] == "甲公司"
    assert r["industries"] == ["制造"]
    assert r["status"] == "intention"


async def test_crm_search_customers_blank_query_returns_empty():
    db = _FakeSession()
    assert await crm_search_customers_impl(db, _user(), "   ") == []
    assert db.statements == []  # 空查询不打数据库


# ---------------------------------------------------------------------------
# crm_get_customer
# ---------------------------------------------------------------------------


async def test_crm_get_customer_detail_with_followups_and_opportunities():
    followup = SimpleNamespace(
        id=31, type="call", content="电话沟通", ai_summary="摘要",
        created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    opp = SimpleNamespace(
        id=41, name="年度采购", amount=100000, stage="negotiation",
        probability=60, expected_close_date=None,
    )
    db = _FakeSession(
        results=[[followup], [opp]],
        get_map={(Customer, 5): _customer()},
    )
    result = await crm_get_customer_impl(db, _user(), 5)
    assert result["customer_id"] == 5
    assert result["profile"] == "画像文本"
    assert result["recent_followups"][0]["content"] == "电话沟通"
    assert result["recent_followups"][0]["created_at"] == "2026-09-01T00:00:00+00:00"
    assert result["open_opportunities"][0]["stage"] == "negotiation"
    assert result["open_opportunities"][0]["amount"] == 100000.0


async def test_crm_get_customer_missing():
    db = _FakeSession(get_map={})
    with pytest.raises(McpToolError, match="不存在"):
        await crm_get_customer_impl(db, _user(), 999)


async def test_crm_get_customer_cross_tenant_denied():
    db = _FakeSession(get_map={(Customer, 5): _customer(tenant_id=2)})
    with pytest.raises(McpToolError, match="不存在"):
        await crm_get_customer_impl(db, _user(), 5)


# ---------------------------------------------------------------------------
# crm_add_followup / mail_draft_create：只落审批单，不落业务数据
# ---------------------------------------------------------------------------


async def test_crm_add_followup_creates_approval_only():
    db = _FakeSession(
        results=[[_user(10, role="admin")]],  # create_approval 查 admin
        get_map={(Customer, 5): _customer()},
    )
    result = await crm_add_followup_impl(db, _user(), 5, "电话沟通了报价", "下周拜访")
    assert result["status"] == "pending_approval"
    assert result["approval_id"] is not None
    assert "审批" in result["message"]
    approvals = [o for o in db.added if isinstance(o, AgentApproval)]
    assert len(approvals) == 1
    approval = approvals[0]
    assert approval.status == "pending"
    assert approval.tool_name == "crm_add_followup"
    assert approval.arguments == {
        "customer_id": 5, "content": "电话沟通了报价", "next_plan": "下周拜访",
    }
    assert "甲公司" in approval.summary  # 摘要含客户名与内容
    assert "电话沟通" in approval.summary
    assert db.committed
    # 关键断言：写工具不落业务库
    assert not [o for o in db.added if isinstance(o, FollowUpRecord)]


async def test_crm_add_followup_missing_customer():
    db = _FakeSession(get_map={})
    with pytest.raises(McpToolError, match="不存在"):
        await crm_add_followup_impl(db, _user(), 999, "内容")


async def test_crm_add_followup_blank_content():
    db = _FakeSession(get_map={(Customer, 5): _customer()})
    with pytest.raises(McpToolError, match="为空"):
        await crm_add_followup_impl(db, _user(), 5, "  ")


async def test_mail_draft_create_creates_approval_only():
    db = _FakeSession(
        results=[[_user(10, role="admin")]],
        get_map={(Customer, 5): _customer()},
    )
    result = await mail_draft_create_impl(db, _user(), 5, "a@b.com", "报价确认", "正文")
    assert result["status"] == "pending_approval"
    approval = [o for o in db.added if isinstance(o, AgentApproval)][0]
    assert approval.tool_name == "mail_draft_create"
    assert approval.arguments["to"] == "a@b.com"
    assert "a@b.com" in approval.summary  # 摘要含收件人与主题
    assert "报价确认" in approval.summary
    assert db.committed


async def test_mail_draft_create_without_customer():
    db = _FakeSession(results=[[_user(10, role="admin")]])
    result = await mail_draft_create_impl(db, _user(), None, "a@b.com", "主题", "正文")
    assert result["status"] == "pending_approval"
    approval = [o for o in db.added if isinstance(o, AgentApproval)][0]
    assert approval.arguments["customer_id"] is None


async def test_mail_draft_create_requires_to_and_subject():
    db = _FakeSession()
    with pytest.raises(McpToolError, match="不能为空"):
        await mail_draft_create_impl(db, _user(), None, "", "主题", "正文")
    with pytest.raises(McpToolError, match="不能为空"):
        await mail_draft_create_impl(db, _user(), None, "a@b.com", " ", "正文")
