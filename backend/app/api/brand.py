"""品牌配置：系统名称 + 自定义 logo。

- GET  /api/v1/brand          公开读取（登录页/布局），无需鉴权
- PUT  /api/v1/brand          管理员改系统名称
- POST /api/v1/brand/logo     管理员上传自定义 logo（覆盖旧 logo）
"""
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_admin
from app.config import settings
from app.models.brand_settings import BrandSettings
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/brand", tags=["brand"])

_LOGO_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".svg"}
_MAX_LOGO_BYTES = 5 * 1024 * 1024


class BrandUpdate(BaseModel):
    system_name: str = Field(min_length=1, max_length=100)


async def _get_row(db: AsyncSession) -> BrandSettings:
    row = await db.get(BrandSettings, 1)
    if row is None:
        row = BrandSettings(id=1, system_name="榜样知识库")
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


def _logo_url(row: BrandSettings) -> str:
    if row.logo_path:
        return f"/brand/{row.logo_path}"
    return "/logo-256.png"  # 前端内置默认 logo


async def _brand_dict(db: AsyncSession) -> dict:
    row = await _get_row(db)
    return {
        "system_name": row.system_name,
        "logo_url": _logo_url(row),
        # dsh agent 功能开关随公开引导配置下发，前端据此决定聊天默认模式
        "dsh_agent_enabled": settings.DSH_AGENT_ENABLED,
    }


@router.get("")
async def get_public_brand(db: AsyncSession = Depends(get_db)):
    """公开品牌信息：系统名 + logo URL，登录页/布局读取，无需鉴权。"""
    return await _brand_dict(db)


@router.put("")
async def update_brand(
    body: BrandUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """修改系统名称。"""
    row = await _get_row(db)
    row.system_name = body.system_name.strip() or "榜样知识库"
    await db.commit()
    await db.refresh(row)
    return await _brand_dict(db)


@router.post("/logo")
async def upload_logo(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """上传自定义 logo（png/jpg/jpeg/webp/svg，≤5MB），覆盖旧 logo。"""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in _LOGO_EXTS:
        raise HTTPException(status_code=400, detail=f"仅支持 {'/'.join(sorted(_LOGO_EXTS))} 图片格式")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="空文件")
    if len(data) > _MAX_LOGO_BYTES:
        raise HTTPException(status_code=400, detail="logo 超过 5MB")

    settings.brand_path.mkdir(parents=True, exist_ok=True)
    target = settings.brand_path / f"logo{ext}"
    # 清理旧 logo（可能是不同扩展名），只保留当前一个
    for old in settings.brand_path.glob("logo.*"):
        if old.name != target.name:
            try:
                old.unlink(missing_ok=True)
            except OSError:
                pass
    target.write_bytes(data)

    row = await _get_row(db)
    row.logo_path = target.name
    await db.commit()
    await db.refresh(row)
    return await _brand_dict(db)
