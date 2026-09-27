"""PR-G：RAG file_ids 文件级 scope 过滤测试。"""
from app.services import rag as rag_module


class _FakeResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def mappings(self):
        return _FakeMappings(self._rows)


class _FakeMappings:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    """记录 execute SQL，验证 file_ids 过滤拼到 SQL。"""

    def __init__(self, rows=None):
        self._rows = rows or []
        self.calls = []

    async def execute(self, sql, params=None):
        self.calls.append({"sql": str(sql), "params": params or {}})
        return _FakeResult(self._rows)


# ---------- search_chunks_vector ----------


async def test_search_chunks_vector_no_scope_no_filter():
    db = _FakeSession()
    await rag_module.search_chunks_vector(db, tenant_id=1, query_vec=[0.1, 0.2], limit=5)
    sql = db.calls[0]["sql"]
    # 没有 file_ids / kb_ids 时不应拼 ANY 过滤（base SQL 里 "kb_id NOT IN" 用于排除软删 KB 是正常的）
    assert "= ANY" not in sql
    assert "file_ids" not in db.calls[0]["params"]
    assert "kb_ids" not in db.calls[0]["params"]


async def test_search_chunks_vector_kb_ids_only():
    db = _FakeSession()
    await rag_module.search_chunks_vector(
        db, tenant_id=1, query_vec=[0.1], limit=5, kb_ids=[1, 2]
    )
    sql = db.calls[0]["sql"]
    assert "kb_id = ANY" in sql
    # 只给 kb_ids 时不拼 file_ids 的 ANY 过滤（base SQL 里 file_id NOT IN 是软删文件过滤，属正常）
    assert "file_id = ANY" not in sql
    assert db.calls[0]["params"]["kb_ids"] == [1, 2]


async def test_search_chunks_vector_file_ids_overrides_kb_ids():
    db = _FakeSession()
    await rag_module.search_chunks_vector(
        db, tenant_id=1, query_vec=[0.1], limit=5, kb_ids=[1, 2], file_ids=[10, 20]
    )
    sql = db.calls[0]["sql"]
    # file_ids 优先：应有 file_id 过滤，没有 kb_id 过滤
    assert "file_id = ANY" in sql
    assert "kb_id = ANY" not in sql
    assert db.calls[0]["params"]["file_ids"] == [10, 20]
    assert "kb_ids" not in db.calls[0]["params"]


async def test_search_chunks_vector_file_ids_only():
    db = _FakeSession()
    await rag_module.search_chunks_vector(
        db, tenant_id=1, query_vec=[0.1], limit=5, file_ids=[10]
    )
    sql = db.calls[0]["sql"]
    assert "file_id = ANY" in sql
    assert db.calls[0]["params"]["file_ids"] == [10]


# ---------- search_chunks_keyword ----------


async def test_search_chunks_keyword_file_ids():
    db = _FakeSession()
    await rag_module.search_chunks_keyword(
        db, tenant_id=1, question="q", limit=5, file_ids=[7]
    )
    sql = db.calls[0]["sql"]
    assert "file_id = ANY" in sql
    assert "kb_id = ANY" not in sql


# ---------- search_question_chunks ----------


async def test_search_question_chunks_file_ids():
    db = _FakeSession()
    await rag_module.search_question_chunks(
        db, tenant_id=1, query_vec=[0.1], limit=5, file_ids=[7, 8]
    )
    sql = db.calls[0]["sql"]
    assert "file_id = ANY" in sql
    assert db.calls[0]["params"]["file_ids"] == [7, 8]


# ---------- search_chunks_blend file_ids ----------


async def test_search_chunks_blend_file_ids_in_vector_stage():
    db = _FakeSession()
    # 第一阶段返回空 → 跳过 BM25 阶段
    await rag_module.search_chunks_blend(
        db, tenant_id=1, query_vec=[0.1], question="q", limit=5,
        score_threshold=0.5, file_ids=[42],
    )
    # 第一阶段 SQL 应有 file_id 过滤
    sql1 = db.calls[0]["sql"]
    assert "file_id = ANY" in sql1
    assert db.calls[0]["params"]["file_ids"] == [42]


# PR-H 回归保护：asyncpg 不接受 `::type` 语法（与 `:param` 占位符冲突）
async def test_search_chunks_vector_no_double_colon():
    """所有 RAG SQL 都不能含 `::` 字符串。"""
    db = _FakeSession()
    await rag_module.search_chunks_vector(db, tenant_id=1, query_vec=[0.1], limit=5)
    assert "::" not in db.calls[0]["sql"]


async def test_search_chunks_keyword_no_double_colon():
    db = _FakeSession()
    await rag_module.search_chunks_keyword(db, tenant_id=1, question="q", limit=5)
    assert "::" not in db.calls[0]["sql"]


async def test_search_question_chunks_no_double_colon():
    db = _FakeSession()
    await rag_module.search_question_chunks(db, tenant_id=1, query_vec=[0.1], limit=5)
    assert "::" not in db.calls[0]["sql"]


async def test_search_chunks_blend_full_pipeline_no_double_colon():
    """blend 阶段 1 + 阶段 2 SQL 都用 CAST(:name AS type)，不能含 `::`。

    复用了 test_blend_search.py::test_blend_no_double_colon_cast_syntax 的覆盖（已包含两阶段）。
    此处不再重复，仅作为占位提示。
    """
    pass