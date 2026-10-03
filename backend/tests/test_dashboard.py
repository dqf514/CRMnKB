"""dashboard 时区口径测试：晨报统计按服务器本地日界（与触发基准统一）。"""
from datetime import datetime, timezone

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
