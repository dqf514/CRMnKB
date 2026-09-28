from datetime import datetime, timedelta, timezone

from app.services.reminder import (
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
