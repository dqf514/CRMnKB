"""library_sync 目录同步服务测试：纯函数 + fake session，文件系统用 tmp_path，不连真实 DB。

覆盖：sha256_file 哈希、build_folder_paths 路径拼装（嵌套/孤儿父级）、
backfill_sync_fields 回填（含磁盘文件缺失边界）、mark_updated_and_reparse 重解析、
list_changes 增量变更（含无可读 ID 提前返回）。
"""
import hashlib
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services import library_sync as sync_svc


# ---------- sha256_file / build_folder_paths 纯函数 ----------


def test_sha256_file_matches_hashlib(tmp_path):
    p = tmp_path / "a.bin"
    data = b"hello world" * 100000  # 超过一个读块前的小文件，走单块路径
    p.write_bytes(data)
    assert sync_svc.sha256_file(p) == hashlib.sha256(data).hexdigest()


def test_build_folder_paths_nested_and_orphan():
    folders = [
        SimpleNamespace(id=1, parent_id=None, name="合同"),
        SimpleNamespace(id=2, parent_id=1, name="2026"),
        SimpleNamespace(id=3, parent_id=99, name="孤儿目录"),  # 父级缺失按根处理
    ]
    paths = sync_svc.build_folder_paths(folders)
    assert paths == {1: "合同", 2: "合同/2026", 3: "孤儿目录"}


# ---------- fake session ----------


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: self._rows)

    def all(self):
        return self._rows


class _QueueSession:
    """按调用顺序返回预设结果。"""

    def __init__(self, *results):
        self._queue = [_FakeResult(r) for r in results]

    async def execute(self, stmt):
        assert self._queue, "execute 调用次数超出预设"
        return self._queue.pop(0)


# ---------- backfill_sync_fields ----------


async def test_backfill_fills_hash_and_updated_at(tmp_path):
    real = tmp_path / "real.txt"
    real.write_bytes(b"abc")
    created = datetime(2026, 1, 1, 8, 0, 0)
    f1 = SimpleNamespace(
        id=1, file_path=str(real), content_hash=None, updated_at=None, created_at=created
    )
    # 磁盘文件缺失：hash 保持 None，但 updated_at 仍回填
    f2 = SimpleNamespace(
        id=2,
        file_path=str(tmp_path / "gone.txt"),
        content_hash=None,
        updated_at=None,
        created_at=created,
    )
    # 字段齐全的行不应被查出（查询条件即 NULL 过滤，这里不再传入）
    session = _QueueSession([f1, f2])

    await sync_svc.backfill_sync_fields(session)

    assert f1.content_hash == hashlib.sha256(b"abc").hexdigest()
    assert f1.updated_at == created
    assert f2.content_hash is None
    assert f2.updated_at == created


async def test_backfill_noop_when_nothing_pending():
    session = _QueueSession([])
    await sync_svc.backfill_sync_fields(session)  # 无异常即通过


# ---------- mark_updated_and_reparse ----------


async def test_mark_updated_and_reparse(tmp_path, monkeypatch):
    monkeypatch.setattr(sync_svc, "is_supported", lambda name: True)
    file = SimpleNamespace(
        id=10, tenant_id=1, file_name="报价单.txt",
        file_size=1, content_hash="old", updated_at=None, supported=False,
    )
    doc_ok = SimpleNamespace(id=100, status="done")
    doc_processing = SimpleNamespace(id=101, status="processing")  # 查询已排除，不应出现
    db = _QueueSession([doc_ok])

    ids = await sync_svc.mark_updated_and_reparse(db, file, file_size=123, content_hash="newhash")

    assert ids == [100]
    assert doc_ok.status == "processing"
    assert file.file_size == 123
    assert file.content_hash == "newhash"
    assert file.supported is True  # is_supported 变化时刷新
    assert file.updated_at is not None
    assert doc_processing.status == "processing"  # 未被触碰


# ---------- list_changes ----------


def _user():
    return SimpleNamespace(id=1, tenant_id=1, role="user", name="用户", username="u")


async def test_list_changes_returns_modified_and_deleted(monkeypatch):
    monkeypatch.setattr(sync_svc, "accessible_ids", _async_ret([1, 2]))
    now = datetime(2026, 9, 1, 12, 0, 0)
    modified = SimpleNamespace(
        id=1, folder_id=2, file_name="方案.docx", content_hash="h1",
        file_size=100, deleted_at=None, updated_at=now,
    )
    deleted = SimpleNamespace(
        id=2, folder_id=None, file_name="旧表.xlsx", content_hash="h2",
        file_size=200, deleted_at=now, updated_at=now,
    )
    folders = [
        SimpleNamespace(id=1, parent_id=None, name="客户资料", tenant_id=1),
        SimpleNamespace(id=2, parent_id=1, name="A客户", tenant_id=1),
    ]
    db = _QueueSession([modified, deleted], folders)

    result = await sync_svc.list_changes(db, _user(), since=now - timedelta(days=1))

    items = {i["id"]: i for i in result["items"]}
    assert items[1]["path"] == "客户资料/A客户/方案.docx"
    assert items[1]["action"] == "modified"
    assert items[2]["path"] == "旧表.xlsx"  # 根目录无前缀
    assert items[2]["action"] == "deleted"
    assert result["server_time"] is not None


async def test_list_changes_short_circuits_without_access(monkeypatch):
    """无可读文件：直接返回空，不查文件表。"""
    monkeypatch.setattr(sync_svc, "accessible_ids", _async_ret([]))
    db = _QueueSession()  # 任何 execute 调用都会断言失败
    result = await sync_svc.list_changes(db, _user(), since=datetime(2026, 1, 1))
    assert result["items"] == []
    assert result["server_time"] is not None


def _async_ret(value):
    async def _f(*args, **kwargs):
        return value
    return _f
