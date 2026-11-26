"""管理端 dsh 插件库（全租户生效）：插件树查看、启停、安装、重启。

启停/安装写入 patches/plugins.yml 覆盖层后自动重建 dsh ACP 进程生效——
会话历史在 PG（dsh_session_* 表），进程重建后 resume 不丢上下文。
仅硬超管可用（影响全租户 agent 行为，不走 RBAC 权限点）。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import require_admin
from app.config import settings
from app.models.user import User
from app.services import dsh_plugins
from app.services.dsh_plugins import DshPluginError

router = APIRouter(prefix="/admin/dsh-plugins", tags=["admin-dsh-plugins"])


def _ensure_ready() -> None:
    if not settings.DSH_AGENT_ENABLED:
        raise HTTPException(status_code=503, detail="dsh Agent 未启用（DSH_AGENT_ENABLED=false）")
    if not settings.DSH_BIN:
        raise HTTPException(status_code=503, detail="未配置 DSH_BIN（dsh 可执行文件路径）")


async def _restart_bridge() -> None:
    """关闭当前 dsh ACP 进程：下次 agent 请求懒重建并加载新插件配置（会话 resume 自 PG）。"""
    from app.services.acp_bridge import bridge

    await bridge.close_all()


class PluginToggle(BaseModel):
    enabled: bool


class InstallRequest(BaseModel):
    # npm 包名（@scope/name[@version]）或本地绝对路径
    spec: str = Field(min_length=1, max_length=300)


@router.get("")
async def list_plugins(user: User = Depends(require_admin)):
    """插件清单：dsh profile 树合并管理员覆盖层后的有效启停状态。"""
    _ensure_ready()
    try:
        return await dsh_plugins.list_plugins()
    except DshPluginError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.put("/{plugin_id}")
async def toggle_plugin(
    plugin_id: str,
    body: PluginToggle,
    user: User = Depends(require_admin),
):
    """启停插件（写覆盖层 + 重建 dsh 进程生效）。"""
    _ensure_ready()
    try:
        tree = await dsh_plugins.dump_tree()
    except DshPluginError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    known = {it["id"] for it in tree} | {c["id"] for c in dsh_plugins.custom_insert_entries()}
    if plugin_id not in known:
        raise HTTPException(status_code=404, detail=f"插件不存在: {plugin_id}")
    dsh_plugins.set_plugin_enabled(plugin_id, body.enabled)
    await _restart_bridge()
    return {"ok": True, "id": plugin_id, "enabled": body.enabled}


@router.post("/install")
async def install_plugin(
    body: InstallRequest,
    user: User = Depends(require_admin),
):
    """安装 npm 插件包并 insert 进覆盖层加载（dsh plugin add 转发 pnpm，可能耗时数分钟）。"""
    _ensure_ready()
    try:
        result = await dsh_plugins.install_plugin(body.spec)
    except DshPluginError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await _restart_bridge()
    return {"ok": True, **{k: v for k, v in result.items() if k != "output"}, "output": result.get("output", "")}


@router.delete("/custom/{entry_id}", status_code=204)
async def remove_custom_plugin(
    entry_id: str,
    user: User = Depends(require_admin),
):
    """停用管理端安装的自定义插件（移除 insert 条目；已安装包保留在 profile，可重装）。"""
    _ensure_ready()
    if not dsh_plugins.remove_custom_entry(entry_id):
        raise HTTPException(status_code=404, detail=f"自定义插件不存在: {entry_id}")
    await _restart_bridge()


@router.post("/restart")
async def restart_dsh(user: User = Depends(require_admin)):
    """手动重建 dsh ACP 进程（手工编辑 patch 后使其生效）。"""
    _ensure_ready()
    await _restart_bridge()
    return {"ok": True}
