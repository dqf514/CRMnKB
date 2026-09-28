"""每日工作台 / 日报 / 随手记捕获 API。"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.customer import Customer
from app.models.library_file import LibraryFile
from app.models.notebook import Notebook, NotebookNote
from app.models.user import User
from app.services.dashboard import daily_report, today_overview

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/today")
async def get_today(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """今日工作台聚合：今日/逾期任务、久未跟进客户、今日新增、未读通知、团队动态。"""
    return await today_overview(db, user)


@router.get("/daily-report")
async def get_daily_report(
    date: str | None = Query(None),
    team: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """日报：数据驱动（跟进/完成任务/上传/笔记/新增客户）。team=true 仅 admin。"""
    return await daily_report(db, user, date, team)


class CaptureRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    # 附件：文档库文件 id（前端先上传到文档库默认目录再带入），最多 9 个
    file_ids: list[int] | None = Field(default=None, max_length=9)


@router.post("/capture", status_code=201)
async def quick_capture(
    body: CaptureRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """随手记：存入个人「随手记」笔记本；识别文本中提到的客户名，返回建议关联。"""
    # 个人随手记笔记本（每人一个，幂等）
    nb = (
        await db.execute(
            select(Notebook).where(
                Notebook.tenant_id == user.tenant_id,
                Notebook.created_by == user.id,
                Notebook.name == "随手记",
                Notebook.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if nb is None:
        nb = Notebook(
            tenant_id=user.tenant_id,
            name="随手记",
            description="工作台快速捕获",
            created_by=user.id,
        )
        db.add(nb)
        await db.flush()

    # 附件：校验文件归属本租户且未软删，并入随手记笔记本的源文件（供该工作区问答检索）
    attachments: list[dict] = []
    if body.file_ids:
        files = (
            await db.execute(
                select(LibraryFile).where(
                    LibraryFile.id.in_(body.file_ids),
                    LibraryFile.tenant_id == user.tenant_id,
                    LibraryFile.deleted_at.is_(None),
                )
            )
        ).scalars().all()
        attachments = [{"id": f.id, "name": f.file_name} for f in files]
        if attachments:
            merged = list(dict.fromkeys([*(nb.source_file_ids or []), *(f.id for f in files)]))
            nb.source_file_ids = merged  # JSONB 整体重赋值以触发变更检测

    first_line = body.text.strip().splitlines()[0][:40]
    note = NotebookNote(
        tenant_id=user.tenant_id,
        notebook_id=nb.id,
        title=first_line or "随手记",
        content=body.text.strip(),
        source_type="capture",
        source_ref={"attachments": attachments} if attachments else None,
    )
    db.add(note)
    await db.commit()
    await db.refresh(note)

    # 客户名识别：文本里出现的客户名 → 建议关联（仅取活跃客户，最多 5 个）
    customers = (
        await db.execute(
            select(Customer.id, Customer.name).where(
                Customer.tenant_id == user.tenant_id,
                Customer.deleted_at.is_(None),
            )
        )
    ).all()
    text = body.text
    suggested = [
        {"id": cid, "name": name}
        for cid, name in customers
        if name and len(name) >= 2 and name in text
    ][:5]
    return {
        "note_id": note.id,
        "notebook_id": nb.id,
        "suggested_customers": suggested,
        "attachments": attachments,
    }
