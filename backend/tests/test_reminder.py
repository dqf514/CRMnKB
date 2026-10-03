from datetime import datetime, timedelta, timezone

from app.services.reminder import (
    _aware,
    match_due_soon_tasks,
    match_inactive_customers,
    match_stagnant_opportunities,
    render_template,
)

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)


def _ago(days=0, hours=0):
    return NOW - timedelta(days=days, hours=hours)


# ---------- render_template ----------

def test_render_template_basic():
    assert render_template("客户 {{customer_name}} 已 {{days}} 天未跟进", {
        "customer_name": "某某科技", "days": 9,
    }) == "客户 某某科技 已 9 天未跟进"


def test_render_template_keeps_unknown_placeholder():
    assert render_template("{{unknown}} 保持原样", {}) == "{{unknown}} 保持原样"


def test_render_template_no_space_variant():
    assert render_template("{{days}}天", {"days": 3}) == "3天"


# ---------- days_since_last_followup ----------

def test_inactive_customer_matched_by_last_followup():
    rows = [{"customer_id": 1, "customer_name": "A", "owner_id": 1,
             "last_followup_at": _ago(days=8), "customer_created_at": _ago(days=30)}]
    matched = match_inactive_customers(rows, threshold_days=7, now=NOW)
    assert len(matched) == 1
    assert matched[0]["days"] == 8


def test_inactive_customer_recent_followup_not_matched():
    rows = [{"customer_id": 1, "customer_name": "A", "owner_id": 1,
             "last_followup_at": _ago(days=3), "customer_created_at": _ago(days=30)}]
    assert match_inactive_customers(rows, 7, NOW) == []


def test_inactive_customer_without_followups_uses_created_at():
    rows = [
        {"customer_id": 1, "customer_name": "A", "owner_id": 1,
         "last_followup_at": None, "customer_created_at": _ago(days=10)},
        {"customer_id": 2, "customer_name": "B", "owner_id": 1,
         "last_followup_at": None, "customer_created_at": _ago(days=2)},
    ]
    matched = match_inactive_customers(rows, 7, NOW)
    assert [m["customer_id"] for m in matched] == [1]


# ---------- opportunity_stagnant ----------

def test_stagnant_opportunity_matched():
    rows = [{"opportunity_id": 1, "opportunity_name": "大单", "customer_id": 1,
             "customer_name": "A", "owner_id": 1, "stage": "negotiating",
             "updated_at": _ago(days=15)}]
    matched = match_stagnant_opportunities(rows, 10, NOW)
    assert len(matched) == 1
    assert matched[0]["days"] == 15


def test_stagnant_closed_stage_excluded():
    """终结阶段（closed_won/closed_lost，与实际商机阶段枚举一致）不触发停滞提醒。"""
    rows = [
        {"opportunity_id": 1, "stage": "closed_won", "updated_at": _ago(days=100)},
        {"opportunity_id": 2, "stage": "closed_lost", "updated_at": _ago(days=100)},
        {"opportunity_id": 3, "stage": "prospecting", "updated_at": _ago(days=100)},
        {"opportunity_id": 4, "stage": "negotiation", "updated_at": _ago(days=100)},
    ]
    matched = match_stagnant_opportunities(rows, 10, NOW)
    assert [m["opportunity_id"] for m in matched] == [3, 4]


def test_stagnant_recently_updated_not_matched():
    rows = [{"opportunity_id": 1, "stage": "negotiating", "updated_at": _ago(days=2)}]
    assert match_stagnant_opportunities(rows, 10, NOW) == []


# ---------- task_due_soon ----------

def test_due_soon_matched_within_threshold():
    rows = [{"task_id": 1, "title": "回访", "user_id": 1,
             "due_date": NOW + timedelta(hours=12), "status": "pending"}]
    matched = match_due_soon_tasks(rows, threshold_hours=24, now=NOW)
    assert len(matched) == 1
    assert matched[0]["hours_left"] == 12.0


def test_due_soon_beyond_threshold_not_matched():
    rows = [{"task_id": 1, "title": "回访", "user_id": 1,
             "due_date": NOW + timedelta(hours=48), "status": "pending"}]
    assert match_due_soon_tasks(rows, 24, NOW) == []


def test_due_soon_completed_and_overdue_excluded():
    rows = [
        {"task_id": 1, "due_date": NOW + timedelta(hours=1), "status": "completed"},
        {"task_id": 2, "due_date": NOW - timedelta(hours=1), "status": "pending"},
        {"task_id": 3, "due_date": NOW + timedelta(hours=1), "status": "in_progress"},
    ]
    matched = match_due_soon_tasks(rows, 24, NOW)
    assert [m["task_id"] for m in matched] == [3]


# ---------- 时区口径（_aware + aware now） ----------

def test_aware_helper_converts_naive_db_value_to_utc():
    """DB 列是 naive TIMESTAMP（UTC），_aware 统一转 aware UTC，与 aware now 比较口径一致。"""
    naive = datetime(2026, 8, 13, 10, 0)
    assert _aware(naive) == datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)
    assert _aware(None) is None
    aware = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)
    assert _aware(aware) is aware


def test_due_soon_naive_db_rows_match_against_aware_now():
    """模拟真实路径：DB 读出的 naive due_date 经 _aware 后与 aware now 比较，不抛 TypeError。"""
    naive_due = (NOW + timedelta(hours=6)).replace(tzinfo=None)
    rows = [{"task_id": 1, "title": "回访", "user_id": 1,
             "due_date": _aware(naive_due), "status": "pending"}]
    matched = match_due_soon_tasks(rows, 24, NOW)
    assert len(matched) == 1
    assert matched[0]["hours_left"] == 6.0


# ---------- 到期提醒去重键（规则 id + 业务对象 id，不含渲染变量） ----------

async def test_task_due_soon_dedupe_key_stable(monkeypatch):
    """含 {{hours_left}} 模板的标题每轮渲染结果不同；去重键不含渲染变量，
    同一规则+任务+到期时间只通知一次，到期时间变更后可再次提醒。"""
    from types import SimpleNamespace

    import app.services.reminder as reminder
    from app.models.notification import Notification

    seen_keys: set[str] = set()
    added: list = []

    async def _fake_exists(session, tenant_id, dedupe_key):
        return dedupe_key in seen_keys

    monkeypatch.setattr(reminder, "_notification_exists", _fake_exists)

    class _FakeSession:
        def __init__(self, tasks):
            self._tasks = tasks

        async def execute(self, stmt, params=None):
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: list(self._tasks))
            )

        def add(self, obj):
            added.append(obj)
            seen_keys.add(obj.dedupe_key)

    rule = SimpleNamespace(
        id=7, tenant_id=1,
        trigger_config={"threshold_hours": 24},
        action_config={"template": "任务 {{title}} 还剩 {{hours_left}} 小时"},
    )
    due = NOW + timedelta(hours=6)
    task = SimpleNamespace(id=5, tenant_id=1, title="回访", user_id=2,
                           status="pending", due_date=due)
    session = _FakeSession([task])

    assert await reminder._run_task_due_soon(session, rule, NOW) == (0, 1)
    # 下一轮：hours_left 变了（渲染标题不同），但去重键不变 → 不重复通知
    assert await reminder._run_task_due_soon(session, rule, NOW + timedelta(hours=1)) == (0, 0)
    assert len(added) == 1
    key = added[0].dedupe_key
    assert key.startswith("rule:7:task:5:")
    assert added[0].title == "任务 回访 还剩 6.0 小时"  # 标题仍按模板渲染（含 hours_left）
    assert "5.0" not in key and "6.0" not in key  # 去重键不含渲染变量
    # 任务改期（到期时间变更）→ 新去重键，可再次提醒
    task.due_date = due + timedelta(days=1)
    result = await reminder._run_task_due_soon(session, rule, NOW)
    assert result == (0, 0)  # 超出 24h 阈值，不匹配
    task.due_date = due + timedelta(hours=2)
    assert await reminder._run_task_due_soon(session, rule, NOW) == (0, 1)
    assert len(added) == 2
    assert all(isinstance(n, Notification) for n in added)
