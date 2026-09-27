"""同步客户端安装包分发：版本信息 + 下载（网页端头像菜单下载、客户端自动更新用）。

文件名跟随品牌系统名（「<系统名>同步.exe」）；目录内放任意 .exe 即视为安装包。"""
import hashlib
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.config import settings
from app.models.brand_settings import BrandSettings
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sync-app", tags=["sync-app"])


def _find_exe() -> Path | None:
    folder = settings.sync_app_path
    if not folder.exists():
        return None
    exes = sorted(folder.glob("*.exe"), key=lambda p: p.stat().st_mtime, reverse=True)
    return exes[0] if exes else None


async def _download_name(db: AsyncSession) -> str:
    brand = await db.get(BrandSettings, 1)
    name = (brand.system_name if brand else "") or "榜样知识库"
    return f"{name}同步.exe"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()


@router.get("/version")
async def sync_app_version(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """当前安装包版本（sha256 即版本号，免维护）。"""
    exe = _find_exe()
    if exe is None:
        raise HTTPException(status_code=404, detail="暂未发布同步客户端安装包")
    return {
        "filename": await _download_name(db),
        "size": exe.stat().st_size,
        "sha256": _sha256(exe),
    }


@router.get("/download")
async def sync_app_download(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """下载同步客户端安装包（文件名随系统品牌名）。"""
    exe = _find_exe()
    if exe is None:
        raise HTTPException(status_code=404, detail="暂未发布同步客户端安装包")
    return FileResponse(exe, filename=await _download_name(db), media_type="application/octet-stream")
