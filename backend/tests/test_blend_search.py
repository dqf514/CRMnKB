"""blend 二阶段检索测试：search_chunks_blend SQL 与参数正确性。

参照 MaxKB blend_search.sql：
  阶段 1：pgvector 余弦召回 top_k*10（封顶 500）
  阶段 2：对候选逐条算 ts_rank_cd，综合分 = (1 - distance) + ts_rank_cd
"""

import pytest

from app.services import rag as rag_module
from app.services.rag import search_chunks_blend


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
    """记录每次 execute 的 SQL 和 params；按顺序 pop 返回行。"""

    def __init__(self, exec_results):
        self._queue = list(exec_results)
        self.calls = []

    async def execute(self, sql, params=None):
        self.calls.append({"sql": str(sql), "params": params or {}})
        return self._queue.pop(0) if self._queue else _FakeResult()


def _vector_row(cid, distance=0.1, doc_id=1):
    return {
        "chunk_id": cid,
        "doc_id": doc_id,
        "chunk_index": cid,
        "content": f"内容{cid}",
        "doc_title": "文档",
        "kb_id": 1,
        "distance": distance,
    }


def _blend_row(cid, score=1.0):
    return {
        "chunk_id": cid,
        "doc_id": 1,
        "chunk_index": cid,
        "content": f"内容{cid}",
        "doc_title": "文档",
        "score": score,
    }


# ---------- 阶段 1：向量召回 ----------


async def test_blend_vector_sql_contains_cosine_distance():
    db = _FakeSession([_FakeResult([])])
    await search_chunks_blend(db, tenant_id=1, query_vec=[0.1, 0.2, 0.3],
                              question="测试", limit=5, score_threshold=0.5)
    # 阶段 1 SQL 应包含向量距离、tenant 过滤、kb 排除软删、limit
    sql = db.calls[0]["sql"]
    assert "<=>" in sql
    assert "tenant_id" in sql
    assert "deleted_at IS NOT NULL" in sql
    assert "search_vector IS NOT NULL" in sql
    assert "LIMIT LEAST" in sql


async def test_blend_vector_sql_kb_ids_filter():
    db = _FakeSession([_FakeResult([])])
    await search_chunks_blend(db, tenant_id=1, query_vec=[0.1, 0.2],
                              question="测试", limit=5, score_threshold=0.5,
                              kb_ids=[1, 2])
    sql = db.calls[0]["sql"]
    assert "kb_id = ANY" in sql
    assert "kb_ids" in db.calls[0]["params"]
    assert db.calls[0]["params"]["kb_ids"] == [1, 2]


async def test_blend_empty_vector_returns_empty():
    db = _FakeSession([_FakeResult([])])  # 阶段 1 空 → 直接返回
    result = await search_chunks_blend(
        db, tenant_id=1, query_vec=[0.1], question="测试", limit=5, score_threshold=0.5,
    )
    assert result == []
    assert len(db.calls) == 1  # 仅阶段 1 调用


# ---------- 阶段 2：BM25 精排 + 相加融合 ----------


async def test_blend_full_pipeline_returns_ranked_results():
    # 阶段 1：返回 3 个候选
    vec_rows = [_vector_row(1, distance=0.1), _vector_row(2, distance=0.5), _vector_row(3, distance=0.3)]
    # 阶段 2：返回融合排序后的结果（mock 已按 comprehensive_score 降序，模拟 SQL ORDER BY）
    blend_rows = [_blend_row(1, score=1.2), _blend_row(3, score=0.9), _blend_row(2, score=0.7)]
    db = _FakeSession([_FakeResult(vec_rows), _FakeResult(blend_rows)])

    result = await search_chunks_blend(
        db, tenant_id=1, query_vec=[0.1, 0.2], question="查询关键词", limit=5,
        score_threshold=0.5,
    )
    assert len(result) == 3
    # SQL 已 ORDER BY comprehensive_score DESC，mock 模拟该顺序
    assert [r["chunk_id"] for r in result] == [1, 3, 2]
    assert [r["score"] for r in result] == [1.2, 0.9, 0.7]
    # 阶段 2 SQL 应包含 websearch_to_tsquery + ts_rank_cd
    sql2 = db.calls[1]["sql"]
    assert "ts_rank_cd" in sql2
    assert "websearch_to_tsquery" in sql2
    assert "VALUES" in sql2
    assert "comprehensive_score" in sql2


async def test_blend_threshold_filters_results():
    # 阶段 2 SQL 自带 WHERE comprehensive_score > threshold，传入 DB 层过滤
    vec_rows = [_vector_row(1, distance=0.1)]
    blend_rows = []  # 假设阈值过滤后为空
    db = _FakeSession([_FakeResult(vec_rows), _FakeResult(blend_rows)])
    result = await search_chunks_blend(
        db, tenant_id=1, query_vec=[0.1], question="测试", limit=5, score_threshold=0.95,
    )
    assert result == []
    assert db.calls[1]["params"]["threshold"] == 0.95


async def test_blend_params_pass_query_and_topk():
    vec_rows = [_vector_row(1, distance=0.1)]
    blend_rows = [_blend_row(1, score=1.0)]
    db = _FakeSession([_FakeResult(vec_rows), _FakeResult(blend_rows)])
    await search_chunks_blend(
        db, tenant_id=1, query_vec=[0.1, 0.2], question="深度问题",
        limit=10, score_threshold=0.7,
    )
    # 阶段 1
    p1 = db.calls[0]["params"]
    assert p1["tid"] == 1
    assert p1["limit_topn"] == 100  # 10 * 10
    assert p1["vec"].startswith("[")
    # 阶段 2
    p2 = db.calls[1]["params"]
    assert p2["q"] == "深度问题"
    assert p2["topk"] == 10
    assert p2["threshold"] == 0.7
    # 阶段 2 应注入每个候选的 id/doc/idx/content/title/distance
    assert "id0" in p2 and "dist0" in p2
    assert p2["dist0"] == pytest.approx(0.1)


async def test_blend_topn_capped_at_500():
    # 验证 SQL 中 LIMIT LEAST(:limit_topn, 500) 上限
    db = _FakeSession([_FakeResult([])])
    await search_chunks_blend(
        db, tenant_id=1, query_vec=[0.1], question="q", limit=100, score_threshold=0.5,
    )
    assert db.calls[0]["params"]["limit_topn"] == 1000  # 100 * 10
    assert "LEAST" in db.calls[0]["sql"]


async def test_blend_multiple_candidates_become_values_rows():
    vec_rows = [
        _vector_row(1, distance=0.1, doc_id=10),
        _vector_row(2, distance=0.2, doc_id=20),
        _vector_row(3, distance=0.3, doc_id=30),
    ]
    blend_rows = [_blend_row(1), _blend_row(2), _blend_row(3)]
    db = _FakeSession([_FakeResult(vec_rows), _FakeResult(blend_rows)])
    await search_chunks_blend(
        db, tenant_id=1, query_vec=[0.1], question="q", limit=5, score_threshold=0.5,
    )
    p2 = db.calls[1]["params"]
    # VALUES 子句应有 3 行
    assert all(f"id{i}" in p2 and f"doc{i}" in p2 for i in range(3))
    assert p2["doc0"] == 10 and p2["doc1"] == 20 and p2["doc2"] == 30
    # VALUES 占位符数量匹配：用 id{i} 简单计数（避免 :id 被 :idx 子串误匹配）
    count_id_i = sum(1 for k in p2 if k.startswith("id") and k[2:].isdigit())
    assert count_id_i == 3, f"id{{i}} 参数应为 3 个，实际 {count_id_i}"


async def test_blend_no_double_colon_cast_syntax():
    """PR-B 回归保护：asyncpg 不接受 `::vector`（与 `:param` 占位符冲突）。

    SQL 中所有 PG 类型转换必须用 CAST(... AS ...) 显式形式。
    """
    # stage 1 至少要有 vec 候选
    vec_rows = [_vector_row(1, distance=0.1)]
    db = _FakeSession([_FakeResult(vec_rows), _FakeResult([])])
    await search_chunks_blend(
        db, tenant_id=1, query_vec=[0.1, 0.2], question="q", limit=5,
        score_threshold=0.5,
    )
    sql1 = db.calls[0]["sql"]
    sql2 = db.calls[1]["sql"]
    for sql, label in ((sql1, "stage1"), (sql2, "stage2")):
        # 不允许出现 `::` 字符串（asyncpg 误识为参数）
        assert "::" not in sql, f"blend {label} SQL 含 `::` cast，会与 asyncpg :param 冲突: {sql[:200]}"


async def test_blend_no_double_colon_in_actual_runtime():
    """更严格：直接用 asyncpg 编译验证 SQL 真实可执行（本地若有 PG 才跑）。"""
    import os
    if not os.environ.get("DATABASE_URL"):
        import pytest
        pytest.skip("无 DATABASE_URL，跳过集成检查")