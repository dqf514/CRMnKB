import asyncio
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.api.deps import get_current_user, get_db
from app.models.customer import Customer
from app.models.knowledge_base import KnowledgeBase
from app.models.library_file import LibraryFile
from app.models.report import Report
from app.models.user import User
from app.schemas.kb import KbOut
from app.schemas.report import (
    ReportDetailOut,
    ReportGenerateRequest,
    ReportListOut,
    ReportOut,
    ReportReviseRequest,
)
from app.services.audit import record_audit
from app.services.pdf import render_pdf_from_html, render_presentation_pdf
from app.services.permissions import accessible_ids, filter_accessible_ids
from app.services.report import (
    REPORT_TYPES,
    default_date_range,
    generate_report,
    revise_report,
)

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/workbench-kb", response_model=KbOut | None)
async def get_workbench_kb(
    create: bool = Query(True),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """AI 工作台资料库（PR-H：现在复用 is_auto 机制，统一于 library 上传）。

    create=true（默认）不存在则自动创建（幂等）；create=false 仅查询，没有返回 null——
    前端在打开报告对话框时用 create=false 探测，避免仅浏览就产生数据。
    """
    from app.services.kb import get_or_create_auto_kb

    existing_kb = (
        await db.execute(
            select(KnowledgeBase).where(
                KnowledgeBase.tenant_id == user.tenant_id,
                KnowledgeBase.is_auto.is_(True),
                KnowledgeBase.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing_kb:
        return existing_kb
    if not create:
        return None
    return await get_or_create_auto_kb(db, user.tenant_id)


@router.post("/generate", response_model=ReportOut, status_code=201)
async def generate(
    body: ReportGenerateRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if body.type not in REPORT_TYPES:
        raise HTTPException(status_code=400, detail=f"不支持的报告类型: {body.type}")

    params: dict = {}
    if body.type == "customer_analysis":
        if body.customer_id is None:
            raise HTTPException(status_code=400, detail="customer_analysis 需提供 customer_id")
        customer = await db.get(Customer, body.customer_id)
        if customer is None or customer.tenant_id != user.tenant_id:
            raise HTTPException(status_code=404, detail="客户不存在")
        params["customer_id"] = body.customer_id
        title = f"客户分析报告 - {customer.name}"
    elif body.type == "custom":
        if not body.prompt or not body.prompt.strip():
            raise HTTPException(status_code=400, detail="custom 报告需提供 prompt")
        prompt = body.prompt.strip()
        kb_ids = sorted(set(body.kb_ids or []))
        if kb_ids:
            found = set(
                (
                    await db.execute(
                        select(KnowledgeBase.id).where(
                            KnowledgeBase.id.in_(kb_ids),
                            KnowledgeBase.tenant_id == user.tenant_id,
                            KnowledgeBase.deleted_at.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            if found != set(kb_ids):
                raise HTTPException(status_code=400, detail="存在无权访问或已删除的知识库")
            # 用户级 ACL：过滤掉当前用户不可读的知识库，全部不可读则拒绝
            kb_ids = await filter_accessible_ids(db, user, "kb", kb_ids)
            if not kb_ids:
                raise HTTPException(status_code=400, detail="所选知识库均无阅读权限")
        else:
            # 未指定时限定为"当前用户可读的知识库"，而非全租户
            kb_ids = await accessible_ids(db, user, "kb")
        file_ids = sorted(set(body.file_ids or []))
        if file_ids:
            found_files = set(
                (
                    await db.execute(
                        select(LibraryFile.id).where(
                            LibraryFile.id.in_(file_ids),
                            LibraryFile.tenant_id == user.tenant_id,
                            LibraryFile.deleted_at.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            if found_files != set(file_ids):
                raise HTTPException(status_code=400, detail="存在无权访问或已删除的文件")
            # 用户级 ACL：过滤掉当前用户不可读的文件，全部不可读则拒绝
            file_ids = await filter_accessible_ids(db, user, "file", file_ids)
            if not file_ids:
                raise HTTPException(status_code=400, detail="所选文件均无阅读权限")
        params = {
            "prompt": prompt,
            "kb_ids": kb_ids,
            "file_ids": file_ids,
            "format": "html",
        }
        title = prompt[:30] + "…"
    else:
        default_start, default_end = default_date_range(body.type)
        start = body.start_date or default_start
        end = body.end_date or default_end
        params["start_date"] = str(start)
        params["end_date"] = str(end)
        type_name = "销售周报" if body.type == "sales_weekly" else "销售月报"
        title = f"{type_name} {start} ~ {end}"

    # 自定义 AI 生成超时（秒），透传给生成任务的 chat 调用
    if body.timeout is not None:
        params["timeout"] = body.timeout
    # 报告语言（zh/en/zh_en）
    if body.language not in ("zh", "en", "zh_en"):
        raise HTTPException(status_code=400, detail="language 仅支持 zh / en / zh_en")
    params["language"] = body.language

    # Agent 模式：依赖 dsh 基座总开关，关闭时直接拒绝（前端开关也随之下架，双保险）
    if body.agent:
        if not settings.DSH_AGENT_ENABLED:
            raise HTTPException(status_code=400, detail="Agent 模式未启用（DSH_AGENT_ENABLED 关闭）")
        params["agent"] = True

    report = Report(
        tenant_id=user.tenant_id,
        user_id=user.id,
        type=body.type,
        title=title,
        status="generating",
        params=params,
    )
    db.add(report)
    if body.type == "custom":
        await db.flush()  # 取 report.id 供审计记录
        record_audit(db, user, "create", "report", report.id, {"type": "custom"})
    await db.commit()
    await db.refresh(report)
    background_tasks.add_task(generate_report, report.id)
    return report


@router.get("", response_model=ReportListOut)
async def list_reports(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(Report).where(Report.tenant_id == user.tenant_id)
    # 报告按属主隔离：非 admin 仅能看到自己的报告（admin 绕过，与全局 ACL 口径一致）
    if user.role != "admin":
        stmt = stmt.where(Report.user_id == user.id)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    stmt = stmt.order_by(Report.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    return ReportListOut(items=result.scalars().all(), total=total or 0)


async def _get_report_or_404(db: AsyncSession, user: User, report_id: int) -> Report:
    report = await db.get(Report, report_id)
    if report is None or report.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="报告不存在")
    # 非 admin 仅本人可见/可操作，他人报告按不存在处理（不暴露存在性）
    if user.role != "admin" and report.user_id != user.id:
        raise HTTPException(status_code=404, detail="报告不存在")
    return report


@router.get("/{report_id}", response_model=ReportDetailOut)
async def get_report(
    report_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await _get_report_or_404(db, user, report_id)


@router.delete("/{report_id}", status_code=204)
async def delete_report(
    report_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    report = await _get_report_or_404(db, user, report_id)
    await db.delete(report)
    await db.commit()


@router.get("/{report_id}/pdf")
async def download_pdf(
    report_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """HTML 报告导出 A4 PDF（Playwright 服务端打印）。仅 format=html 且 ready 可用。"""
    report = await _get_report_or_404(db, user, report_id)
    if report.format != "html":
        raise HTTPException(status_code=400, detail="仅 HTML 报告支持导出 PDF")
    if report.status != "ready":
        raise HTTPException(status_code=409, detail="报告尚未生成完成，暂不可导出")
    try:
        pdf = await asyncio.to_thread(render_pdf_from_html, report.content or "")
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    record_audit(db, user, "export", "report", report.id, {"format": "pdf"})
    await db.commit()
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            # RFC 5987 filename* UTF-8 编码，兼容中文标题
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(report.title)}.pdf",
        },
    )


@router.get("/{report_id}/presentation.pdf")
async def download_presentation_pdf(
    report_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """演示版（16:9 幻灯片）导出横向 PDF，一页一幻灯片。需已生成演示版。"""
    report = await _get_report_or_404(db, user, report_id)
    pres = (report.params or {}).get("presentation_html")
    if not pres:
        raise HTTPException(status_code=400, detail="该报告暂无演示版，请重新生成")
    try:
        pdf = await asyncio.to_thread(render_presentation_pdf, pres)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    record_audit(db, user, "export", "report", report.id, {"format": "presentation_pdf"})
    await db.commit()
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            # RFC 5987 filename* UTF-8 编码，兼容中文标题
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(report.title)}-演示.pdf",
        },
    )


@router.post("/{report_id}/revise", response_model=ReportOut)
async def revise(
    report_id: int,
    body: ReportReviseRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """对话式修改：仅 custom 类型且 status=ready 可改，后台任务 revise_report 执行。"""
    report = await _get_report_or_404(db, user, report_id)
    if report.type != "custom":
        raise HTTPException(status_code=400, detail="仅自定义报告支持对话式修改")
    if report.status != "ready":
        raise HTTPException(status_code=409, detail="报告正在生成/修改中或已失败，暂不可修改")
    report.status = "revising"
    record_audit(
        db, user, "update", "report", report.id,
        {"action": "revise", "instruction": body.instruction[:100]},
    )
    await db.commit()
    await db.refresh(report)
    background_tasks.add_task(revise_report, report.id, body.instruction)
    return report
