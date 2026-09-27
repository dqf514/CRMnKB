import pytest

from app.services.rag import reciprocal_rank_fusion


def _item(cid, score=0.5, doc_id=1):
    return {
        "chunk_id": cid,
        "doc_id": doc_id,
        "chunk_index": cid,
        "content": f"内容{cid}",
        "doc_title": "文档",
        "score": score,
    }


def test_empty_lists():
    assert reciprocal_rank_fusion([], []) == []


def test_single_list_preserves_order():
    ranked = [_item(1), _item(2), _item(3)]
    fused = reciprocal_rank_fusion(ranked)
    assert [r["chunk_id"] for r in fused] == [1, 2, 3]


def test_item_in_both_lists_ranks_first():
    vector = [_item(1), _item(2), _item(3)]
    keyword = [_item(3), _item(9)]
    fused = reciprocal_rank_fusion(vector, keyword)
    # chunk 3 同时出现在两个列表，RRF 分数最高
    assert fused[0]["chunk_id"] == 3
    # 只在一个列表高位的 chunk 1 次之
    assert fused[1]["chunk_id"] == 1


def test_rrf_score_formula():
    fused = reciprocal_rank_fusion([_item(1), _item(2)], [_item(2)])
    by_id = {r["chunk_id"]: r for r in fused}
    assert by_id[1]["rrf_score"] == pytest.approx(1 / 61)
    assert by_id[2]["rrf_score"] == pytest.approx(1 / 62 + 1 / 61)


def test_keeps_max_original_score_when_merged():
    vector = [_item(1, score=0.4)]
    keyword = [_item(1, score=0.9)]
    fused = reciprocal_rank_fusion(vector, keyword)
    assert fused[0]["score"] == 0.9


def test_custom_k():
    fused = reciprocal_rank_fusion([_item(1)], k=10)
    assert fused[0]["rrf_score"] == pytest.approx(1 / 11)
