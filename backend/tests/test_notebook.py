"""PR-G：notebook + note CRUD 测试 + save-as-document + from-chat-message。"""
from types import SimpleNamespace

import pytest

from app.services import notebook as nb_service


class _FakeSession:
    def __init__(self):
        self.added = []
        self.deleted = []
        self.committed = False
        self._store: dict[int, object] = {}
        self._next_id = 1

    def add(self, obj):
        self.added.append(obj)
        if getattr(obj, "id", None) is None:
            obj.id = self._next_id
            self._next_id += 1

    async def get(self, model, ident):
        if model.__name__ == "Notebook":
            return self._store.get(("nb", ident))
        if model.__name__ == "NotebookNote":
            return self._store.get(("note", ident))
        if model.__name__ == "KnowledgeBase":
            return self._store.get(("kb", ident))
        return None

    async def commit(self):
        self.committed = True

    async def flush(self):
        pass

    async def refresh(self, obj):
        pass

    async def execute(self, stmt, params=None):
        # 简易支持 COUNT + JOIN Notebook/Note → 返回 [()]；兼容 .scalars().all()
        class _R:
            def all(self):
                return []
            def scalars(self):
                return self
        return _R()

    async def delete(self, obj):
        self.deleted.append(obj)


# ---------- list_notebooks ----------


async def test_list_notebooks_empty():
    db = _FakeSession()
    user = SimpleNamespace(id=1, tenant_id=1, role="admin")
    rows = await nb_service.list_notebooks(db, user)
    assert rows == []


# ---------- create_notebook ----------


async def test_create_notebook_sets_defaults():
    db = _FakeSession()
    nb = await nb_service.create_notebook(db, tenant_id=1, user_id=10, name="我的研究")
    assert nb.tenant_id == 1
    assert nb.created_by == 10
    assert nb.note_count == 0
    assert nb in db.added


# ---------- update_notebook ----------


async def test_update_notebook_changes_name_and_description():
    db = _FakeSession()
    nb = SimpleNamespace(id=1, tenant_id=1, name="old", description="d", updated_at=None)
    nb = await nb_service.update_notebook(db, nb, name="new", description="new d")
    assert nb.name == "new"
    assert nb.description == "new d"


# ---------- notes CRUD ----------


async def test_create_note_links_to_notebook():
    db = _FakeSession()
    note = await nb_service.create_note(
        db, tenant_id=1, notebook_id=5, title="t1", content="c1"
    )
    assert note.notebook_id == 5
    assert note.tenant_id == 1
    assert note.title == "t1"


async def test_update_note_modifies_fields():
    db = _FakeSession()
    note = SimpleNamespace(
        id=1, tenant_id=1, notebook_id=5, title="old", content="old", sort=0,
        updated_at=None,
    )
    note = await nb_service.update_note(db, note, title="new", content="new c", sort=2)
    assert note.title == "new"
    assert note.content == "new c"
    assert note.sort == 2


# ---------- create_note_from_chat ----------


async def test_create_note_from_chat_truncates_title_and_includes_sources():
    db = _FakeSession()
    note = await nb_service.create_note_from_chat(
        db, tenant_id=1, user_id=10, notebook_id=5,
        session_id=99, query_log_id=42,
        question="这是一个很长很长的问题标题应该截断三十字",
        answer="简短回答",
        sources=[{"doc_title": "doc1", "chunk_id": 1, "score": 0.9}],
    )
    # title 取 question 前 30 字
    assert len(note.title) <= 30
    # content 含 Markdown 标题 + 问题 + 回答 + 引用
    assert "## 问题" in note.content
    assert "## 回答" in note.content
    assert "简短回答" in note.content
    assert "doc1" in note.content
    assert note.source_type == "chat"
    assert note.source_ref["session_id"] == 99
    assert note.source_ref["query_log_id"] == 42


async def test_create_note_from_chat_empty_question_uses_default_title():
    db = _FakeSession()
    note = await nb_service.create_note_from_chat(
        db, tenant_id=1, user_id=10, notebook_id=5,
        session_id=None, query_log_id=None,
        question="",
        answer="a",
        sources=[],
    )
    assert note.title == "未命名问答"


# ---------- save_note_as_document ----------


async def test_save_note_as_document_creates_processing_doc(monkeypatch, tmp_path):
    """PR-G-2：note → KB document → 后台 ingestion。"""
    # 跳过真实 asyncio.create_task 启动 ingestion；上传目录指向临时目录
    import asyncio

    from app.config import settings

    monkeypatch.setattr(type(settings), "upload_path", property(lambda self: tmp_path))
    fake_task = SimpleNamespace()
    monkeypatch.setattr(
        asyncio, "create_task", lambda coro: fake_task
    )

    db = _FakeSession()
    # 预存 KB
    kb = SimpleNamespace(id=7, tenant_id=1, name="kb7")
    db._store[("kb", 7)] = kb
    note = SimpleNamespace(
        id=1, tenant_id=1, notebook_id=5, title="My Note", content="# Title\n\ncontent",
        source_ref=None, source_type="manual", sort=0,
    )

    result = await nb_service.save_note_as_document(
        db, tenant_id=1, user_id=10, note=note, kb_id=7
    )
    assert result["kb_id"] == 7
    assert result["status"] == "processing"
    assert "document_id" in result
    # note.source_ref 被更新
    assert note.source_ref["saved_as_document_id"] is not None
    assert note.source_ref["saved_as_kb_id"] == 7


async def test_save_note_as_document_wrong_kb_raises_404(monkeypatch):
    import asyncio
    monkeypatch.setattr(asyncio, "create_task", lambda coro: None)
    db = _FakeSession()
    note = SimpleNamespace(id=1, tenant_id=1, notebook_id=5, title="t", content="c")
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await nb_service.save_note_as_document(
            db, tenant_id=1, user_id=10, note=note, kb_id=999
        )
    assert exc.value.status_code == 404

# ---------------------------------------------------------------------------
# 工作区来源净化：过滤已删除/不存在的 KB 与文件引用
# ---------------------------------------------------------------------------

class _ExecuteQueueSession:
    def __init__(self, queues):
        self._queues = queues  # list of list-of-rows

    async def execute(self, stmt, params=None):
        rows = self._queues.pop(0) if self._queues else []
        class _R:
            def all(self):
                return rows
        return _R()


def test_sanitize_sources_drops_deleted_kb():
    from app.api.notebooks import _sanitize_sources
    import asyncio

    db = _ExecuteQueueSession([
        [(3,)],  # KnowledgeBase IN (2,3) 存活 → 只有 3
        [],      # LibraryFile 无存活
    ])
    kb_ids, file_ids = asyncio.run(_sanitize_sources(db, 1, [2, 3], [5]))
    assert kb_ids == [3]   # 失效的 2 被剔除
    assert file_ids == []


def test_sanitize_sources_all_invalid():
    from app.api.notebooks import _sanitize_sources
    import asyncio

    db = _ExecuteQueueSession([
        [],  # KB 全失效/删除
        [(7,)],  # 文件 7 存活
    ])
    kb_ids, file_ids = asyncio.run(_sanitize_sources(db, 1, [12], [7]))
    assert kb_ids == []
    assert file_ids == [7]
