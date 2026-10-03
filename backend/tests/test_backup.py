"""备份服务测试：UTC 时间戳口径 + 定时自动备份开关/按日去重。

风格与全项目一致：monkeypatch 打桩 subprocess/DB，不起真实 PG、不跑 pg_dump。
"""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.config import settings
from app.services import backup as backup_svc


@pytest.fixture
def backup_env(tmp_path, monkeypatch):
    """备份/上传/品牌目录全部指向 tmp_path（settings 上是只读 property，打类级桩）。"""
    bp = tmp_path / "backups"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    cls = type(settings)
    monkeypatch.setattr(cls, "backup_path", property(lambda self: bp))
    monkeypatch.setattr(cls, "upload_path", property(lambda self: uploads))
    monkeypatch.setattr(cls, "brand_path", property(lambda self: tmp_path / "brand"))
    return bp


async def test_run_backup_uses_utc_timestamp(backup_env, monkeypatch):
    """备份目录名带 Z 后缀（UTC 口径），manifest.created_at 为 aware UTC。"""
    async def _fake_run(cmd, input=None, env=None):
        return b"-- fake dump"

    monkeypatch.setattr(backup_svc, "_run", _fake_run)
    name = await backup_svc.run_backup()
    assert name.endswith("Z")
    ts = datetime.strptime(name, "%Y%m%d_%H%M%SZ").replace(tzinfo=timezone.utc)
    assert abs((datetime.now(timezone.utc) - ts).total_seconds()) < 60
    meta = json.loads((backup_env / name / "manifest.json").read_text(encoding="utf-8"))
    assert meta["ts"] == name
    assert meta["created_at"].endswith("+00:00")
    assert meta["db_bytes"] == len(b"-- fake dump")


class _FakeDbSession:
    """maybe_auto_backup 只用到 db.get(SystemSetting, key)。"""

    def __init__(self, value=None):
        self._row = SimpleNamespace(value=value) if value is not None else None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, model, key):
        return self._row


def _patch_settings_row(monkeypatch, value):
    monkeypatch.setattr(
        "app.database.AsyncSessionLocal", lambda: _FakeDbSession(value)
    )


async def test_maybe_auto_backup_disabled_by_default(backup_env, monkeypatch):
    """无配置行（默认关）：不备份。"""
    _patch_settings_row(monkeypatch, None)

    async def _must_not_run():
        raise AssertionError("默认关闭时不应执行备份")

    monkeypatch.setattr(backup_svc, "run_backup", _must_not_run)
    assert await backup_svc.maybe_auto_backup() is None


async def test_maybe_auto_backup_enabled_runs_once_per_utc_day(backup_env, monkeypatch):
    """开启且无当日备份 → 执行一次；当日已有备份 → 跳过。"""
    _patch_settings_row(monkeypatch, "true")
    calls: list[str] = []

    async def _fake_backup():
        calls.append("run")
        return "20990101_000000Z"

    monkeypatch.setattr(backup_svc, "run_backup", _fake_backup)
    assert await backup_svc.maybe_auto_backup() == "20990101_000000Z"
    assert calls == ["run"]

    # 造一个"今日"（UTC）的既有备份目录
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    d = backup_env / f"{today}_010203Z"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text("{}", encoding="utf-8")
    assert await backup_svc.maybe_auto_backup() is None
    assert calls == ["run"]  # 未再次执行
