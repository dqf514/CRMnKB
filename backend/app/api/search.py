"""全局搜索 API：一个入口搜客户/文件/笔记/任务/报告（Ctrl+K 命令面板用）。"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.customer import Customer
from app.models.library_file import LibraryFile
from app.models.notebook import Notebook, NotebookNote
from app.models.report import Report
from app.models.task import Task
from app.models.user import User
from app.services.permissions import accessible_ids

router = APIRouter(prefix="/search", tags=["search"])


@router.get("/global")
async def global_search(
    q: str = Query(..., min_length=1, max_length=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """跨资源模糊搜索，按类型分组返回。权限：文件/笔记只搜用户可读的。"""
    kw = f"%{q.strip()}%"
    limit = 5  # 每组最多返回数

    customers = (
        await db.execute(
            select(Customer)
            .where(
                Customer.tenant_id == user.tenant_id,
                Customer.deleted_at.is_(None),
                or_(
                    Customer.name.like(kw),
                    Customer.company.like(kw),
                    Customer.phone.like(kw),
                    Customer.email.like(kw),
                ),
            )
            .order_by(Customer.updated_at.desc())
            .limit(limit)
        )
    ).scalars().all()

    file_ids = await accessible_ids(db, user, "file")
    files = []
    if file_ids:
        files = (
            await db.execute(
                select(LibraryFile)
                .where(
                    LibraryFile.tenant_id == user.tenant_id,
                    LibraryFile.id.in_(file_ids),
                    LibraryFile.deleted_at.is_(None),
                    LibraryFile.file_name.like(kw),
                )
                .order_by(LibraryFile.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()

    nb_ids = await accessible_ids(db, user, "notebook")
    notes = []
    if nb_ids:
        notes = (
            await db.execute(
                select(NotebookNote)
                .join(Notebook, Notebook.id == NotebookNote.notebook_id)
                .where(
                    NotebookNote.tenant_id == user.tenant_id,
                    NotebookNote.notebook_id.in_(nb_ids),
                    Notebook.deleted_at.is_(None),
                    or_(
                        NotebookNote.title.like(kw),
                        NotebookNote.content.like(kw),
                    ),
                )
                .order_by(NotebookNote.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()

    tasks = (
        await db.execute(
            select(Task)
            .where(
                Task.tenant_id == user.tenant_id,
                Task.user_id == user.id,
                or_(Task.title.like(kw), Task.description.like(kw)),
            )
            .order_by(Task.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()

    reports = (
        await db.execute(
            select(Report)
            .where(
                Report.tenant_id == user.tenant_id,
                Report.user_id == user.id,
                Report.title.like(kw),
            )
            .order_by(Report.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()

    return {
        "customers": [
            {"id": c.id, "name": c.name, "sub": c.company or c.phone or ""}
            for c in customers
        ],
        "files": [
            {"id": f.id, "name": f.file_name, "sub": f.file_type}
            for f in files
        ],
        "notes": [
            {"id": n.id, "name": n.title, "sub": (n.content or "")[:60]}
            for n in notes
        ],
        "tasks": [
            {"id": t.id, "name": t.title, "sub": t.status}
            for t in tasks
        ],
        "reports": [
            {"id": r.id, "name": r.title, "sub": r.status}
            for r in reports
        ],
    }
