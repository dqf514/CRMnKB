"""租户级 skill 解析：DB 行优先（builtin 行覆盖内置默认 config），
未建行的内置 skill 以代码默认出现（disabled）。

PR-E：execute_skill 加超时（asyncio.timeout）+ 调用埋点（record_skill_call_log）。"""
import asyncio
import logging
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill import Skill as SkillRow
from app.services.skill_usage import record_skill_call_log
from app.services.skills.api_skill import ApiSkill
from app.services.skills.base import Skill, truncate_result
from app.services.skills.builtin import BUILTIN_SKILLS

logger = logging.getLogger(__name__)


async def get_tenant_skills(db: AsyncSession, tenant_id: int) -> list[dict]:
    """合并内置（代码注册）与自定义行，返回管理端列表项。"""
    rows = (
        (
            await db.execute(
                select(SkillRow)
                .where(SkillRow.tenant_id == tenant_id)
                .order_by(SkillRow.created_at)
            )
        )
        .scalars()
        .all()
    )
    by_name = {r.name: r for r in rows}

    def _timeout_from(cfg: dict | None) -> int:
        try:
            t = int((cfg or {}).get("timeout", Skill.DEFAULT_TIMEOUT))
        except (TypeError, ValueError):
            return Skill.DEFAULT_TIMEOUT
        return max(1, min(t, 600))

    items: list[dict] = []
    for name, cls in BUILTIN_SKILLS.items():
        row = by_name.pop(name, None)
        cfg = dict(row.config) if row else {}
        items.append(
            {
                "id": row.id if row else None,
                "name": name,
                "display_name": (row.display_name if row else None) or name,
                "description": (row.description if row else None) or cls.description,
                "type": "builtin",
                "enabled": row.enabled if row else False,
                "config": cfg,
                "timeout": _timeout_from(cfg),
            }
        )
    for r in by_name.values():  # 自定义行：api / mcp
        cfg = dict(r.config or {})
        items.append(
            {
                "id": r.id,
                "name": r.name,
                "display_name": r.display_name or r.name,
                "description": r.description or "",
                "type": r.type,  # 原样保留：api / mcp
                "enabled": r.enabled,
                "config": cfg,
                "timeout": _timeout_from(cfg),
            }
        )
    return items


async def build_skill_from_row(db: AsyncSession, row: SkillRow) -> Skill:
    """DB 行 → skill 实例（async：MCP 需拉 server config）。

    builtin / api skill 不需 DB；mcp 类型需查 mcp_servers 表拿 server_config。
    """
    if row.type == "builtin" and row.name in BUILTIN_SKILLS:
        return BUILTIN_SKILLS[row.name](dict(row.config or {}))
    if row.type == "mcp":
        from app.models.mcp_server import McpServer
        from app.services.skills.mcp_skill import McpSkill

        cfg = dict(row.config or {})
        server_id = int(cfg.get("server_id", 0))
        server_config: dict = {}
        transport: str = cfg.get("transport", "stdio")
        if server_id and db is not None:
            server = await db.get(McpServer, server_id)
            if server:
                server_config = dict(server.config or {})
                transport = server.transport
                cfg["server_config"] = server_config
                cfg["transport"] = transport
        # parameters 存在 cfg 内（由 sync_tools 写入）
        params = cfg.pop("parameters", None) or {"type": "object", "properties": {}}
        return McpSkill(
            name=row.name,
            description=row.description or "",
            parameters=params,
            config=cfg,
        )
    cfg = dict(row.config or {})
    return ApiSkill(
        name=row.name,
        description=row.description,
        parameters=cfg.pop("parameters", None),
        config=cfg,
        display_name=row.display_name,
    )


async def get_enabled_skills(db: AsyncSession, tenant_id: int) -> list[Skill]:
    """对话管线用：租户启用的 skill 实例列表（无则 []，调用方走原路径）。"""
    rows = (
        (
            await db.execute(
                select(SkillRow).where(
                    SkillRow.tenant_id == tenant_id, SkillRow.enabled.is_(True)
                )
            )
        )
        .scalars()
        .all()
    )
    skills = []
    for row in rows:
        try:
            skills.append(await build_skill_from_row(db, row))
        except Exception as exc:
            logger.warning("skill %s 构建失败（跳过）: %s", row.name, exc)
    return skills


async def execute_skill(skill: Skill, args: dict, ctx: dict) -> str:
    """执行单个工具 + 超时控制 + 结果截断 + 调用埋点。

    任何失败（异常/超时）均抛给调用方转"调用失败: ..."文本，但埋点日志始终写入。
    兼容旧式 skill-like 对象（无 timeout 属性时回退 Skill.DEFAULT_TIMEOUT）。
    """
    timeout_sec = getattr(skill, "timeout", Skill.DEFAULT_TIMEOUT)
    start = time.perf_counter()
    success = False
    error: str | None = None
    try:
        async with asyncio.timeout(timeout_sec):
            result = await skill.run(args or {}, ctx)
        success = True
        return truncate_result(result)
    except Exception as exc:
        error = str(exc)[:500]
        raise
    finally:
        latency_ms = int((time.perf_counter() - start) * 1000)
        await record_skill_call_log(
            {
                "tenant_id": ctx.get("tenant_id"),
                "user_id": ctx.get("user_id"),
                "skill_name": skill.name,
                "caller": ctx.get("caller", "chat"),
                "latency_ms": latency_ms,
                "success": success,
                "error": error,
            }
        )
