import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import text as sql_text

from app.api.admin_llm import mask_api_key
from app.api.deps import get_db, require_admin
from app.models.skill import Skill as SkillRow
from app.models.user import User
from app.schemas.skill import (
    ApiSkillCreate,
    SkillOut,
    SkillStats,
    SkillTestRequest,
    SkillTestResult,
    SkillUpdate,
)
from app.services.audit import record_audit
from app.services.skills.base import Skill
from app.services.skills.builtin import BUILTIN_SKILLS
from app.services.skills.registry import (
    build_skill_from_row,
    execute_skill,
    get_tenant_skills,
)

router = APIRouter(prefix="/admin", tags=["admin-skills"], dependencies=[Depends(require_admin)])

_SENSITIVE_MARKS = ("key", "secret", "token", "password", "authorization")


def _is_sensitive_key(key: object) -> bool:
    return isinstance(key, str) and any(mark in key.lower() for mark in _SENSITIVE_MARKS)


def _mask_config(config: dict) -> dict:
    """config 敏感字段脱敏（沿用 admin_llm 惯例）。

    递归处理：任意深度下键名含 key/secret/token/password/authorization（不区分大小写）
    的字符串值都脱敏，覆盖 headers.Authorization 等嵌套密钥；嵌套 dict/list 原样深入。
    """

    def _mask(node):
        if isinstance(node, dict):
            return {
                k: mask_api_key(v) if _is_sensitive_key(k) and isinstance(v, str) else _mask(v)
                for k, v in node.items()
            }
        if isinstance(node, list):
            return [_mask(item) for item in node]
        return node

    return _mask(config or {})


def _extract_timeout(config: dict | None) -> int:
    """从 config.timeout 取超时（PR-E），缺省回退类默认。"""
    try:
        t = int((config or {}).get("timeout", Skill.DEFAULT_TIMEOUT))
    except (TypeError, ValueError):
        return Skill.DEFAULT_TIMEOUT
    return max(1, min(t, 600))


async def _get_skill_or_404(db: AsyncSession, tenant_id: int, skill_id: int) -> SkillRow:
    skill = await db.get(SkillRow, skill_id)
    if skill is None or skill.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="skill 不存在")
    return skill


def _to_skill_out(skill: SkillRow) -> SkillOut:
    """构造 SkillOut：脱敏 config + 提取 timeout（PR-E）。"""
    return SkillOut(
        id=skill.id,
        name=skill.name,
        display_name=skill.display_name or skill.name,
        description=skill.description or "",
        type=skill.type,
        enabled=skill.enabled,
        config=_mask_config(skill.config),
        timeout=_extract_timeout(skill.config),
        created_at=skill.created_at,
    )


@router.get("/skills", response_model=list[SkillOut])
async def list_skills(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """内置（代码注册）+ 自定义合并列表，敏感配置脱敏。"""
    items = await get_tenant_skills(db, admin.tenant_id)
    for item in items:
        item["config"] = _mask_config(item["config"])
    return items


@router.get("/skills/stats", response_model=list[SkillStats])
async def skills_stats(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """PR-E：最近 30 天每个 skill 的调用统计（总次数 / 失败次数 / 平均耗时 / 最后调用）。"""
    rows = (
        await db.execute(
            sql_text(
                "SELECT skill_name,"
                " COUNT(*)::int AS total,"
                " COUNT(*) FILTER (WHERE NOT success)::int AS failed,"
                " COALESCE(AVG(latency_ms), 0)::int AS avg_latency_ms,"
                " MAX(created_at) AS last_called_at"
                " FROM skill_call_logs"
                " WHERE tenant_id = :tid AND created_at > NOW() - INTERVAL '30 days'"
                " GROUP BY skill_name"
                " ORDER BY total DESC"
            ),
            {"tid": admin.tenant_id},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


@router.put("/skills/{skill_id}", response_model=SkillOut)
async def update_skill(
    skill_id: str,
    body: SkillUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """改 enabled/config/description/display_name/timeout。

    skill_id 兼容两种形态：数字 ID（已有行）或技能名——尚未入库的内置技能在
    列表中 id 为 NULL，前端只能以 name 作为路径参数调用；按名未找到且是内置
    技能时自动建行（首次保存即启用）。"""
    skill: SkillRow | None = None
    if skill_id.isdigit():
        skill = await db.get(SkillRow, int(skill_id))
        if skill is not None and skill.tenant_id != admin.tenant_id:
            raise HTTPException(status_code=404, detail="skill 不存在")
    if skill is None:
        # 按名称解析（未入库的内置技能只有 name 可用）
        skill = await db.scalar(
            select(SkillRow).where(
                SkillRow.tenant_id == admin.tenant_id, SkillRow.name == skill_id
            )
        )
    if skill is None:
        # 首次保存内置 skill：自动建行
        if skill_id not in BUILTIN_SKILLS:
            raise HTTPException(status_code=404, detail="skill 不存在")
        skill = SkillRow(
            tenant_id=admin.tenant_id,
            name=skill_id,
            type="builtin",
            description=BUILTIN_SKILLS[skill_id].description,
            enabled=False,
            config={},
        )
        db.add(skill)
        await db.flush()

    changed: dict = {}
    if body.enabled is not None and body.enabled != skill.enabled:
        skill.enabled = body.enabled
        changed["enabled"] = body.enabled
    if body.config is not None:
        skill.config = body.config
        changed["config"] = list(body.config.keys())  # 审计不记明文配置
    if body.timeout is not None:
        # timeout 写入 config.timeout，不新增列（与 base.py 兼容）
        cfg = dict(skill.config or {})
        cfg["timeout"] = body.timeout
        skill.config = cfg
        changed["timeout"] = body.timeout
    if body.description is not None:
        skill.description = body.description
    if body.display_name is not None:
        skill.display_name = body.display_name
    if changed:
        record_audit(db, admin, "update", "skill", skill.id, changed)
    await db.commit()
    await db.refresh(skill)
    return _to_skill_out(skill)


@router.post("/skills", response_model=SkillOut, status_code=201)
async def create_api_skill(
    body: ApiSkillCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """新建自定义 api skill（name 租户内唯一，且不得与内置重名）。"""
    if body.name in BUILTIN_SKILLS:
        raise HTTPException(status_code=400, detail="与内置 skill 重名")
    exists = await db.scalar(
        select(SkillRow.id).where(SkillRow.tenant_id == admin.tenant_id, SkillRow.name == body.name)
    )
    if exists:
        raise HTTPException(status_code=400, detail="同名 skill 已存在")
    if not (body.config or {}).get("url"):
        raise HTTPException(status_code=400, detail="api skill 需配置 url")
    cfg = dict(body.config or {})
    if body.timeout is not None:
        cfg["timeout"] = body.timeout
    skill = SkillRow(
        tenant_id=admin.tenant_id,
        name=body.name,
        display_name=body.display_name,
        description=body.description,
        type="api",
        enabled=body.enabled,
        config=cfg,
    )
    db.add(skill)
    await db.flush()
    record_audit(db, admin, "create", "skill", skill.id, {"name": skill.name})
    await db.commit()
    await db.refresh(skill)
    return _to_skill_out(skill)


@router.delete("/skills/{skill_id}", status_code=204)
async def delete_skill(
    skill_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    skill = await _get_skill_or_404(db, admin.tenant_id, skill_id)
    if skill.type == "builtin":
        raise HTTPException(status_code=400, detail="内置 skill 不可删除，可禁用")
    record_audit(db, admin, "delete", "skill", skill.id, {"name": skill.name})
    await db.delete(skill)
    await db.commit()


@router.post("/skills/{skill_id}/test", response_model=SkillTestResult)
async def test_skill(
    skill_id: int,
    body: SkillTestRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """真实执行一次 skill 供管理员测试；执行失败返回 ok:false 而非 500。"""
    skill = await _get_skill_or_404(db, admin.tenant_id, skill_id)
    start = time.perf_counter()
    try:
        instance = await build_skill_from_row(db, skill)
        result = await execute_skill(
            instance, body.args, {"tenant_id": admin.tenant_id, "user_id": admin.id, "caller": "test"}
        )
        return SkillTestResult(
            ok=True, result=result, latency_ms=int((time.perf_counter() - start) * 1000)
        )
    except Exception as exc:
        return SkillTestResult(
            ok=False,
            error=str(exc)[:500],
            latency_ms=int((time.perf_counter() - start) * 1000),
        )
