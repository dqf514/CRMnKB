"""系统设置（管理端）：品牌设置 + 解析文件格式开关。

- 品牌配置沿用 /api/v1/brand（公开读 + 管理员改），前端并入本页"品牌设置"Tab。
- 解析格式开关存 system_settings（key=parse_formats_enabled），本接口读写并刷新
  进程内缓存；关闭的格式上传/关联/重试时一律不解析。
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_admin
from app.models.user import User
from app.services.ingestion import (
    FORMAT_CATALOG,
    enabled_parse_exts,
    set_enabled_parse_exts,
)

router = APIRouter(prefix="/admin/settings", tags=["admin-settings"], dependencies=[Depends(require_admin)])


class FormatsUpdate(BaseModel):
    enabled: list[str] = Field(default_factory=list, description="启用的扩展名列表（小写含点，如 .pdf）")


@router.get("/formats")
async def list_formats(db: AsyncSession = Depends(get_db)):
    """解析文件格式清单（按类别分组 + 中文名 + 当前启用状态）。"""
    current = enabled_parse_exts()
    items = []
    for group in FORMAT_CATALOG:
        for ext in group["exts"]:
            items.append(
                {
                    "ext": ext,
                    "category": group["key"],
                    "category_label": group["label"],
                    "enabled": ext in current,
                }
            )
    return {"items": items}


@router.put("/formats")
async def update_formats(body: FormatsUpdate, db: AsyncSession = Depends(get_db)):
    """保存启用的解析格式，并立即刷新进程内缓存。"""
    enabled = await set_enabled_parse_exts(db, body.enabled)
    return {"ok": True, "enabled": sorted(enabled)}
