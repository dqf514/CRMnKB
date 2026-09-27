"""工作台聊天"深度思考"开关测试。

覆盖：_strip_think_text / _ThinkStripper / _clean_stream 的 <think> 块剥离
（含跨 token 断句）；enable_thinking 参数透传到 ApiLLM 请求体；开关默认值解析。
全部纯函数/桩，不触网不触库。
"""
import asyncio
import json

import httpx

import app.services.chat as chat_module
from app.services.llm.api_llm import ApiLLM


# ---------------------------------------------------------------------------
# <think> 块剥离
# ---------------------------------------------------------------------------

def test_strip_think_text_removes_block():
    assert chat_module._strip_think_text("<think>\n推理过程\n</think>\n你好") == "你好"


def test_strip_think_text_variant_tag():
    assert chat_module._strip_think_text("<thinking>a</thinking>正文<b>保留</b>") == "正文<b>保留</b>"


def test_strip_think_text_noop():
    assert chat_module._strip_think_text("正常内容 <div>标签</div>") == "正常内容 <div>标签</div>"
    assert chat_module._strip_think_text("") == ""


def test_think_stripper_across_token_boundaries():
    """标签被拆在多个 token 里也要正确剥离。"""
    s = chat_module._ThinkStripper()
    out = []
    for tok in ["<thi", "nk>思考内容", "…", "</", "think>", "最终答案"]:
        c = s.feed(tok)
        if c:
            out.append(c)
    assert "".join(out) == "最终答案"


def test_think_stripper_keeps_outer_text():
    s = chat_module._ThinkStripper()
    assert s.feed("开头 ") == "开头 "
    assert s.feed("<think>隐藏内容") == ""
    assert s.feed("仍隐藏") == ""
    assert s.feed("</think>") == ""
    assert s.feed("结尾") == "结尾"


def test_think_stripper_releases_pending_when_overlong():
    """未闭合的 think 块超上限后整体放行，不吞内容。"""
    s = chat_module._ThinkStripper()
    chunk = "x" * (chat_module._ThinkStripper._MAX_PENDING + 50)
    assert s.feed("<think>" + chunk) == chunk


async def test_clean_stream_filters():
    async def gen():
        for t in ["<think>推理", "</think>", "回复"]:
            yield t

    got = [t async for t in chat_module._clean_stream(gen())]
    assert "".join(got) == "回复"


# ---------------------------------------------------------------------------
# 开关默认值与请求参数
# ---------------------------------------------------------------------------

def test_thinking_extra(monkeypatch):
    monkeypatch.setattr(chat_module.settings, "LLM_CHAT_THINKING_PARAM", "enable_thinking")
    assert chat_module._thinking_extra(True) == {}
    assert chat_module._thinking_extra(False) == {"enable_thinking": False}


def test_thinking_extra_empty_param(monkeypatch):
    monkeypatch.setattr(chat_module.settings, "LLM_CHAT_THINKING_PARAM", "")
    assert chat_module._thinking_extra(False) == {}


def test_resolve_thinking_default(monkeypatch):
    monkeypatch.setattr(chat_module.settings, "CHAT_DEFAULT_THINKING", True)
    assert chat_module._resolve_thinking(None) is True
    monkeypatch.setattr(chat_module.settings, "CHAT_DEFAULT_THINKING", False)
    assert chat_module._resolve_thinking(None) is False
    assert chat_module._resolve_thinking(True) is True
    assert chat_module._resolve_thinking(False) is False


def _mock_httpx(monkeypatch, captured):
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        async def _handler(request):
            captured["body"] = request.content.decode()
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

        kwargs["transport"] = httpx.MockTransport(_handler)
        return real(*args, **kwargs)

    monkeypatch.setattr("app.services.llm.api_llm.httpx.AsyncClient", factory)


def test_apillm_sends_thinking_false_param(monkeypatch):
    captured = {}
    _mock_httpx(monkeypatch, captured)
    llm = ApiLLM("https://api.minimaxi.com/v1", "k", "m", "e")
    asyncio.run(llm.chat([{"role": "user", "content": "hi"}], enable_thinking=False))
    body = json.loads(captured["body"])
    assert body["enable_thinking"] is False


def test_apillm_omits_thinking_param_when_default(monkeypatch):
    captured = {}
    _mock_httpx(monkeypatch, captured)
    llm = ApiLLM("https://api.minimaxi.com/v1", "k", "m", "e")
    asyncio.run(llm.chat([{"role": "user", "content": "hi"}]))
    assert "enable_thinking" not in json.loads(captured["body"])
