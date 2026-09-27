"""PR-F：McpSkill —— 把 MCP server 的每个工具包装成一个 Skill 实例。"""
import logging

from app.services.skills.base import Skill, truncate_result
from app.services.skills.mcp_pool import get_mcp_pool

logger = logging.getLogger(__name__)


class McpSkill(Skill):
    """包装 MCP server 的单个工具。

    实例由 build_skill_from_row(mcp type row) 构造。
    配置存于 config：
      - server_id: int
      - tool_name: str
      - transport: str
      - server_config: dict  # server 自身的 command/url 等
      - timeout: int (秒)
    display_name 用 `{server_name}__{tool_name}` 形式避免跨 server 重名。
    """

    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict,
        config: dict,
    ):
        super().__init__(config)
        self.name = name
        self.description = description
        self.parameters = parameters
        self.server_id: int = int(config.get("server_id", 0))
        self.tool_name: str = config.get("tool_name", "")
        self.transport: str = config.get("transport", "stdio")
        self.server_config: dict = dict(config.get("server_config") or {})

    async def close(self) -> None:
        """长连接由 pool 统一管理；McpSkill 本身无需关闭。"""
        return None

    async def run(self, args: dict, ctx: dict) -> str:
        """调 MCP 工具：pool.get_session(...) → session.call_tool(...) → 拼装文本结果。

        MCP CallToolResult.content 是 list（TextContent / ImageContent / EmbeddedResource）。
        仅提取文本部分；图片/资源另行处理（Phase 2）。
        """
        if not self.server_id or not self.tool_name:
            raise RuntimeError("MCP skill 配置不完整（缺少 server_id / tool_name）")
        pool = get_mcp_pool()
        async with pool.get_session(self.server_id, self.transport, self.server_config) as session:
            result = await session.call_tool(self.tool_name, args or {})
        # 拼装文本结果
        texts: list[str] = []
        for item in (result.content or []):
            text = getattr(item, "text", None)
            if text:
                texts.append(text)
        if not texts:
            return "(工具返回空结果)"
        return truncate_result("\n".join(texts))