"""maintenance 维护服务测试：asyncpg 打桩 + tmp_path 文件系统，不连真实 PG。

覆盖：VACUUM / REINDEX 的 SQL 与顺序、asyncpg 失败静默兜底、孤儿文件清理
（孤儿删除 / 已知保留 / 子目录跳过 / 目录不存在）。
"""
from types import SimpleNamespace

import pytest

from app.config import settings
from app.services import maintenance


class _FakeConn:
    def __init__(self, executed):
        self.executed = executed
        self.closed = False

    async def execute(self, sql):
        self.executed.append(sql)

    async def close(self):
        self.closed = True


@pytest.fixture
def fake_asyncpg(monkeypatch):
    """打桩 asyncpg.connect，记录执行过的 SQL。"""
    env = SimpleNamespace(executed=[], fail=None)

    async def _fake_connect(dsn, timeout=None, server_settings=None):
        if env.fail is not None:
            raise env.fail
        return _FakeConn(env.executed)

    monkeypatch.setattr(maintenance.asyncpg, "connect", _fake_connect)
    return env


async def test_run_vacuum_executes_vacuum_analyze(fake_asyncpg):
    await maintenance.run_vacuum()
    assert fake_asyncpg.executed == ["VACUUM (ANALYZE)"]


async def test_reindex_rebuilds_vector_and_gin_then_vacuum(fake_asyncpg):
    await maintenance.reindex_vector_index()
    assert fake_asyncpg.executed == [
        "REINDEX INDEX CONCURRENTLY ix_chunks_embedding_hnsw",
        "REINDEX INDEX CONCURRENTLY ix_chunks_search_vector_gin",
        "REINDEX INDEX CONCURRENTLY ix_chunks_content_trgm",
        "VACUUM (ANALYZE)",
    ]


async def test_run_failure_logged_not_raised(fake_asyncpg):
    """连接失败只记日志，不向上抛（后台任务场景）。"""
    fake_asyncpg.fail = OSError("连接被拒绝")
    await maintenance.run_vacuum()  # 不应抛异常
    assert fake_asyncpg.executed == []


# ---------- cleanup_orphan_uploads ----------


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: self._rows)


class _FakeSession:
    """cleanup_orphan_uploads 只用到 execute（查已知 file_path 列表）。"""

    def __init__(self, known_paths):
        self._known = list(known_paths)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, stmt):
        return _FakeResult(self._known)


@pytest.fixture
def upload_dir(tmp_path, monkeypatch):
    """settings.upload_path 是只读 property，打类级桩指向 tmp_path。"""
    d = tmp_path / "uploads"
    d.mkdir()
    cls = type(settings)
    monkeypatch.setattr(cls, "upload_path", property(lambda self: d))
    return d


async def test_cleanup_removes_orphans_keeps_known(upload_dir, monkeypatch):
    known = upload_dir / "known.txt"
    known.write_bytes(b"k" * 10)
    orphan = upload_dir / "orphan.bin"
    orphan.write_bytes(b"o" * 20)
    subdir = upload_dir / "subdir"  # 目录应跳过
    subdir.mkdir()

    monkeypatch.setattr(
        "app.database.AsyncSessionLocal",
        lambda: _FakeSession([str(known)]),
    )

    result = await maintenance.cleanup_orphan_uploads()
    assert result == {"removed": 1, "bytes": 20}
    assert known.exists()          # 已知文件保留
    assert not orphan.exists()     # 孤儿删除
    assert subdir.exists()         # 子目录不动


async def test_cleanup_missing_dir_returns_zero(tmp_path, monkeypatch):
    """上传目录不存在：直接返回零，不查库。"""
    cls = type(settings)
    monkeypatch.setattr(cls, "upload_path", property(lambda self: tmp_path / "nope"))

    def _must_not_open_session():
        raise AssertionError("目录不存在时不应打开 DB 会话")

    monkeypatch.setattr("app.database.AsyncSessionLocal", _must_not_open_session)
    assert await maintenance.cleanup_orphan_uploads() == {"removed": 0, "bytes": 0}
