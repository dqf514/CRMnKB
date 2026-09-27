"""PR-E：Skill 框架埋点与超时测试。

覆盖：
  - execute_skill 成功路径写 log(success=true)
  - execute_skill 超时路径写 log(success=false)
  - execute_skill 异常路径写 log(success=false, error=...)
  - record_skill_call_log 失败静默
  - timeout 字段从 config 读取 + 钳制到 [1, 600]
  - Skill 基类的 async context manager
"""

import asyncio
import pytest

from app.services import skill_usage
from app.services.skills import registry as skills_registry
from app.services.skill_usage import record_skill_call_log
from app.services.skills.base import Skill, truncate_result
from app.services.skills.registry import execute_skill


class _EchoSkill(Skill):
    """返回 args + self.timeout 便于断言。"""

    name = "echo"
    description = "echo back"
    parameters = {"type": "object", "properties": {"x": {"type": "string"}}}

    async def run(self, args, ctx):
        await asyncio.sleep(0)
        return f"x={args.get('x', '')};timeout={self.timeout}"


class _SlowSkill(Skill):
    name = "slow"
    description = "always slow"
    parameters = {"type": "object", "properties": {}}

    async def run(self, args, ctx):
        await asyncio.sleep(1.5)  # 远长于 1s timeout，触发超时
        return "done"


class _BoomSkill(Skill):
    name = "boom"
    description = "always error"
    parameters = {"type": "object", "properties": {}}

    async def run(self, args, ctx):
        raise RuntimeError("skill execution failed: simulated")


# ---------- Skill 基类 ----------


def test_skill_default_timeout_60():
    s = _EchoSkill()
    assert s.timeout == 60


def test_skill_timeout_from_config():
    s = _EchoSkill(config={"timeout": 30})
    assert s.timeout == 30


def test_skill_timeout_clamped_min():
    s = _EchoSkill(config={"timeout": -5})
    assert s.timeout == 1


def test_skill_timeout_clamped_max():
    s = _EchoSkill(config={"timeout": 9999})
    assert s.timeout == 600


def test_skill_timeout_invalid_falls_back_to_default():
    s = _EchoSkill(config={"timeout": "abc"})
    assert s.timeout == Skill.DEFAULT_TIMEOUT


async def test_skill_async_context_manager_no_op_close():
    """默认 close 是 no-op；async with 应可正常进入/退出。"""
    s = _EchoSkill()
    async with s as ctx_skill:
        assert ctx_skill is s
        assert s.timeout == 60


# ---------- execute_skill 埋点 ----------


async def test_execute_skill_success_writes_log(monkeypatch):
    """成功路径：写 log(success=true, latency_ms>=0)。"""
    captured: list[dict] = []

    async def _capture(entry):
        captured.append(entry)

    monkeypatch.setattr(skills_registry, "record_skill_call_log", _capture)
    result = await execute_skill(
        _EchoSkill(), {"x": "hi"}, {"tenant_id": 1, "user_id": 2, "caller": "chat"}
    )
    assert "x=hi" in result
    assert "timeout=60" in result  # truncate 后保留
    assert len(captured) == 1
    assert captured[0]["success"] is True
    assert captured[0]["skill_name"] == "echo"
    assert captured[0]["caller"] == "chat"
    assert captured[0]["tenant_id"] == 1
    assert captured[0]["user_id"] == 2
    assert captured[0]["latency_ms"] >= 0
    assert captured[0]["error"] is None


async def test_execute_skill_timeout_writes_failure_log(monkeypatch):
    """超时路径：抛 TimeoutError + 写 log(success=false)。"""
    captured: list[dict] = []

    async def _capture(entry):
        captured.append(entry)

    monkeypatch.setattr(skills_registry, "record_skill_call_log", _capture)
    skill = _SlowSkill(config={"timeout": 1})  # 1ms 超时（SleepUnit 实际最少 ~10ms）
    with pytest.raises((TimeoutError, asyncio.TimeoutError)):
        await execute_skill(skill, {}, {"tenant_id": 1, "user_id": 2})
    assert len(captured) == 1
    assert captured[0]["success"] is False
    assert captured[0]["error"] is not None


async def test_execute_skill_exception_writes_failure_log(monkeypatch):
    """异常路径：抛原异常 + 写 log(success=false, error=...)"""
    captured: list[dict] = []

    async def _capture(entry):
        captured.append(entry)

    monkeypatch.setattr(skills_registry, "record_skill_call_log", _capture)
    with pytest.raises(RuntimeError, match="simulated"):
        await execute_skill(_BoomSkill(), {}, {"tenant_id": 1, "user_id": 2})
    assert len(captured) == 1
    assert captured[0]["success"] is False
    assert "simulated" in captured[0]["error"]


async def test_execute_skill_legacy_skill_without_timeout_attr(monkeypatch):
    """旧式 skill-like 对象（无 timeout 属性）应回退到默认。"""
    class _LegacySkill:
        name = "legacy"
        description = "no timeout attr"
        parameters = {"type": "object", "properties": {}}
        async def run(self, args, ctx):
            return "ok"

    captured: list[dict] = []

    async def _capture(entry):
        captured.append(entry)

    monkeypatch.setattr(skills_registry, "record_skill_call_log", _capture)
    result = await execute_skill(_LegacySkill(), {}, {"tenant_id": 1})
    assert result == "ok"
    assert captured[0]["success"] is True


# ---------- record_skill_call_log 失败静默 ----------


async def test_record_skill_call_log_db_failure_silent(monkeypatch):
    """写 DB 失败不应抛错。"""
    # 把 AsyncSessionLocal 替换为始终抛异常的 fake
    class _BrokenSessionCtx:
        async def __aenter__(self):
            raise RuntimeError("DB unreachable")
        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(
        skill_usage, "AsyncSessionLocal", lambda: _BrokenSessionCtx()
    )
    # 应静默不抛
    await record_skill_call_log(
        {"tenant_id": 1, "skill_name": "x", "latency_ms": 10, "success": True}
    )


async def test_truncate_result_short_text_unchanged():
    assert truncate_result("hello") == "hello"


async def test_truncate_result_long_text_truncated():
    text = "x" * 5000
    out = truncate_result(text)
    assert len(out) == 3000 + len("…[截断]")
    assert out.endswith("…[截断]")