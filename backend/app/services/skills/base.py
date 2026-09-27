"""Skill 协议：名称/描述/JSON Schema 参数 + run(args, ctx) 返回给模型的文本结果。

PR-E 扩展：
  - 每个 skill 可独立配置 timeout（默认 60s）
  - async context manager（__aenter__/__aexit__）管理资源生命周期（MCP 长连接等）
  - 子类可覆写 close() 释放资源，默认 no-op
"""

# 返回给模型的工具结果截断上限（字符）
RESULT_MAX_CHARS = 3000


class Skill:
    """工具基类。子类定义 name/description/parameters，实现 run。

    配置项（self.config）：
      - timeout: 超时秒数（默认 DEFAULT_TIMEOUT）
    """

    name: str = ""
    description: str = ""
    parameters: dict = {"type": "object", "properties": {}}
    DEFAULT_TIMEOUT: int = 60

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        # 从 config 读 timeout，否则用类默认（钳制到 [1, 600] 区间）
        try:
            t = int(self.config.get("timeout", self.DEFAULT_TIMEOUT))
        except (TypeError, ValueError):
            t = self.DEFAULT_TIMEOUT
        self.timeout: int = max(1, min(t, 600))

    async def __aenter__(self) -> "Skill":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        """子类覆写以释放长连接资源（如 MCP session / HTTP keep-alive）。
        默认 no-op。close() 失败不应抛出（吞掉记录日志即可）。"""

    async def run(self, args: dict, ctx: dict) -> str:
        """执行工具，返回给模型的文本结果。ctx 含 tenant_id / user_id。"""
        raise NotImplementedError


def truncate_result(text: str) -> str:
    """工具结果统一截断。纯函数。"""
    text = text or ""
    if len(text) <= RESULT_MAX_CHARS:
        return text
    return text[:RESULT_MAX_CHARS] + "…[截断]"


def tool_spec(skill: Skill) -> dict:
    """OpenAI tools 参数用的函数描述。纯函数。"""
    return {
        "name": skill.name,
        "description": skill.description,
        "parameters": skill.parameters,
    }