from datetime import date, datetime, timedelta, timezone

import pytest

from app.services.email import build_email, send_email
from app.services.workflow import (
    eval_condition,
    eval_conditions,
    is_birthday_today,
    should_run,
)

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)  # 周四 isoweekday=4


# ---------- 触发器到期判断 ----------

def test_interval_trigger():
    cfg = {"interval_minutes": 30}
    assert should_run("interval", cfg, None, NOW) is True
    assert should_run("interval", cfg, NOW - timedelta(minutes=10), NOW) is False
    assert should_run("interval", cfg, NOW - timedelta(minutes=31), NOW) is True


def test_daily_trigger():
    cfg = {"time": "09:00"}
    # 当天 09:00 之后且当天未跑过
    assert should_run("daily", cfg, None, NOW) is True
    assert should_run("daily", cfg, NOW - timedelta(days=1), NOW) is True
    assert should_run("daily", cfg, NOW - timedelta(hours=1), NOW) is False
    # 还没到当天时刻
    early = NOW.replace(hour=8, minute=59)
    assert should_run("daily", cfg, None, early) is False


def test_weekly_trigger():
    cfg = {"weekday": 4, "time": "09:00"}  # 周四
    assert should_run("weekly", cfg, None, NOW) is True
    assert should_run("weekly", cfg, NOW - timedelta(hours=2), NOW) is False
    cfg_other_day = {"weekday": 1, "time": "09:00"}  # 周一
    assert should_run("weekly", cfg_other_day, None, NOW) is False


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
