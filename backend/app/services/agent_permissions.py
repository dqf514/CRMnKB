"""Agent 工具自动放行白名单（dsh 权限应答链）。

dsh agent 调用工具时经 session/request_permission 反向请求后端审批。白名单内
的工具自动 allow-once（不再逐次打断），白名单外一律拒绝——写操作本就走后端
agent_approvals 审批链，不经 dsh 直接执行。

存储：system_settings 表 key=agent_auto_allow_tools（JSON 数组，整体替换），
进程内 60s 缓存（权限应答是热路径，避免每次查库）；未配置时回退默认白名单
（知识库 MCP 只读工具），保证升级后行为不变。

管理端：`GET/PUT /admin/agent-tools`（仅硬超管——改的是全租户 agent 能力边界）。
"""
from __future__ import annotations

import json
import logging
import re
import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_setting import SystemSetting

logger = logging.getLogger(__name__)

AUTO_ALLOW_KEY = "agent_auto_allow_tools"
_CACHE_TTL = 60.0

# 默认白名单：知识库 MCP 只读工具（与本功能上线前的硬编码集合一致，保证行为不变）
DEFAULT_AUTO_ALLOW_TOOLS: list[str] = [
    "kb_search", "kb_read_doc", "kb_list",
    "crm_list_customers", "crm_search_customers", "crm_get_customer",
    "crm_list_followups", "crm_list_opportunities", "crm_list_tasks", "crm_stats",
    "skill_list",
    "memory_save", "memory_list", "memory_search", "memory_delete",
]

# 管理端展示用的已知工具目录（自由文本也可添加，目录只用于分组/说明/风险提示）
# risk: low=只读 medium=外发/写文件 high=执行命令/写库（绕审批链）
TOOL_CATALOG: list[dict] = [
    # 知识库 MCP（只读）
    {"name": "kb_search", "source": "知识库 MCP", "label": "知识库检索", "risk": "low"},
    {"name": "kb_read_doc", "source": "知识库 MCP", "label": "读取文档内容", "risk": "low"},
    {"name": "kb_list", "source": "知识库 MCP", "label": "列出知识库", "risk": "low"},
    {"name": "crm_list_customers", "source": "知识库 MCP", "label": "客户名单", "risk": "low"},
    {"name": "crm_search_customers", "source": "知识库 MCP", "label": "搜索客户", "risk": "low"},
    {"name": "crm_get_customer", "source": "知识库 MCP", "label": "客户详情", "risk": "low"},
    {"name": "crm_list_followups", "source": "知识库 MCP", "label": "跟进记录", "risk": "low"},
    {"name": "crm_list_opportunities", "source": "知识库 MCP", "label": "商机列表", "risk": "low"},
    {"name": "crm_list_tasks", "source": "知识库 MCP", "label": "任务列表", "risk": "low"},
    {"name": "crm_stats", "source": "知识库 MCP", "label": "经营概览统计", "risk": "low"},
    {"name": "skill_list", "source": "知识库 MCP", "label": "已启用工具清单", "risk": "low"},
    {"name": "memory_save", "source": "知识库 MCP", "label": "保存个人记忆", "risk": "low"},
    {"name": "memory_list", "source": "知识库 MCP", "label": "列出个人记忆", "risk": "low"},
    {"name": "memory_search", "source": "知识库 MCP", "label": "搜索个人记忆", "risk": "low"},
    {"name": "memory_delete", "source": "知识库 MCP", "label": "删除个人记忆", "risk": "low"},
    # 知识库 MCP（写入/外发）——写操作正常应走后端审批链，自动放行等于绕开审批
    {"name": "crm_create_customer", "source": "知识库 MCP（写）", "label": "新建客户", "risk": "high"},
    {"name": "crm_update_customer", "source": "知识库 MCP（写）", "label": "修改客户", "risk": "high"},
    {"name": "crm_delete_customer", "source": "知识库 MCP（写）", "label": "删除客户", "risk": "high"},
    {"name": "crm_create_opportunity", "source": "知识库 MCP（写）", "label": "新建商机", "risk": "high"},
    {"name": "crm_create_task", "source": "知识库 MCP（写）", "label": "新建任务", "risk": "high"},
    {"name": "crm_add_followup", "source": "知识库 MCP（写）", "label": "添加跟进", "risk": "high"},
    {"name": "mail_draft_create", "source": "知识库 MCP（写）", "label": "邮件草稿", "risk": "high"},
    {"name": "skill_call", "source": "知识库 MCP（写）", "label": "通用工具派发", "risk": "high"},
    {"name": "skill_create_api", "source": "知识库 MCP（写）", "label": "AI 起草 API 工具", "risk": "high"},
    {"name": "web_search", "source": "知识库 MCP / dsh", "label": "联网搜索", "risk": "medium"},
    {"name": "web_fetch", "source": "知识库 MCP / dsh", "label": "网页抓取", "risk": "medium"},
    # dsh 内置工具
    {"name": "read", "source": "dsh 内置", "label": "读工作区文件", "risk": "low"},
    {"name": "glob", "source": "dsh 内置", "label": "文件名匹配查找", "risk": "low"},
    {"name": "grep", "source": "dsh 内置", "label": "文件内容搜索", "risk": "low"},
    {"name": "read_image", "source": "dsh 内置", "label": "读图片", "risk": "low"},
    {"name": "todo_write", "source": "dsh 内置", "label": "任务清单（对话内）", "risk": "low"},
    {"name": "ask_user_question", "source": "dsh 内置", "label": "向用户提问", "risk": "low"},
    {"name": "write", "source": "dsh 内置", "label": "写工作区文件", "risk": "medium"},
    {"name": "edit", "source": "dsh 内置", "label": "编辑工作区文件", "risk": "medium"},
    {"name": "web_search", "source": "dsh 内置", "label": "联网搜索（dsh）", "risk": "medium"},
    {"name": "web_fetch", "source": "dsh 内置", "label": "网页抓取（dsh）", "risk": "medium"},
    {"name": "skill", "source": "dsh 内置", "label": "调用 dsh 技能", "risk": "medium"},
    {"name": "job_list", "source": "dsh 内置", "label": "后台任务列表", "risk": "low"},
    {"name": "job_output", "source": "dsh 内置", "label": "后台任务输出", "risk": "low"},
    {"name": "job_kill", "source": "dsh 内置", "label": "结束后台任务", "risk": "medium"},
    {"name": "bash", "source": "dsh 内置", "label": "执行 Bash 命令", "risk": "high"},
    {"name": "pwsh", "source": "dsh 内置", "label": "执行 PowerShell 命令", "risk": "high"},
]

_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,100}$")
MAX_TOOLS = 200

_cache: dict = {"tools": None, "at": 0.0}


def parse_tools(value: str | None) -> list[str]:
    """解析配置（纯函数）：未配置/非法 JSON 回退默认白名单；合法 JSON 数组按原样
    清洗后使用（显式空数组 = 全部工具拒绝/走审批，是最严格的合法配置）。去重保序。"""
    if not value:
        return list(DEFAULT_AUTO_ALLOW_TOOLS)
    try:
        data = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return list(DEFAULT_AUTO_ALLOW_TOOLS)
    if not isinstance(data, list):
        return list(DEFAULT_AUTO_ALLOW_TOOLS)
    tools: list[str] = []
    for t in data:
        t = str(t).strip() if isinstance(t, (str, int)) else ""
        if t and _TOOL_NAME_RE.match(t) and t not in tools:
            tools.append(t)
    return tools


def is_auto_allowed(title: str, whitelist: set[str] | list[str]) -> bool:
    """工具是否自动放行（纯函数）：精确匹配，或 MCP 前缀工具按后缀匹配
    （mcp__kb__kb_search 命中 kb_search）。"""
    if not title:
        return False
    wl = whitelist if isinstance(whitelist, set) else set(whitelist)
    if title in wl:
        return True
    if "__" in title:
        return title.rsplit("__", 1)[-1] in wl
    return False


async def get_auto_allow_tools(db: AsyncSession) -> list[str]:
    """读白名单（60s 进程缓存）。"""
    now = time.monotonic()
    if _cache["tools"] is not None and now - _cache["at"] < _CACHE_TTL:
        return _cache["tools"]
    row = await db.get(SystemSetting, AUTO_ALLOW_KEY)
    tools = parse_tools(row.value if row else None)
    _cache["tools"] = tools
    _cache["at"] = now
    return tools


def validate_tools_payload(items: list) -> list[str]:
    """管理端整体替换校验（纯函数）。非法抛 ValueError，正常返回清洗后的列表。"""
    if not isinstance(items, list):
        raise ValueError("tools 必须是数组")
    if len(items) > MAX_TOOLS:
        raise ValueError(f"白名单最多 {MAX_TOOLS} 条")
    cleaned: list[str] = []
    for it in items:
        t = str(it).strip() if isinstance(it, str) else ""
        if not t or not _TOOL_NAME_RE.match(t):
            raise ValueError(f"非法工具名「{it}」（仅字母/数字/_.-，最长 100）")
        if t not in cleaned:
            cleaned.append(t)
    return cleaned


async def set_auto_allow_tools(db: AsyncSession, tools: list[str]) -> None:
    row = await db.get(SystemSetting, AUTO_ALLOW_KEY)
    if row is None:
        row = SystemSetting(key=AUTO_ALLOW_KEY, value=json.dumps(tools, ensure_ascii=False))
        db.add(row)
    else:
        row.value = json.dumps(tools, ensure_ascii=False)
    await db.commit()
    _cache["tools"] = None  # 失效缓存
    logger.info("agent 工具白名单已更新：%d 条", len(tools))
