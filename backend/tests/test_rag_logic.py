from app.services.rag import (
    FALLBACK_ANSWER,
    build_prompt,
    decide_grounded,
    merge_windows,
    reciprocal_rank_fusion,
)


def test_empty_scores_not_grounded():
    assert decide_grounded([], 0.7) is False


def test_below_threshold_not_grounded():
    assert decide_grounded([0.5, 0.65, 0.69], 0.7) is False


def test_at_or_above_threshold_grounded():
    assert decide_grounded([0.5, 0.7], 0.7) is True
    assert decide_grounded([0.95], 0.7) is True


def test_fallback_answer_text():
    assert "转人工" in FALLBACK_ANSWER


def test_build_prompt_structure():
    chunks = [
        {"doc_title": "产品手册", "content": "产品支持七天无理由退货。"},
        {"doc_title": "售后政策", "content": "退货需保留原包装。"},
    ]
    messages = build_prompt("退货政策是什么？", chunks)
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "不知道" in messages[0]["content"]
    user_msg = messages[1]["content"]
    assert "[1]" in user_msg and "[2]" in user_msg
    assert "产品手册" in user_msg
    assert "退货政策是什么？" in user_msg


# ---------- 混合检索：RRF 融合后的兜底决策 ----------

def _hit(cid, score, doc_id=1):
    return {
        "chunk_id": cid,
        "doc_id": doc_id,
        "chunk_index": cid,
        "content": f"内容{cid}",
        "doc_title": "文档",
        "score": score,
    }


def test_hybrid_decision_below_threshold_falls_back():
    # 向量与关键词结果融合后，最高原始相似度仍低于阈值 → 不 grounded
    vector_rows = [_hit(1, 0.65), _hit(2, 0.5)]
    keyword_rows = [_hit(2, 0.4), _hit(3, 0.3)]
    fused = reciprocal_rank_fusion(vector_rows, keyword_rows)
    scores = [float(r["score"]) for r in fused]
    assert decide_grounded(scores, 0.7) is False


def test_hybrid_decision_above_threshold_grounded():
    vector_rows = [_hit(1, 0.85)]
    keyword_rows = [_hit(2, 0.2)]
    fused = reciprocal_rank_fusion(vector_rows, keyword_rows)
    scores = [float(r["score"]) for r in fused]
    assert decide_grounded(scores, 0.7) is True


# ---------- Small2Big：相邻窗口合并 ----------

def test_merge_windows_merges_overlapping_and_adjacent():
    hits = [
        {"doc_id": 1, "chunk_index": 5},
        {"doc_id": 1, "chunk_index": 6},
        {"doc_id": 1, "chunk_index": 20},
    ]
    windows = merge_windows(hits, window=1)
    # 相邻命中（5、6）的 ±1 窗口合并为 (4,7)，只展开一次；20 独立
    assert windows[1] == [(4, 7), (19, 21)]


def test_merge_windows_clamps_at_zero():
    assert merge_windows([{"doc_id": 2, "chunk_index": 0}], window=2) == {2: [(0, 2)]}


def test_merge_windows_separate_docs():
    hits = [
        {"doc_id": 1, "chunk_index": 3},
        {"doc_id": 2, "chunk_index": 3},
    ]
    windows = merge_windows(hits, window=1)
    assert windows == {1: [(2, 4)], 2: [(2, 4)]}
