"""monitoring 告警检查测试：psutil 打桩 + fake session，不连真实 DB、不跑真实备份。

覆盖：磁盘/内存/LLM 失败告警触发与状态翻转去重、psutil 异常兜底、
定时自动备份失败不影响告警主流程。
"""
from types import SimpleNamespace

import pytest

from app.services import monitoring


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: self._rows)


class _FakeSession:
    """check_alerts 只用到：execute（查 admin 列表）/ scalar（LLM 失败计数）/ add / commit。"""

    def __init__(self, admins=(), llm_fails=0):
        self._admins = list(admins)
        self._llm_fails = llm_fails
        self.added = []
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, stmt):
        return _FakeResult(self._admins)

    async def scalar(self, stmt):
        return self._llm_fails

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


@pytest.fixture
def alert_env(monkeypatch):
    """每用例重置模块级告警状态，打桩 DB 会话与自动备份。"""
    monitoring._state.clear()
    env = SimpleNamespace(session=None)

    def _session_factory():
        assert env.session is not None, "测试需先设置 env.session"
        return env.session

    async def _noop_auto_backup():
        return None

    monkeypatch.setattr(monitoring, "AsyncSessionLocal", _session_factory)
    monkeypatch.setattr(monitoring, "maybe_auto_backup", _noop_auto_backup)
    return env


def _stub_psutil(monkeypatch, disk_pct=None, mem_pct=None):
    """disk_pct/mem_pct 传 None 表示对应 psutil 调用抛异常。"""
    def _disk_usage(path):
        if disk_pct is None:
            raise OSError("盘符不存在")
        return SimpleNamespace(percent=disk_pct)

    def _virtual_memory():
        if mem_pct is None:
            raise OSError("读取失败")
        return SimpleNamespace(percent=mem_pct)

    monkeypatch.setattr(monitoring.psutil, "disk_usage", _disk_usage)
    monkeypatch.setattr(monitoring.psutil, "virtual_memory", _virtual_memory)


async def test_disk_and_mem_alerts_fire_and_notify_admins(alert_env, monkeypatch):
    """磁盘/内存超阈值：触发告警并给每个启用 admin 写通知。"""
    admins = [SimpleNamespace(id=1, tenant_id=1), SimpleNamespace(id=2, tenant_id=1)]
    alert_env.session = _FakeSession(admins=admins, llm_fails=0)
    _stub_psutil(monkeypatch, disk_pct=95, mem_pct=99)

    fired = await monitoring.check_alerts()
    assert set(fired) == {"disk", "mem"}
    # 每个告警给 2 个 admin 各一条通知（disk 2 条 + mem 2 条）
    assert len(alert_env.session.added) == 4
    titles = {n.title for n in alert_env.session.added}
    assert titles == {"磁盘空间告警", "内存使用告警"}
    assert all(n.type == "system_alert" for n in alert_env.session.added)


async def test_alert_fires_once_until_recovery(alert_env, monkeypatch):
    """状态翻转去重：持续超阈值只告警一次，恢复后再次超阈值会重新告警。"""
    alert_env.session = _FakeSession(admins=[SimpleNamespace(id=1, tenant_id=1)])
    _stub_psutil(monkeypatch, disk_pct=95, mem_pct=10)

    assert await monitoring.check_alerts() == ["disk"]
    assert await monitoring.check_alerts() == []  # 不重复告警

    _stub_psutil(monkeypatch, disk_pct=10, mem_pct=10)
    assert await monitoring.check_alerts() == []  # 恢复，重置状态
    assert monitoring._state["disk"] is False

    _stub_psutil(monkeypatch, disk_pct=95, mem_pct=10)
    assert await monitoring.check_alerts() == ["disk"]  # 再次触发


async def test_no_alert_below_threshold(alert_env, monkeypatch):
    """全部低于阈值：无告警、无通知。"""
    alert_env.session = _FakeSession(admins=[SimpleNamespace(id=1, tenant_id=1)], llm_fails=0)
    _stub_psutil(monkeypatch, disk_pct=10, mem_pct=10)
    assert await monitoring.check_alerts() == []
    assert alert_env.session.added == []


async def test_psutil_exception_treated_as_zero(alert_env, monkeypatch):
    """psutil 读不到（如挂载点不存在）按 0 处理，不误报。"""
    alert_env.session = _FakeSession(admins=[SimpleNamespace(id=1, tenant_id=1)])
    _stub_psutil(monkeypatch, disk_pct=None, mem_pct=None)
    assert await monitoring.check_alerts() == []


async def test_llm_fail_threshold_alert(alert_env, monkeypatch):
    """窗口内 LLM 失败数达到阈值触发 llm 告警。"""
    from app.config import settings

    alert_env.session = _FakeSession(
        admins=[SimpleNamespace(id=1, tenant_id=1)],
        llm_fails=settings.ALERT_LLM_FAIL_THRESHOLD,
    )
    _stub_psutil(monkeypatch, disk_pct=10, mem_pct=10)
    assert await monitoring.check_alerts() == ["llm"]
    assert alert_env.session.added[0].title == "LLM 服务异常告警"


async def test_auto_backup_failure_does_not_break_alerts(alert_env, monkeypatch):
    """定时自动备份抛异常只记日志，不影响告警结果。"""
    alert_env.session = _FakeSession(admins=[SimpleNamespace(id=1, tenant_id=1)])
    _stub_psutil(monkeypatch, disk_pct=95, mem_pct=10)

    async def _boom():
        raise RuntimeError("备份失败")

    monkeypatch.setattr(monitoring, "maybe_auto_backup", _boom)
    assert await monitoring.check_alerts() == ["disk"]
