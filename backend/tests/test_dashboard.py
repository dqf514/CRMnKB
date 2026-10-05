"""dashboard 时区口径测试：晨报统计按服务器本地日界（与触发基准统一）。"""
from datetime import datetime, timedelta, timezone

from app.services.dashboard import _local_today_range


def test_local_today_range_is_local_day_in_naive_utc():
    start, end = _local_today_range()
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    # 返回 naive UTC 区间且覆盖当前时刻
    assert start.tzinfo is None and end.tzinfo is None
    assert start <= now_utc <= end
    # 换算回本地后正好是本地当天 00:00 / 23:59:59
    start_local = start.replace(tzinfo=timezone.utc).astimezone()
    end_local = end.replace(tzinfo=timezone.utc).astimezone()
    assert (start_local.hour, start_local.minute, start_local.second) == (0, 0, 0)
    assert (end_local.hour, end_local.minute) == (23, 59)
    assert start_local.date() == end_local.date()


# ---------- today_overview / daily_report 主路径（fake session，不连真实 DB） ----------

from types import SimpleNamespace

from app.services import dashboard as dashboard_svc


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: self._rows)

    def all(self):
        return self._rows


class _QueueSession:
    """execute 按序弹出预设结果；scalar 按序弹出计数。"""

    def __init__(self, execute_results=(), scalars=()):
        self._exec = [_FakeResult(r) for r in execute_results]
        self._scalars = list(scalars)

    async def execute(self, stmt):
        assert self._exec, "execute 调用次数超出预设"
        return self._exec.pop(0)

    async def scalar(self, stmt):
        assert self._scalars, "scalar 调用次数超出预设"
        return self._scalars.pop(0)


def _user(role="user", uid=1, name="用户", username="u1"):
    return SimpleNamespace(
        id=uid, tenant_id=1, role=role, name=name, username=username, status=1
    )


def _task(tid, title):
    return SimpleNamespace(
        id=tid, title=title, priority="high",
        due_date=datetime(2026, 10, 3, 10, 0), customer_id=3, type="call",
    )


async def test_today_overview_main_path():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    stale_never = SimpleNamespace(id=5, name="甲公司", company="甲", created_at=now - timedelta(days=30))
    stale_old = SimpleNamespace(id=6, name="乙公司", company="乙", created_at=now - timedelta(days=60))
    fresh = SimpleNamespace(id=7, name="丙公司", company="丙", created_at=now - timedelta(days=60))
    stale_rows = [
        (stale_never, None),                       # 从未跟进
        (stale_old, now - timedelta(days=10)),     # 10 天未跟进
        (fresh, now - timedelta(days=1)),          # 1 天前刚跟进，不算久未跟进
    ]
    audit = SimpleNamespace(
        action="create", resource_type="customer", detail={"k": 1}, created_at=now
    )
    db = _QueueSession(
        execute_results=[
            [_task(1, "今日任务")],      # today_tasks
            [_task(2, "逾期任务")],      # overdue_tasks
            stale_rows,                  # 久未跟进
            [(audit, None, "u1")],       # activity（name 为空回退 username）
        ],
        scalars=[1, 2, 3, 4, 5],         # files/followups/customers/notes/unread
    )

    result = await dashboard_svc.today_overview(db, _user())

    assert [t["title"] for t in result["today_tasks"]] == ["今日任务"]
    assert [t["title"] for t in result["overdue_tasks"]] == ["逾期任务"]
    # 久未跟进：按天数倒序，fresh 被排除
    stale = result["stale_customers"]
    assert [c["id"] for c in stale] == [5, 6]
    assert stale[0]["never"] is True and stale[0]["days"] == 30
    assert stale[1]["never"] is False and stale[1]["days"] == 10
    assert result["today_new"] == {"files": 1, "followups": 2, "customers": 3, "notes": 4}
    assert result["unread_notifications"] == 5
    assert result["activity"][0]["user"] == "u1"
    assert result["activity"][0]["action"] == "create"


async def test_today_overview_empty():
    """无任何数据：全部为空集合/零。"""
    db = _QueueSession(
        execute_results=[[], [], [], []],
        scalars=[None, 0, None, 0, None],  # scalar 返回 None 也按 0 兜底
    )
    result = await dashboard_svc.today_overview(db, _user())
    assert result["today_tasks"] == []
    assert result["overdue_tasks"] == []
    assert result["stale_customers"] == []
    assert result["today_new"] == {"files": 0, "followups": 0, "customers": 0, "notes": 0}
    assert result["unread_notifications"] == 0
    assert result["activity"] == []


async def test_daily_report_self_for_non_admin_team_request():
    """非 admin 请求 team=True 被降级为仅本人；day 参数指定统计日期。"""
    db = _QueueSession(scalars=[1, 2, 3, 4, 5])
    result = await dashboard_svc.daily_report(db, _user(role="user"), day="2026-09-30", team=True)
    assert result["date"] == "2026-09-30"
    assert result["team"] is False
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item == {
        "user_id": 1, "user_name": "用户",
        "followups": 1, "tasks_done": 2, "files": 3, "notes": 4, "customers_new": 5,
    }


async def test_daily_report_team_for_admin():
    """admin + team=True：拉取租户内全部启用用户，逐人统计。"""
    users = [_user(role="admin", uid=1, name="管理员"), _user(uid=2, name="成员")]
    db = _QueueSession(
        execute_results=[users],
        scalars=[1, 0, 0, 0, 0, 0, 0, 2, 0, 0],  # 每人 5 个计数
    )
    result = await dashboard_svc.daily_report(db, users[0], day=None, team=True)
    assert result["team"] is True
    assert [i["user_name"] for i in result["items"]] == ["管理员", "成员"]
    assert result["items"][0]["followups"] == 1
    assert result["items"][1]["files"] == 2
