"""兼容壳：dsh 桥接层已从 Python SDK（stdio JSON-RPC）切换到 ACP
（agent-client-protocol，`dsh --profile acp`），实现在 app.services.acp_bridge。

本模块仅保留旧导入路径（bridge / DshUnavailable / DshBridge），新代码请直接
引用 app.services.acp_bridge。阶段 1 的每用户进程池、user_{id}.yml patch、
令牌临期重建机制已整体移除：现为单租户单进程 + 每会话动态挂 MCP（headers
带新签令牌）+ session/resume 跨进程恢复。
"""
from app.services.acp_bridge import AcpBridge, AcpUnavailable, bridge

# 旧名别名（阶段 1 的引用方兼容）
DshBridge = AcpBridge
DshUnavailable = AcpUnavailable

__all__ = ["AcpBridge", "AcpUnavailable", "DshBridge", "DshUnavailable", "bridge"]
