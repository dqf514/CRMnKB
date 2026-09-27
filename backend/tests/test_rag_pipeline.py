import pytest

from app.services import rag as rag_module
from app.services.rag import (
    parse_scores,
    parse_string_array,
    rerank_chunks,
    rewrite_question,
    trim_blocks,
)


class _FakeLLM:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error

    async def chat(self, messages, **kw):
        if self._error:
            raise self._error
        return self._response


def _candidate(cid, score=0.5):
    return {
        "chunk_id": cid,
        "doc_id": 1,
        "chunk_index": cid,
        "content": f"内容{cid}",
        "doc_title": "文档",
        "score": score,
    }


def _patch_chat_llm(monkeypatch, llm, no_rerank_model=True):
    """五期后 LLM 经异步 resolve_chat_llm 解析，测试统一替换它。

    no_rerank_model=True（默认）：同时让 resolve_rerank_llm 返回 None，
    强制 rerank_chunks 走 chat LLM 打分分支（PR-D 行为）。
    """
    async def _resolve_chat(caller=None, **kw):
        return llm

    monkeypatch.setattr(rag_module, "resolve_chat_llm", _resolve_chat)

    if no_rerank_model:
        async def _resolve_rerank_none(caller=None, **kw):
            return None
        monkeypatch.setattr(rag_module, "resolve_rerank_llm", _resolve_rerank_none)


# ---------- 问题改写（指代消解）降级 ----------

async def test_rewrite_without_history_returns_original():
    assert await rewrite_question("它多少钱？", []) == "它多少钱？"


async def test_rewrite_success(monkeypatch):
    _patch_chat_llm(monkeypatch, _FakeLLM(response="产品价格是多少？"))
    history = [{"role": "user", "content": "介绍一下产品"}, {"role": "assistant", "content": "..."}]
    assert await rewrite_question("它多少钱？", history) == "产品价格是多少？"


async def test_rewrite_llm_failure_falls_back_to_original(monkeypatch):
    _patch_chat_llm(monkeypatch, _FakeLLM(error=RuntimeError("LLM API key 未配置")))
    history = [{"role": "user", "content": "介绍一下产品"}]
    assert await rewrite_question("它多少钱？", history) == "它多少钱？"


async def test_rewrite_empty_response_falls_back(monkeypatch):
    _patch_chat_llm(monkeypatch, _FakeLLM(response="  "))
    history = [{"role": "user", "content": "hi"}]
    assert await rewrite_question("它多少钱？", history) == "它多少钱？"


# ---------- Rerank 解析容错 ----------

async def test_rerank_success_sorts_by_score(monkeypatch):
    _patch_chat_llm(monkeypatch, _FakeLLM(response="[0.2, 0.9, 0.5]"))
    candidates = [_candidate(1), _candidate(2), _candidate(3)]
    reranked = await rerank_chunks("问题", candidates)
    assert [r["chunk_id"] for r in reranked] == [2, 3, 1]
    assert reranked[0]["rerank_score"] == 0.9


async def test_rerank_bad_json_returns_none(monkeypatch):
    _patch_chat_llm(monkeypatch, _FakeLLM(response="我觉得都不错"))
    assert await rerank_chunks("问题", [_candidate(1)]) is None


async def test_rerank_wrong_length_returns_none(monkeypatch):
    _patch_chat_llm(monkeypatch, _FakeLLM(response="[0.9]"))
    assert await rerank_chunks("问题", [_candidate(1), _candidate(2)]) is None


async def test_rerank_llm_failure_returns_none(monkeypatch):
    _patch_chat_llm(monkeypatch, _FakeLLM(error=RuntimeError("LLM API key 未配置")))
    assert await rerank_chunks("问题", [_candidate(1)]) is None


async def test_rerank_empty_candidates():
    assert await rerank_chunks("问题", []) == []


def test_parse_scores_clamps_to_unit_interval():
    assert parse_scores("[1.5, -0.2, 0.5]", 3) == [1.0, 0.0, 0.5]


# ---------- 上下文长度裁剪 ----------

def test_trim_blocks_respects_char_limit():
    blocks = [
        {"doc_id": 1, "doc_title": "A", "start": 0, "end": 0, "content": "x" * 100},
        {"doc_id": 1, "doc_title": "A", "start": 1, "end": 1, "content": "y" * 200},
        {"doc_id": 2, "doc_title": "B", "start": 0, "end": 0, "content": "z" * 50},
    ]
    selected = trim_blocks(blocks, 250)
    assert len(selected) == 1  # 100 装入，100+200=300 超限停止
    assert selected[0]["content"] == "x" * 100


def test_trim_blocks_truncates_first_oversized_block():
    blocks = [{"doc_id": 1, "doc_title": "A", "start": 0, "end": 0, "content": "x" * 500}]
    selected = trim_blocks(blocks, 100)
    assert len(selected) == 1
    assert len(selected[0]["content"]) == 100


def test_trim_blocks_empty_and_zero_limit():
    assert trim_blocks([], 100) == []
    assert trim_blocks([{"content": "x"}], 0) == []


def test_parse_string_array_invalid():
    assert parse_string_array("不是 JSON") == []
    assert parse_string_array('[1, "有效", null]') == ["有效"]
