"""Agent 审批链测试：创建 → 通知 admin → 批准执行/拒绝 全生命周期 + API 鉴权。

风格与 test_api_smoke.py / test_kb_mcp_server.py 一致：fake session + monkeypatch，
不起真实 PG。
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import deps
from app.core.security import create_access_token, create_mcp_token
from app.database import get_db
from app.main import app
from app.models.agent_approval import AgentApproval
from app.models.audit_log import AuditLog
from app.models.customer import Customer
from app.models.follow_up import FollowUpRecord
from app.models.notification import Notification
from app.models.opportunity import Opportunity
from app.models.task import Task
from app.models.user import User
from app.services import agent_approvals as svc
from app.services.agent_approvals import ApprovalError, create_approval, decide_approval


def _user(uid=1, **kw):
    base = {
        "id": uid, "tenant_id": 1, "username": f"u{uid}", "name": f"用户{uid}",
        "role": "member", "status": 1, "password_changed_at": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _customer(cid=5, **kw):
    base = {"id": cid, "tenant_id": 1, "name": "甲公司", "deleted_at": None}
    base.update(kw)
    return SimpleNamespace(**base)


def _approval(**kw):
    base = {
        "id": 7, "tenant_id": 1, "requester_user_id": 1, "chat_session_id": None,
        "tool_name": "crm_add_followup",
        "arguments": {"customer_id": 5, "content": "电话沟通了报价", "next_plan": "下周拜访"},
        "summary": "客户「甲公司」新增跟进：电话沟通了报价",
        "status": "pending", "decided_by": None, "decided_at": None,
        "decision_reason": None, "result": None,
        "created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc),
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

    async def refresh(self, obj):
        pass


# ---------------------------------------------------------------------------
# 服务层：创建
# ---------------------------------------------------------------------------


async def test_create_approval_notifies_all_admins():
    admins = [_user(10, role="admin", name="管理员A"), _user(11, role="admin", name="管理员B")]
    db = _FakeSession(results=[admins])
    approval = await create_approval(
        db, _user(), "crm_add_followup", {"customer_id": 5, "content": "x"}, "摘要"
    )
    assert approval.status == "pending"
    assert approval.id is not None  # flush 已分配
    assert approval.tool_name == "crm_add_followup"
    notifications = [o for o in db.added if isinstance(o, Notification)]
    assert {n.user_id for n in notifications} == {10, 11}  # 全部 admin 收到
    assert all(n.type == "agent_approval" for n in notifications)
    assert all(n.resource_id == approval.id for n in notifications)
    audits = [o for o in db.added if isinstance(o, AuditLog)]
    assert len(audits) == 1 and audits[0].action == "create"


async def test_create_approval_unknown_tool_rejected():
    with pytest.raises(ApprovalError, match="不支持审批流"):
        await create_approval(_FakeSession(), _user(), "kb_delete", {}, "摘要")


# ---------------------------------------------------------------------------
# 服务层：审批流转 + 执行器
# ---------------------------------------------------------------------------


async def test_decide_reject():
    approval = _approval()
    admin = _user(10, role="admin")
    db = _FakeSession()
    await decide_approval(db, approval, admin, "reject", "内容不实")
    assert approval.status == "rejected"
    assert approval.decided_by == 10
    assert approval.decided_at is not None
    assert approval.decision_reason == "内容不实"
    notifications = [o for o in db.added if isinstance(o, Notification)]
    assert len(notifications) == 1
    assert notifications[0].user_id == 1  # 通知发起人
    assert "拒绝" in notifications[0].title
    assert any(o.action == "reject" for o in db.added if isinstance(o, AuditLog))


async def test_decide_approve_followup_executes():
    approval = _approval()
    db = _FakeSession(get_map={(Customer, 5): _customer()})
    await decide_approval(db, approval, _user(10, role="admin"), "approve")
    assert approval.status == "executed"
    assert "跟进记录已写入" in approval.result
    records = [o for o in db.added if isinstance(o, FollowUpRecord)]
    assert len(records) == 1
    rec = records[0]
    assert rec.customer_id == 5
    assert rec.user_id == 1  # 归属发起人
    assert rec.type == "other"  # Agent 代写标记（非沟通渠道）
    assert "电话沟通了报价" in rec.content
    assert "下一步计划：下周拜访" in rec.content  # next_plan 并入正文
    actions = [o.action for o in db.added if isinstance(o, AuditLog)]
    assert actions == ["approve", "execute"]


async def test_decide_approve_followup_customer_missing_fails():
    approval = _approval()
    db = _FakeSession(get_map={})  # 客户不存在
    await decide_approval(db, approval, _user(10, role="admin"), "approve")
    assert approval.status == "failed"
    assert "客户不存在" in approval.result
    assert not [o for o in db.added if isinstance(o, FollowUpRecord)]  # 未落库


async def test_decide_approve_mail_smtp_unconfigured_fails(monkeypatch):
    """SMTP 未配置：send_email 抛 RuntimeError，审批单落 failed 并写清 result。"""

    async def _send(to, subject, body):
        raise RuntimeError("SMTP 未配置")

    monkeypatch.setattr(svc, "send_email", _send)
    approval = _approval(
        tool_name="mail_draft_create",
        arguments={"customer_id": 5, "to": "a@b.com", "subject": "报价", "body": "正文"},
    )
    db = _FakeSession(get_map={(Customer, 5): _customer()})
    await decide_approval(db, approval, _user(10, role="admin"), "approve")
    assert approval.status == "failed"
    assert "SMTP 未配置" in approval.result
    notifications = [o for o in db.added if isinstance(o, Notification)]
    assert "失败" in notifications[0].title


async def test_decide_approve_mail_success(monkeypatch):
    sent: dict = {}

    async def _send(to, subject, body):
        sent.update(to=to, subject=subject, body=body)

    monkeypatch.setattr(svc, "send_email", _send)
    approval = _approval(
        tool_name="mail_draft_create",
        arguments={"customer_id": 5, "to": "a@b.com", "subject": "报价", "body": "正文"},
    )
    db = _FakeSession(get_map={(Customer, 5): _customer()})
    await decide_approval(db, approval, _user(10, role="admin"), "approve")
    assert approval.status == "executed"
    assert "邮件已发送至 a@b.com" in approval.result
    assert sent == {"to": "a@b.com", "subject": "报价", "body": "正文"}


async def test_decide_non_pending_rejected():
    approval = _approval(status="executed")
    with pytest.raises(ApprovalError, match="已处理"):
        await decide_approval(_FakeSession(), approval, _user(10, role="admin"), "approve")


# ---------------------------------------------------------------------------
# 服务层：CRM 写工具执行器（阶段 3：建/改/删客户、建商机、建任务）
# ---------------------------------------------------------------------------


async def test_decide_approve_create_customer_executes():
    """批准建客户：画像写入 profile 并置 ready，owner 归属发起人，空数组元素被过滤。"""
    approval = _approval(
        tool_name="crm_create_customer",
        arguments={
            "name": "Alex Borissov", "company": "新加坡财富管理公司",
            "position": "Principal", "status": "intention",
            "industries": ["金融", ""], "tags": None,
            "birthday": "1980-05-01",
            "profile": "## 基本信息\n会议纪要提取资料",
        },
    )
    db = _FakeSession()
    await decide_approval(db, approval, _user(10, role="admin"), "approve")
    assert approval.status == "executed"
    customers = [o for o in db.added if isinstance(o, Customer)]
    assert len(customers) == 1
    c = customers[0]
    assert c.name == "Alex Borissov"
    assert c.tenant_id == 1
    assert c.owner_id == 1  # 归属发起人
    assert c.status == "intention"
    assert c.industries == ["金融"]  # 空值被过滤
    assert c.profile == "## 基本信息\n会议纪要提取资料"
    assert c.profile_status == "ready"
    assert c.profile_updated_at is not None
    assert "/customers/" in approval.result  # 结果含客户主页链接


async def test_decide_approve_create_customer_bad_status_fails():
    approval = _approval(
        tool_name="crm_create_customer",
        arguments={"name": "X", "status": "vip"},
    )
    db = _FakeSession()
    await decide_approval(db, approval, _user(10, role="admin"), "approve")
    assert approval.status == "failed"
    assert "状态非法" in approval.result
    assert not [o for o in db.added if isinstance(o, Customer)]  # 未落库


async def test_decide_approve_update_customer_whitelist():
    """批准改客户：白名单字段生效，tenant_id 等越权字段被忽略。"""
    cust = _customer()
    approval = _approval(
        tool_name="crm_update_customer",
        arguments={
            "customer_id": 5,
            "fields": {"phone": "139", "status": "negotiating", "tenant_id": 99, "profile": "新画像"},
        },
    )
    db = _FakeSession(get_map={(Customer, 5): cust})
    await decide_approval(db, approval, _user(10, role="admin"), "approve")
    assert approval.status == "executed"
    assert cust.phone == "139"
    assert cust.status == "negotiating"
    assert cust.tenant_id == 1  # 白名单外字段未生效
    assert cust.profile == "新画像"
    assert cust.profile_status == "ready"


async def test_decide_approve_update_customer_empty_fields_fails():
    approval = _approval(
        tool_name="crm_update_customer",
        arguments={"customer_id": 5, "fields": {"owner_id": 9}},  # 全在白名单外
    )
    db = _FakeSession(get_map={(Customer, 5): _customer()})
    await decide_approval(db, approval, _user(10, role="admin"), "approve")
    assert approval.status == "failed"
    assert "没有可更新的字段" in approval.result


async def test_decide_approve_delete_customer_soft_deletes():
    cust = _customer()
    approval = _approval(tool_name="crm_delete_customer", arguments={"customer_id": 5})
    db = _FakeSession(get_map={(Customer, 5): cust})
    await decide_approval(db, approval, _user(10, role="admin"), "approve")
    assert approval.status == "executed"
    assert cust.deleted_at is not None  # 软删
    assert "回收站" in approval.result


async def test_decide_approve_delete_cross_tenant_fails():
    cust = _customer(tenant_id=2)
    approval = _approval(tool_name="crm_delete_customer", arguments={"customer_id": 5})
    db = _FakeSession(get_map={(Customer, 5): cust})
    await decide_approval(db, approval, _user(10, role="admin"), "approve")
    assert approval.status == "failed"
    assert "客户不存在" in approval.result
    assert cust.deleted_at is None  # 未动


async def test_decide_approve_create_opportunity_executes():
    approval = _approval(
        tool_name="crm_create_opportunity",
        arguments={
            "customer_id": 5, "name": "年度采购", "amount": "120000",
            "stage": "proposal", "probability": 150, "expected_close_date": "2026-12-31",
        },
    )
    db = _FakeSession(get_map={(Customer, 5): _customer()})
    await decide_approval(db, approval, _user(10, role="admin"), "approve")
    assert approval.status == "executed"
    opps = [o for o in db.added if isinstance(o, Opportunity)]
    assert len(opps) == 1
    o = opps[0]
    assert o.customer_id == 5
    assert o.amount == 120000.0
    assert o.stage == "proposal"
    assert o.probability == 100  # 截断到 0-100
    assert o.owner_id == 1


async def test_decide_approve_create_opportunity_bad_stage_fails():
    approval = _approval(
        tool_name="crm_create_opportunity",
        arguments={"customer_id": 5, "name": "X", "stage": "winning"},
    )
    db = _FakeSession(get_map={(Customer, 5): _customer()})
    await decide_approval(db, approval, _user(10, role="admin"), "approve")
    assert approval.status == "failed"
    assert "阶段非法" in approval.result
    assert not [o for o in db.added if isinstance(o, Opportunity)]


async def test_decide_approve_create_task_executes():
    approval = _approval(
        tool_name="crm_create_task",
        arguments={
            "customer_id": 5, "title": "下周拜访", "priority": "high",
            "due_date": "2026-10-01T09:00:00",
        },
    )
    db = _FakeSession(get_map={(Customer, 5): _customer()})
    await decide_approval(db, approval, _user(10, role="admin"), "approve")
    assert approval.status == "executed"
    tasks = [o for o in db.added if isinstance(o, Task)]
    assert len(tasks) == 1
    t = tasks[0]
    assert t.title == "下周拜访"
    assert t.customer_id == 5
    assert t.tenant_id == 1
    assert t.ai_generated is True
    assert t.due_date == datetime(2026, 10, 1, 9, 0)


async def test_decide_approve_create_task_without_customer():
    approval = _approval(
        tool_name="crm_create_task",
        arguments={"title": "整理周报"},
    )
    db = _FakeSession()
    await decide_approval(db, approval, _user(10, role="admin"), "approve")
    assert approval.status == "executed"
    tasks = [o for o in db.added if isinstance(o, Task)]
    assert tasks[0].customer_id is None


# ---------------------------------------------------------------------------
# API 层
# ---------------------------------------------------------------------------


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _override_db(fake):
    async def _fake_get_db():
        yield fake

    app.dependency_overrides[get_db] = _fake_get_db


async def test_create_endpoint_rejects_login_jwt(client):
    """普通登录 JWT（无 aud=dsh-mcp）不允许创建审批单。"""
    _override_db(_FakeSession())
    resp = await client.post(
        "/api/v1/agent-approvals",
        json={"tool_name": "crm_add_followup", "arguments": {}, "summary": "x"},
        headers={"Authorization": f"Bearer {create_access_token(1, 'u1')}"},
    )
    assert resp.status_code == 401


async def test_create_endpoint_with_mcp_token(client):
    caller = _user(1)
    db = _FakeSession(
        results=[[_user(10, role="admin")]],  # create_approval 查 admin
        get_map={(User, 1): caller},  # resolve_mcp_user
    )
    _override_db(db)
    resp = await client.post(
        "/api/v1/agent-approvals",
        json={
            "tool_name": "crm_add_followup",
            "arguments": {"customer_id": 5, "content": "电话沟通"},
            "summary": "客户「甲公司」新增跟进：电话沟通",
        },
        headers={"Authorization": f"Bearer {create_mcp_token(1, 'u1', 60)}"},
    )
    assert resp.status_code == 201
    assert resp.json()["approval_id"] is not None
    assert db.committed
    assert any(isinstance(o, AgentApproval) for o in db.added)


async def test_create_endpoint_unknown_tool_400(client):
    db = _FakeSession(get_map={(User, 1): _user(1)})
    _override_db(db)
    resp = await client.post(
        "/api/v1/agent-approvals",
        json={"tool_name": "kb_delete", "arguments": {}, "summary": "x"},
        headers={"Authorization": f"Bearer {create_mcp_token(1, 'u1', 60)}"},
    )
    assert resp.status_code == 400


def _override_user(user):
    async def _fake_user():
        return user

    app.dependency_overrides[deps.get_current_user] = _fake_user


async def test_list_scopes_by_role(client):
    """admin 看全租户（无 requester 过滤），普通用户只看自己发起的。"""
    rows = [_approval(id=7), _approval(id=8, requester_user_id=2)]

    db_admin = _FakeSession(results=[rows])
    _override_db(db_admin)
    _override_user(_user(10, role="admin"))
    resp = await client.get("/api/v1/agent-approvals", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 200
    assert len(resp.json()) == 2
    where = str(db_admin.statements[0].whereclause)
    assert "requester_user_id" not in where  # admin 不加发起人过滤

    db_member = _FakeSession(results=[[rows[0]]])
    _override_db(db_member)
    _override_user(_user(1))
    resp = await client.get(
        "/api/v1/agent-approvals?status=pending", headers={"Authorization": "Bearer x"}
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    where = str(db_member.statements[0].whereclause)
    assert "requester_user_id" in where  # 普通用户按发起人过滤
    assert "status" in where  # status 过滤生效


async def test_decide_endpoint_requires_admin(client):
    _override_db(_FakeSession())
    _override_user(_user(1, role="member"))
    resp = await client.post(
        "/api/v1/agent-approvals/7/decide",
        json={"decision": "approve"},
        headers={"Authorization": "Bearer x"},
    )
    assert resp.status_code == 403


async def test_decide_endpoint_reject_happy_path(client):
    approval = _approval()
    db = _FakeSession(get_map={(AgentApproval, 7): approval})
    _override_db(db)
    _override_user(_user(10, role="admin"))
    resp = await client.post(
        "/api/v1/agent-approvals/7/decide",
        json={"decision": "reject", "reason": "不合适"},
        headers={"Authorization": "Bearer x"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "rejected"
    assert data["decision_reason"] == "不合适"
    assert data["decided_by"] == 10
    assert db.committed


async def test_decide_endpoint_approve_with_patched_executor(client, monkeypatch):
    async def _exec(db, approval):
        return "执行完成"

    monkeypatch.setattr(svc, "execute_approval", _exec)
    approval = _approval()
    db = _FakeSession(get_map={(AgentApproval, 7): approval})
    _override_db(db)
    _override_user(_user(10, role="admin"))
    resp = await client.post(
        "/api/v1/agent-approvals/7/decide",
        json={"decision": "approve"},
        headers={"Authorization": "Bearer x"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "executed"
    assert data["result"] == "执行完成"


async def test_decide_endpoint_non_pending_409(client):
    db = _FakeSession(get_map={(AgentApproval, 7): _approval(status="executed")})
    _override_db(db)
    _override_user(_user(10, role="admin"))
    resp = await client.post(
        "/api/v1/agent-approvals/7/decide",
        json={"decision": "approve"},
        headers={"Authorization": "Bearer x"},
    )
    assert resp.status_code == 409


async def test_decide_endpoint_cross_tenant_404(client):
    db = _FakeSession(get_map={(AgentApproval, 7): _approval(tenant_id=2)})
    _override_db(db)
    _override_user(_user(10, role="admin"))
    resp = await client.post(
        "/api/v1/agent-approvals/7/decide",
        json={"decision": "approve"},
        headers={"Authorization": "Bearer x"},
    )
    assert resp.status_code == 404
