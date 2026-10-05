"""备份服务测试：UTC 时间戳口径 + 定时自动备份开关/按日去重 + tar 恢复路径。

风格与全项目一致：monkeypatch 打桩 subprocess/DB，不起真实 PG、不跑 pg_dump。
"""
import gzip
import io
import json
import tarfile
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


# ---------- restore_backup：tar 解档 / 恢复路径 ----------


def _make_backup_dir(bp, name="20260101_000000Z"):
    """造一个最小可恢复的备份目录（manifest + db.sql.gz）。"""
    d = bp / name
    d.mkdir(parents=True)
    (d / "manifest.json").write_text("{}", encoding="utf-8")
    with gzip.open(d / "db.sql.gz", "wb") as f:
        f.write(b"-- fake sql")
    return d


def _write_tar(path, members: dict):
    with tarfile.open(path, "w:gz") as tar:
        for arcname, data in members.items():
            info = tarfile.TarInfo(arcname)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


async def test_restore_backup_happy_path(backup_env, monkeypatch, tmp_path):
    """恢复：清掉现有上传目录 → 解档回 data/ 下 → 灌 SQL（_run 打桩）。"""
    d = _make_backup_dir(backup_env)
    _write_tar(d / "uploads.tar.gz", {"uploads/a.txt": b"hi", "brand/logo.png": b"lg"})

    uploads = tmp_path / "uploads"
    (uploads / "stale.txt").write_text("旧文件", encoding="utf-8")

    ran = []

    async def _fake_run(cmd, input=None, env=None):
        ran.append((cmd, input))
        return b""

    monkeypatch.setattr(backup_svc, "_run", _fake_run)
    await backup_svc.restore_backup(d.name)

    # 旧文件被清、归档内容还原
    assert not (uploads / "stale.txt").exists()
    assert (uploads / "a.txt").read_bytes() == b"hi"
    assert (tmp_path / "brand" / "logo.png").read_bytes() == b"lg"
    # SQL 经 psql 灌入
    assert len(ran) == 1
    assert "psql" in ran[0][0]
    assert ran[0][1] == b"-- fake sql"


async def test_restore_backup_missing_raises(backup_env):
    with pytest.raises(RuntimeError, match="备份不存在"):
        await backup_svc.restore_backup("20990101_000000Z")


async def test_restore_backup_rejects_path_traversal(backup_env, monkeypatch, tmp_path):
    """恶意 tar（../ 路径穿越）必须被拒绝：filter="data" 抛 TarError，落盘文件不存在。"""
    d = _make_backup_dir(backup_env)
    probe = "evil_kbcrm_probe.txt"
    _write_tar(d / "uploads.tar.gz", {f"../{probe}": b"pwned"})

    async def _must_not_run(*args, **kwargs):
        raise AssertionError("tar 解档失败时不应执行 psql 恢复")

    monkeypatch.setattr(backup_svc, "_run", _must_not_run)
    escaped = tmp_path.parent / probe  # 穿越目标在解档根目录之外
    try:
        with pytest.raises(tarfile.TarError):
            await backup_svc.restore_backup(d.name)
        assert not escaped.exists()
    finally:
        escaped.unlink(missing_ok=True)  # 万一防护失效也不留残文件
