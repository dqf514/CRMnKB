from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models.notification import Notification
from app.models.workflow_run import WorkflowRun
from app.services.email import build_email, send_email
from app.services.workflow import (
    _to_local,
    eval_condition,
    eval_conditions,
    is_birthday_today,
    run_workflow,
    should_run,
)

# 服务器本地时区的中午（aware；should_run 的 daily/weekly 配置时间按本地时间解释）
NOW = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc).astimezone()


# ---------- 触发器到期判断 ----------

def test_interval_trigger():
    cfg = {"interval_minutes": 30}
    assert should_run("interval", cfg, None, NOW) is True
    assert should_run("interval", cfg, NOW - timedelta(minutes=10), NOW) is False
    assert should_run("interval", cfg, NOW - timedelta(minutes=31), NOW) is True


def test_daily_trigger():
    cfg = {"time": "09:00"}  # 本地 09:00；NOW 为本地 12:00，已过触发时刻
    # 当天 09:00 之后且当天未跑过
    assert should_run("daily", cfg, None, NOW) is True
    assert should_run("daily", cfg, NOW - timedelta(days=1), NOW) is True
    assert should_run("daily", cfg, NOW - timedelta(hours=1), NOW) is False
    # 还没到当天时刻（本地 08:59）
    early = NOW.replace(hour=8, minute=59)
    assert should_run("daily", cfg, None, early) is False


def test_daily_trigger_naive_now_treated_as_utc():
    """naive now（项目约定 UTC）先换算本地再与配置时间比较。"""
    cfg = {"time": "09:00"}
    naive_utc = NOW.astimezone(timezone.utc).replace(tzinfo=None)
    assert should_run("daily", cfg, None, naive_utc) is True
    early_utc = NOW.replace(hour=8, minute=59).astimezone(timezone.utc).replace(tzinfo=None)
    assert should_run("daily", cfg, None, early_utc) is False


def test_to_local_naive_and_aware():
    naive = datetime(2026, 8, 13, 4, 0)  # naive 视为 UTC
    assert _to_local(naive) == naive.replace(tzinfo=timezone.utc).astimezone()
    assert _to_local(NOW) == NOW  # aware 本地时间原样


def test_weekly_trigger():
    cfg = {"weekday": NOW.isoweekday(), "time": "09:00"}  # 今天（本地星期）
    assert should_run("weekly", cfg, None, NOW) is True
    assert should_run("weekly", cfg, NOW - timedelta(hours=2), NOW) is False
    other_day = (NOW.isoweekday() % 7) + 1  # 另一天
    assert should_run("weekly", {"weekday": other_day, "time": "09:00"}, None, NOW) is False


def test_birthday_trigger_once_per_day():
    assert should_run("birthday", {}, None, NOW) is True
    assert should_run("birthday", {}, NOW - timedelta(hours=1), NOW) is False
    assert should_run("birthday", {}, NOW - timedelta(days=1), NOW) is True


def test_condition_trigger_always_runs():
    assert should_run("condition", {}, NOW, NOW) is True


def test_unknown_trigger_never_runs():
    assert should_run("cron", {}, None, NOW) is False


# ---------- conditions DSL ----------

def test_eval_condition_ops():
    customer = {"name": "某某科技", "industry": "软件", "status": "intention"}
    assert eval_condition({"field": "name", "op": "contains", "value": "科技"}, customer)
    assert eval_condition({"field": "status", "op": "eq", "value": "intention"}, customer)
    assert eval_condition({"field": "status", "op": "ne", "value": "closed"}, customer)
    assert not eval_condition({"field": "industry", "op": "eq", "value": "制造"}, customer)


def test_eval_condition_gt_lt_lexicographic():
    customer = {"status": "intention"}
    assert eval_condition({"field": "status", "op": "gt", "value": "closed"}, customer)
    assert eval_condition({"field": "status", "op": "lt", "value": "potential"}, customer)
    # 字段为 None 时 gt/lt/contains 不命中
    assert not eval_condition({"field": "phone", "op": "contains", "value": "138"}, customer)


def test_eval_condition_gt_lt_numeric_aware():
    """数值感知比较：两边可转 float 按数值比，修复字典序错序（'10' < '9'）。"""
    customer = {"phone": "138"}
    # 数值语义：138 > 20 成立（字典序 "138" < "20" 不成立）
    assert eval_condition({"field": "phone", "op": "gt", "value": "20"}, customer) is True
    # 数值语义：138 < 9 不成立（字典序 "138" < "9" 反而成立）
    assert eval_condition({"field": "phone", "op": "lt", "value": "9"}, customer) is False
    # 一边不是数字时回退字符串比较
    assert eval_condition({"field": "status", "op": "gt", "value": "closed"}, {"status": "intention"}) is True
    # 字段为 None 时不命中
    assert eval_condition({"field": "phone", "op": "gt", "value": "1"}, {"phone": None}) is False


def test_eval_condition_field_whitelist_rejected():
    with pytest.raises(ValueError, match="白名单"):
        eval_condition({"field": "password_hash", "op": "eq", "value": "x"}, {})
    with pytest.raises(ValueError, match="白名单"):
        eval_condition({"field": "1; DROP TABLE users; --", "op": "eq", "value": "x"}, {})


def test_eval_condition_op_whitelist_rejected():
    with pytest.raises(ValueError, match="操作"):
        eval_condition({"field": "name", "op": "regex", "value": "x"}, {"name": "x"})


def test_eval_conditions_and_semantics():
    customer = {"name": "A", "status": "intention", "industry": "软件"}
    assert eval_conditions([], customer) is True  # 空条件 = 全部匹配
    assert eval_conditions(
        [{"field": "status", "op": "eq", "value": "intention"},
         {"field": "industry", "op": "contains", "value": "软"}],
        customer,
    ) is True
    assert eval_conditions(
        [{"field": "status", "op": "eq", "value": "intention"},
         {"field": "industry", "op": "eq", "value": "制造"}],
        customer,
    ) is False


# ---------- birthday ----------

def test_is_birthday_today():
    assert is_birthday_today(date(1990, 8, 13), NOW) is True
    assert is_birthday_today(date(1990, 8, 14), NOW) is False
    assert is_birthday_today(None, NOW) is False


# ---------- email ----------

def test_build_email_renders_placeholders():
    msg = build_email(
        "{{customer_name}} 生日快乐",
        "尊敬的 {{customer_name}}，祝您生日快乐！",
        {"customer_name": "张三", "email": "a@b.com"},
    )
    assert msg["subject"] == "张三 生日快乐"
    assert "张三" in msg["body"]
    assert msg["to"] == "a@b.com"


async def test_send_email_without_smtp_config():
    from app.config import settings

    assert not settings.SMTP_HOST  # 测试环境默认未配置
    with pytest.raises(RuntimeError, match="SMTP 未配置"):
        await send_email("a@b.com", "主题", "正文")


def test_send_sync_starttls_failure_closes_connection(monkeypatch):
    """starttls 失败时连接仍被关闭（quit 抛错回退 close），不泄漏连接。"""
    import smtplib

    from app.config import settings
    from app.services import email as email_mod

    events: list[str] = []

    class _FakeSMTP:
        def __init__(self, *args, **kwargs):
            events.append("connect")

        def starttls(self):
            events.append("starttls")
            raise smtplib.SMTPException("tls handshake failed")

        def quit(self):
            events.append("quit")
            raise smtplib.SMTPServerDisconnected("connection broken")

        def close(self):
            events.append("close")

    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(settings, "SMTP_USE_SSL", False)
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    with pytest.raises(smtplib.SMTPException):
        email_mod._send_sync("a@b.com", "主题", "正文")
    assert events == ["connect", "starttls", "quit", "close"]


# ---------- run_workflow：keyset 分批扫描客户 ----------


class _FakeResult:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    """队列式 fake session：execute 按序弹出预设结果（去重标记 3 条 + 客户批次 N+1 条）。"""

    def __init__(self, results=()):
        self._results = list(results)
        self.added: list = []
        self._next_id = 100

    async def execute(self, stmt, *args, **kwargs):
        return _FakeResult(self._results.pop(0) if self._results else [])

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = self._next_id
                self._next_id += 1


def _customer(cid, **kw):
    base = {
        "id": cid, "tenant_id": 1, "name": f"客户{cid}", "industry": None,
        "status": "intention", "source": None, "phone": None, "email": None,
        "owner_id": 9, "birthday": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _workflow(**kw):
    base = {
        "id": 1, "tenant_id": 1, "name": "测试工作流", "enabled": True,
        "trigger_type": "condition", "trigger_config": {},
        "conditions": [{"field": "status", "op": "eq", "value": "intention"}],
        "action_type": "create_notification",
        "action_config": {"title": "跟进 {{customer_name}}"},
        "last_run_at": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


async def test_run_workflow_batches_customers(monkeypatch):
    """客户按 keyset 分批扫描：批大小=1 时两个匹配客户分两批处理，不全量加载。"""
    from app.services import workflow as wf_mod

    monkeypatch.setattr(wf_mod, "_CUSTOMER_BATCH_SIZE", 1)
    c1, c2 = _customer(1), _customer(2)
    other = _customer(3, status="closed")  # 不匹配条件
    db = _FakeSession(results=[
        [],          # emailed_markers
        [],          # open_rule_tasks
        [],          # notified_keys
        [c1],        # 批次 1
        [c2],        # 批次 2
        [other],     # 批次 3
        [],          # 批次 4：空 → 结束
    ])
    run = await run_workflow(db, _workflow(), now=datetime(2026, 8, 13, 12, 0))
    assert run.status == "success"
    assert run.matched_count == 2  # other 不匹配未计入
    notifications = [o for o in db.added if isinstance(o, Notification)]
    assert len(notifications) == 2
    assert all(n.user_id == 9 for n in notifications)  # 归属客户 owner
    assert {n.title for n in notifications} == {"跟进 客户1", "跟进 客户2"}
    assert any(isinstance(o, WorkflowRun) for o in db.added)
