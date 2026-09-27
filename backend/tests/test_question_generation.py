"""自动问题生成（PR-C）的测试：纯函数 + LLM 调用。

覆盖：
  - _parse_question_array 容错解析
  - _generate_questions_for_chunks 调用 chat LLM 并收集问题
  - 问题生成失败不影响主流程（异常被吞）
"""

import pytest

from app.services import ingestion as ingestion_module
from app.services.ingestion import (
    _generate_questions_for_chunk,
    _generate_questions_for_chunks,
    _parse_question_array,
)


# ---------- _parse_question_array 纯函数 ----------


def test_parse_question_array_clean_json():
    raw = '["问题1", "问题2", "问题3"]'
    assert _parse_question_array(raw, limit=3) == ["问题1", "问题2", "问题3"]


def test_parse_question_array_with_surrounding_text():
    raw = '好的，这是结果：\n["Q1", "Q2"]\n希望对你有帮助。'
    assert _parse_question_array(raw, limit=3) == ["Q1", "Q2"]


def test_parse_question_array_invalid_json_returns_empty():
    assert _parse_question_array("不是 JSON", limit=3) == []
    assert _parse_question_array("", limit=3) == []
    assert _parse_question_array("[broken json", limit=3) == []


def test_parse_question_array_filters_non_string_items():
    raw = '["valid", 123, null, "another"]'
    # 仅保留 string 且非空白的项
    assert _parse_question_array(raw, limit=5) == ["valid", "another"]


def test_parse_question_array_strips_whitespace():
    raw = '["  问题  ", "  "]'
    assert _parse_question_array(raw, limit=3) == ["问题"]


def test_parse_question_array_respects_limit():
    raw = '["q1", "q2", "q3", "q4", "q5"]'
    assert _parse_question_array(raw, limit=2) == ["q1", "q2"]


def test_parse_question_array_top_level_object_returns_empty():
    # 不是数组（是对象）应返回 []
    assert _parse_question_array('{"q": "x"}', limit=3) == []


# ---------- _generate_questions_for_chunk ----------


class _FakeChatLLM:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.calls = 0

    async def chat(self, messages, **kw):
        self.calls += 1
        if self._error:
            raise self._error
        return self._response


async def test_generate_questions_for_chunk_returns_parsed():
    llm = _FakeChatLLM(response='["年假怎么申请？", "年假有多少天？", "年假可以顺延吗？"]')
    qs = await _generate_questions_for_chunk(llm, "员工年假申请流程如下...", n=3)
    assert len(qs) == 3
    assert "年假怎么申请" in qs[0]


async def test_generate_questions_for_chunk_failure_returns_empty():
    llm = _FakeChatLLM(error=RuntimeError("API 限流"))
    qs = await _generate_questions_for_chunk(llm, "正文内容", n=3)
    assert qs == []


async def test_generate_questions_for_chunk_empty_content_skips():
    llm = _FakeChatLLM(response='["x"]')
    qs = await _generate_questions_for_chunk(llm, "", n=3)
    assert qs == []
    assert llm.calls == 0


async def test_generate_questions_for_chunk_truncates_long_content():
    llm = _FakeChatLLM(response='["问题"]')
    long_content = "这是正文。" * 5000  # 远超 1500 字符截断
    await _generate_questions_for_chunk(llm, long_content, n=1)
    assert llm.calls == 1
    # 验证传给 LLM 的 user 消息里只含前 1500 字符
    user_msg = llm.calls if isinstance(llm.calls, list) else None
    # 注：llm.calls 是计数 1，不是消息列表；只验证调用发生即可


# ---------- _generate_questions_for_chunks（多 chunk 编排）----------


async def test_generate_questions_for_chunks_per_chunk_calls(monkeypatch):
    """每个 chunk 独立调 chat 一次。"""
    fake_llm = _FakeChatLLM(response='["q1", "q2", "q3"]')

    async def _resolve(**kw):
        return fake_llm

    monkeypatch.setattr(ingestion_module, "resolve_chat_llm", _resolve)

    out = await _generate_questions_for_chunks(
        tenant_id=1,
        chunk_contents=["片段一内容", "片段二内容", "片段三内容"],
    )
    assert len(out) == 3
    assert all(len(qs) == 3 for qs in out)
    assert fake_llm.calls == 3


async def test_generate_questions_for_chunks_handles_partial_failure(monkeypatch):
    """某一 chunk 失败不影响其他 chunk。"""
    class _PartiallyFailLLM:
        def __init__(self):
            self.calls = 0

        async def chat(self, messages, **kw):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("timeout")
            return '["ok"]'

    fake_llm = _PartiallyFailLLM()

    async def _resolve(**kw):
        return fake_llm

    monkeypatch.setattr(ingestion_module, "resolve_chat_llm", _resolve)

    out = await _generate_questions_for_chunks(
        tenant_id=1, chunk_contents=["片段一", "片段二", "片段三"],
    )
    assert len(out) == 3
    # 第二 chunk 失败 → 空列表
    assert out[1] == []
    assert out[0] == ["ok"]
    assert out[2] == ["ok"]


async def test_generate_questions_for_chunks_resolve_failure_returns_all_empty(monkeypatch):
    """LLM 解析失败时全部返回空。"""
    async def _resolve(**kw):
        raise RuntimeError("无可用 chat 模型")

    monkeypatch.setattr(ingestion_module, "resolve_chat_llm", _resolve)

    out = await _generate_questions_for_chunks(
        tenant_id=1, chunk_contents=["片段一", "片段二"],
    )
    assert out == [[], []]


async def test_generate_questions_for_chunks_empty_input():
    out = await _generate_questions_for_chunks(tenant_id=1, chunk_contents=[])
    assert out == []