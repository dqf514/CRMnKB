"""RAG sources 携带 library 文件信息（attach_file_info）的单测。"""
from types import SimpleNamespace

from app.schemas.rag import RagSource
from app.services.rag import attach_file_info


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    """队列式 execute：按调用顺序弹出预置行集。"""

    def __init__(self, result_sets):
        self._queue = list(result_sets)

    async def execute(self, stmt, params=None):
        return _FakeResult(self._queue.pop(0) if self._queue else [])


def _source(doc_id):
    return {
        "chunk_id": 1,
        "doc_id": doc_id,
        "doc_title": "某文档",
        "score": 0.9,
        "excerpt": "摘录",
    }


async def test_attach_file_info_with_active_file():
    """doc 带 file_id 且文件未软删：四个 file_* 字段正确回填。"""
    db = _FakeSession(
        [
            [SimpleNamespace(id=10, file_id=7, file_name="产品手册.pdf", file_type="pdf")],
            [SimpleNamespace(id=7, file_size=2048)],
        ]
    )
    sources = [_source(10)]

    await attach_file_info(db, sources)

    assert sources[0]["file_id"] == 7
    assert sources[0]["file_name"] == "产品手册.pdf"
    assert sources[0]["file_type"] == "pdf"
    assert sources[0]["file_size"] == 2048
    # 序列化为 RagSource 时字段齐全
    parsed = RagSource(**sources[0])
    assert parsed.file_id == 7
    assert parsed.file_size == 2048


async def test_attach_file_info_without_file_id():
    """doc.file_id 为 NULL（非 library 来源文档）：file_* 全为 None，doc_title 保留。"""
    db = _FakeSession(
        [
            [SimpleNamespace(id=11, file_id=None, file_name="手工录入.txt", file_type="txt")],
        ]
    )
    sources = [_source(11)]

    await attach_file_info(db, sources)

    s = sources[0]
    assert s["doc_title"] == "某文档"
    assert s["file_id"] is None
    assert s["file_name"] is None
    assert s["file_type"] is None
    assert s["file_size"] is None
    parsed = RagSource(**s)
    assert parsed.file_id is None


async def test_attach_file_info_soft_deleted_file():
    """文件已软删（sizes 查询返回空）：file_* 全为 None，doc_title 保留。"""
    db = _FakeSession(
        [
            [SimpleNamespace(id=12, file_id=9, file_name="旧手册.pdf", file_type="pdf")],
            [],  # LibraryFile 查询带 deleted_at.is_(None) 过滤，软删文件查不到
        ]
    )
    sources = [_source(12)]

    await attach_file_info(db, sources)

    s = sources[0]
    assert s["doc_title"] == "某文档"
    assert s["file_id"] is None
    assert s["file_name"] is None
    assert s["file_type"] is None
    assert s["file_size"] is None


async def test_attach_file_info_empty_sources():
    """空 sources 直接返回，不触发任何查询。"""
    db = _FakeSession([])
    sources = []
    await attach_file_info(db, sources)
    assert sources == []
