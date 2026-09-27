"""PST 流式拆解入库：编排逻辑测试（假解析器替代真实 PST 二进制）。"""
from types import SimpleNamespace

import pytest

from app.services import pst as pst_module
from app.services.pst import process_pst


class _FakeSession:
    """最小 Session 假件：get/add/flush/commit/execute 按调用顺序返回预置结果。"""

    def __init__(self, doc, file):
        self.doc = doc
        self.file = file
        self.added = []
        self.commits = 0
        self._queue = []
        # _get_or_create_pst_folder 的查询 → 首次返回 None（新建）
        self._queue.append(SimpleNamespace(scalar_one_or_none=lambda: None))
        self._queue_idx = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, model, ident):
        name = getattr(model, "__name__", "")
        if name == "KnowledgeDocument":
            return self.doc
        if name == "LibraryFile":
            return self.file
        return None

    def add(self, obj):
        self.added.append(obj)
        # 模拟 flush 后分配 id
        if getattr(obj, "id", None) is None:
            obj.id = 1000 + len(self.added)

    async def flush(self):
        pass

    async def commit(self):
        self.commits += 1

    async def execute(self, stmt):
        if self._queue_idx < len(self._queue):
            r = self._queue[self._queue_idx]
            self._queue_idx += 1
            return r
        return SimpleNamespace(scalar_one_or_none=lambda: None, scalars=lambda: SimpleNamespace(all=lambda: []))


@pytest.fixture
def fake_env(monkeypatch, tmp_path):
    doc = SimpleNamespace(
        id=1, tenant_id=1, file_id=1, kb_id=10, file_path=str(tmp_path / "a.pst"),
        file_name="工作邮件.pst", file_type="pst", status="processing",
        doc_metadata={}, content=None, chunk_count=0,
    )
    file = SimpleNamespace(
        id=1, tenant_id=1, folder_id=None, file_name="工作邮件.pst",
        owner_id=5, is_private=True,
    )
    (tmp_path / "a.pst").write_bytes(b"fake-pst")
    # upload_path 是 property（由 UPLOAD_DIR 派生），改字段让 property 指向临时目录
    monkeypatch.setattr(pst_module.settings, "UPLOAD_DIR", str(tmp_path))

    sessions: list[_FakeSession] = []

    def _session_factory():
        s = _FakeSession(doc, file)
        sessions.append(s)
        return s

    monkeypatch.setattr(pst_module, "AsyncSessionLocal", _session_factory)
    # 假 pypff 解析器：产出 3 封邮件
    monkeypatch.setattr(pst_module, "_pypff_available", lambda: True)
    monkeypatch.setattr(pst_module, "_count_pypff", lambda p: 3)

    def _fake_iter(path):
        for i in range(3):
            yield f"主题{i}", f"Subject: 主题{i}\n\n正文{i}".encode()

    monkeypatch.setattr(pst_module, "_iter_pypff_eml", _fake_iter)
    # 邮件解析任务直接 noop（不打 LLM）
    async def _noop(cid):
        return None

    import app.services.ingestion as ingestion

    monkeypatch.setattr(ingestion, "process_document", _noop)
    return doc, file, sessions


async def test_process_pst_streams_and_completes(fake_env):
    doc, file, sessions = fake_env
    await process_pst(1)

    assert doc.status == "ready"
    assert "3 封邮件" in (doc.content or "")
    assert doc.doc_metadata["pst_state"] == "done"
    assert doc.doc_metadata["pst_done"] == 3
    assert doc.doc_metadata["pst_total"] == 3
    # 每个会话里新增的对象：文件夹 + 3 个文件 + 3 个邮件文档
    files = [o for s in sessions for o in s.added if o.__class__.__name__ == "LibraryFile"]
    docs = [o for s in sessions for o in s.added if o.__class__.__name__ == "KnowledgeDocument"]
    folders = [o for s in sessions for o in s.added if o.__class__.__name__ == "LibraryFolder"]
    assert len(files) == 3
    assert len(docs) == 3
    assert len(folders) == 1
    assert folders[0].name == "工作邮件"
    assert all(f.folder_id == folders[0].id for f in files)
    assert all(d.status == "processing" and d.kb_id == 10 for d in docs)
    assert all(f.owner_id == 5 and f.is_private for f in files)


async def test_process_pst_missing_parser_marks_failed(fake_env, monkeypatch):
    doc, _, _ = fake_env
    monkeypatch.setattr(pst_module, "_pypff_available", lambda: False)
    monkeypatch.setattr(pst_module, "_readpst_available", lambda: False)

    async def _no_log(*a, **kw):
        return None

    import app.services.error_log as error_log

    monkeypatch.setattr(error_log, "log_error", _no_log)
    await process_pst(1)
    assert doc.status == "failed"
    assert doc.doc_metadata["pst_state"] == "failed"
    assert "不可用" in doc.doc_metadata["error"]
