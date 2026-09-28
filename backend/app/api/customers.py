import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.customer import Customer
from app.models.follow_up import FollowUpRecord
from app.models.system_setting import SystemSetting
from app.models.user import User
from app.schemas.customer import (
    DDQ_STATUSES,
    CustomerCreate,
    CustomerListOut,
    CustomerOut,
    CustomerProfileOut,
    CustomerUpdate,
    EmailDraftOut,
    EmailDraftRequest,
)
from app.schemas.kb import KbOut
from app.services.audit import record_audit
from app.services.customer_io import (
    EXPORT_MAX_ROWS,
    build_export,
    build_template,
    find_duplicate_customers,
    parse_customers_xlsx,
)
from app.services.email_draft import build_email_draft_prompt, parse_email_draft
from app.services.kb import get_or_create_customer_kb
from app.services.llm import resolve_chat_llm
from app.services.pipeline_brief import generate_brief
from app.services.profile import generate_profile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/customers", tags=["customers"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _customer_filter_stmt(
    tenant_id: int,
    keyword: str | None = None,
    status: str | None = None,
    industry: str | None = None,
    tag: str | None = None,
    ddq_status: str | None = None,
):
    """客户列表/导出共用的筛选（默认排除已软删）。"""
    stmt = select(Customer).where(
        Customer.tenant_id == tenant_id, Customer.deleted_at.is_(None)
    )
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(
            or_(
                Customer.name.like(like),
                Customer.phone.like(like),
                Customer.email.like(like),
                Customer.company.like(like),
            )
        )
    if status:
        stmt = stmt.where(Customer.status == status)
    if ddq_status:
        stmt = stmt.where(Customer.ddq_status == ddq_status)
    if industry:
        # JSONB ? 操作符：industries 数组包含该行业名称即命中
        stmt = stmt.where(Customer.industries.has_key(industry))  # noqa: W601
    if tag:
        stmt = stmt.where(Customer.tags.has_key(tag))  # noqa: W601
    return stmt


@router.get("", response_model=CustomerListOut)
async def list_customers(
    keyword: str | None = Query(None),
    status: str | None = Query(None),
    industry: str | None = Query(None),
    tag: str | None = Query(None),
    ddq_status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = _customer_filter_stmt(user.tenant_id, keyword, status, industry, tag, ddq_status)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    stmt = stmt.order_by(Customer.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    return CustomerListOut(items=result.scalars().all(), total=total or 0)


@router.post("", response_model=CustomerOut, status_code=201)
async def create_customer(
    body: CustomerCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    customer = Customer(
        tenant_id=user.tenant_id,
        owner_id=user.id,
        **body.model_dump(),
    )
    db.add(customer)
    await db.flush()
    record_audit(db, user, "create", "customer", customer.id, {"name": customer.name}, _client_ip(request))
    await db.commit()
    await db.refresh(customer)
    return customer


# ---------------------------------------------------------------------------
# 查重 / Excel 模板 / 导入 / 导出（必须先于 /{customer_id} 注册）
# ---------------------------------------------------------------------------

@router.get("/duplicates")
async def find_duplicates(
    name: str | None = Query(None),
    phone: str | None = Query(None),
    exclude_id: int | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """疑似重复客户 Top5：名称 trgm 相似度 > 0.4 或电话精确匹配。"""
    if not name and not phone:
        return []
    return await find_duplicate_customers(db, user.tenant_id, name, phone, exclude_id)


@router.get("/import-template")
async def import_template(user: User = Depends(get_current_user)):
    """下载客户导入 xlsx 模板。"""
    content = await asyncio.to_thread(build_template)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''customers_template.xlsx"},
    )


@router.post("/import")
async def import_customers(
    file: UploadFile,
    request: Request,
    mode: str = Query("skip"),  # skip / overwrite
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if mode not in ("skip", "overwrite"):
        raise HTTPException(status_code=400, detail="mode 仅支持 skip / overwrite")
    data = await file.read()
    try:
        rows, errors = await asyncio.to_thread(parse_customers_xlsx, data)
    except Exception:
        raise HTTPException(status_code=400, detail="文件解析失败，请使用下载的模板")
    created = updated = skipped = 0
    for row in rows:
        dups = await find_duplicate_customers(db, user.tenant_id, row["name"], row["phone"])
        if dups:
            if mode == "skip":
                skipped += 1
                continue
            existing = await db.get(Customer, dups[0]["id"])
            if existing is None or existing.tenant_id != user.tenant_id or existing.deleted_at is not None:
                skipped += 1
                continue
            for field in ("company", "position", "phone", "email", "wechat", "industries", "tags", "attributes"):
                value = row.get(field)
                if value:  # 空值不覆盖已有数据
                    setattr(existing, field, value)
            updated += 1
            continue
        customer = Customer(tenant_id=user.tenant_id, owner_id=user.id, name=row["name"])
        for field in ("company", "position", "phone", "email", "wechat", "industries", "tags", "attributes"):
            setattr(customer, field, row.get(field))
        db.add(customer)
        created += 1
    record_audit(
        db, user, "import", "customer", None,
        {"created": created, "updated": updated, "skipped": skipped, "errors": len(errors)},
        _client_ip(request),
    )
    await db.commit()
    return {"created": created, "updated": updated, "skipped": skipped, "errors": errors}


@router.get("/export")
async def export_customers(
    request: Request,
    keyword: str | None = Query(None),
    status: str | None = Query(None),
    industry: str | None = Query(None),
    tag: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """按列表同款筛选导出 xlsx，上限 1 万行。"""
    stmt = _customer_filter_stmt(user.tenant_id, keyword, status, industry, tag)
    stmt = stmt.order_by(Customer.created_at.desc()).limit(EXPORT_MAX_ROWS)
    customers = (await db.execute(stmt)).scalars().all()
    content = await asyncio.to_thread(build_export, list(customers))
    record_audit(db, user, "export", "customer", None, {"count": len(customers)}, _client_ip(request))
    await db.commit()
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''customers.xlsx"},
    )


async def _get_customer_or_404(db: AsyncSession, tenant_id: int, customer_id: int) -> Customer:
    customer = await db.get(Customer, customer_id)
    if (
        customer is None
        or customer.tenant_id != tenant_id
        or customer.deleted_at is not None
    ):
        raise HTTPException(status_code=404, detail="客户不存在")
    return customer


@router.get("/{customer_id}", response_model=CustomerOut)
async def get_customer(
    customer_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await _get_customer_or_404(db, user.tenant_id, customer_id)


@router.put("/{customer_id}", response_model=CustomerOut)
async def update_customer(
    customer_id: int,
    body: CustomerUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    customer = await _get_customer_or_404(db, user.tenant_id, customer_id)
    updates = body.model_dump(exclude_unset=True)
    # ddq_status 取值手工校验（schema 层用 Literal 会返回 422，契约要求 400）
    if "ddq_status" in updates and updates["ddq_status"] not in DDQ_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"ddq_status 仅支持 {'/'.join(DDQ_STATUSES)}",
        )
    for field, value in updates.items():
        setattr(customer, field, value)
    record_audit(
        db, user, "update", "customer", customer.id,
        {"fields": sorted(updates)}, _client_ip(request),
    )
    await db.commit()
    await db.refresh(customer)
    return customer


@router.delete("/{customer_id}", status_code=204)
async def delete_customer(
    customer_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    customer = await _get_customer_or_404(db, user.tenant_id, customer_id)
    # 软删除：连同专属知识库一起进回收站（恢复时整体找回；彻底删除走 /recycle-bin）
    from app.models.knowledge_base import KnowledgeBase

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    customer.deleted_at = now
    kbs = (
        (
            await db.execute(
                select(KnowledgeBase).where(
                    KnowledgeBase.customer_id == customer_id,
                    KnowledgeBase.type == "customer",
                    KnowledgeBase.tenant_id == user.tenant_id,
                    KnowledgeBase.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for kb in kbs:
        kb.deleted_at = now
    # 客户删除时联动软删其附件文件（否则回收站里的客户附件仍可访问）
    from app.models.library_file import LibraryFile

    files = (
        (
            await db.execute(
                select(LibraryFile).where(
                    LibraryFile.customer_id == customer_id,
                    LibraryFile.tenant_id == user.tenant_id,
                    LibraryFile.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for f in files:
        f.deleted_at = now
    record_audit(db, user, "delete", "customer", customer.id, {"name": customer.name}, _client_ip(request))
    await db.commit()


# ---------------------------------------------------------------------------
# 客户专属知识库与 AI 画像
# ---------------------------------------------------------------------------

@router.get("/{customer_id}/kb", response_model=KbOut)
async def get_customer_kb(
    customer_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """客户专属知识库：无则自动创建（"{客户名}-专属知识库", type=customer）。"""
    customer = await _get_customer_or_404(db, user.tenant_id, customer_id)
    kb = await get_or_create_customer_kb(db, user.tenant_id, customer.id, customer.name)
    await db.commit()
    await db.refresh(kb)
    from app.api.kbs import _kb_out, _kb_stats

    stats = await _kb_stats(db, user.tenant_id)
    return _kb_out(kb, *stats.get(kb.id, (0, 0)))


@router.get("/{customer_id}/profile", response_model=CustomerProfileOut)
async def get_customer_profile(
    customer_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    customer = await _get_customer_or_404(db, user.tenant_id, customer_id)
    return CustomerProfileOut(
        profile=customer.profile,
        status=customer.profile_status,
        updated_at=customer.profile_updated_at,
    )


@router.post("/{customer_id}/profile/generate", status_code=202)
async def generate_customer_profile(
    customer_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    customer = await _get_customer_or_404(db, user.tenant_id, customer_id)
    customer.profile_status = "generating"
    await db.commit()
    background_tasks.add_task(generate_profile, customer.id)
    return {"status": "generating"}


# ---------------------------------------------------------------------------
# P1 Pipeline 阶段简报 / P2 AI 邮件草稿
# ---------------------------------------------------------------------------

@router.post("/{customer_id}/brief/refresh", status_code=202)
async def refresh_customer_brief(
    customer_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """后台重新生成 AI 阶段简报（结果写回 customer.ai_brief，随客户详情下发）。"""
    customer = await _get_customer_or_404(db, user.tenant_id, customer_id)
    background_tasks.add_task(generate_brief, customer.id)
    return {"ok": True}


@router.post("/{customer_id}/email-draft", response_model=EmailDraftOut)
async def create_email_draft(
    customer_id: int,
    body: EmailDraftRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """AI 邮件草稿：客户资料 + 阶段简报 + 最近 5 条跟进 + Email Guide + 用户意图 → LLM。"""
    customer = await _get_customer_or_404(db, user.tenant_id, customer_id)
    guide_row = await db.get(SystemSetting, "email_guide")
    guide = (guide_row.value or "") if guide_row else ""
    followups = (
        (
            await db.execute(
                select(FollowUpRecord)
                .where(FollowUpRecord.customer_id == customer_id)
                .order_by(FollowUpRecord.created_at.desc())
                .limit(5)
            )
        )
        .scalars()
        .all()
    )
    prompt = build_email_draft_prompt(
        customer, followups, customer.ai_brief, guide, body.intent, body.language
    )
    chat_llm = await resolve_chat_llm(caller="email_draft", tenant_id=user.tenant_id)
    text = await chat_llm.chat(prompt)
    draft = parse_email_draft(text)
    if not draft:
        raise HTTPException(status_code=502, detail="AI 草稿生成失败，请重试")
    return EmailDraftOut(**draft)
