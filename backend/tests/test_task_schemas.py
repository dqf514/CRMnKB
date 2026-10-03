"""TaskCreate/TaskUpdate 的 due_date 时区归一化：aware 输入转 naive UTC，naive 原样通过。

DB 的 tasks.due_date 是 naive TIMESTAMP（UTC），前端可能传带时区的 ISO 串，
schema 层统一归一化，避免 asyncpg 写 aware datetime 进 naive 列时报错或错位。
"""
from datetime import datetime, timedelta, timezone

from app.schemas.task import TaskCreate, TaskUpdate


# ---------- TaskCreate ----------

def test_create_aware_due_date_normalized_to_naive_utc():
    t = TaskCreate(title="回访", due_date="2026-10-01T12:00:00+08:00")
    assert t.due_date == datetime(2026, 10, 1, 4, 0)
    assert t.due_date.tzinfo is None


def test_create_z_suffix_due_date_normalized():
    t = TaskCreate(title="回访", due_date="2026-10-01T04:00:00Z")
    assert t.due_date == datetime(2026, 10, 1, 4, 0)
    assert t.due_date.tzinfo is None


def test_create_naive_due_date_unchanged():
    t = TaskCreate(title="回访", due_date="2026-10-01T12:00:00")
    assert t.due_date == datetime(2026, 10, 1, 12, 0)
    assert t.due_date.tzinfo is None


def test_create_none_due_date_passes():
    t = TaskCreate(title="回访")
    assert t.due_date is None


def test_create_aware_datetime_object_normalized():
    aware = datetime(2026, 10, 1, 12, 0, tzinfo=timezone(timedelta(hours=-5)))
    t = TaskCreate(title="回访", due_date=aware)
    assert t.due_date == datetime(2026, 10, 1, 17, 0)
    assert t.due_date.tzinfo is None


# ---------- TaskUpdate ----------

def test_update_aware_due_date_normalized_to_naive_utc():
    t = TaskUpdate(due_date="2026-10-01T12:00:00+08:00")
    assert t.due_date == datetime(2026, 10, 1, 4, 0)
    assert t.due_date.tzinfo is None


def test_update_naive_due_date_unchanged():
    t = TaskUpdate(due_date=datetime(2026, 10, 1, 12, 0))
    assert t.due_date == datetime(2026, 10, 1, 12, 0)
    assert t.due_date.tzinfo is None


def test_update_none_due_date_passes():
    t = TaskUpdate(status="completed")
    assert t.due_date is None
