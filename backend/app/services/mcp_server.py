"""知识库 MCP server（Streamable HTTP，dsh 基座实施方案阶段 1）。

把现有 RAG 检索能力包装成 MCP 工具供 dsh 基座消费：
- kb_search：混合检索（blend）+ Small2Big 扩展，返回当前用户可读的切片
- kb_read_doc：读取整份文档的切片全文

挂载点 /api/mcp（见 main.py）。鉴权方案：HTTP Authorization 头携带 dsh 专用
JWT（aud=dsh-mcp，ACP 阶段由 acp_bridge 在每会话 session/new|resume 时新签，
经 mcpServers headers 注入，不落盘）；选 header 而非
"/api/mcp/{token}" 路径方案，因为项目安全约定禁止把令牌放进 URL（防访问日志
窃取，见 api/deps.py 文件令牌注释），且 MCP streamable-http 规范本就走 header。

信任边界：dsh 进程与 LLM 均不可信——令牌只授予知识库只读能力（其余业务接口
拒绝 aud=dsh-mcp），ACL 在本模块工具实现内按 user_id 强制（复用 permissions 服务）。
"""
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import jwt
from mcp.server.fastmcp import Context, FastMCP
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from app.config import settings
from app.core.security import MCP_TOKEN_AUDIENCE, decode_token
from app.database import AsyncSessionLocal
from app.models.chunk import DocumentChunk
from app.models.customer import Customer
from app.models.document import KnowledgeDocument
from app.models.follow_up import FollowUpRecord
from app.models.knowledge_base import KnowledgeBase
from app.models.opportunity import Opportunity
from app.models.skill import Skill as SkillRow
from app.models.task import Task
from app.models.user import User
from app.services.agent_approvals import create_approval
from app.services.llm import resolve_embed_llm
from app.services.memory import add_memory, delete_memory, list_memories, search_memories
from app.services.permissions import accessible_ids, get_access, satisfies
from app.services.skills.registry import execute_skill, get_enabled_skills
from app.services.rag import (
    _find_block,
    attach_file_info,
    expand_contexts,
    reciprocal_rank_fusion,
    search_chunks_blend,
    search_chunks_keyword,
    search_chunks_vector,
)

logger = logging.getLogger(__name__)


class McpAuthError(Exception):
    """MCP 令牌/账号校验失败（工具层收口为 MCP error 结果）。"""


class McpToolError(Exception):
    """工具业务错误（如文档不存在、无权限），向调用方返回 error 结果。"""


# FastMCP server：serverName 在 dsh 侧 patch yml 里配置为 kb，
# 工具以 mcp__kb__kb_search / mcp__kb__kb_read_doc 暴露给模型。
# streamable_http_path 与挂载点一致（/api/mcp）：子应用经 starlette Route 直接
# 挂进 FastAPI（不用 Mount——Mount 对 POST /api/mcp 会 307 到带尾斜杠路径，
# MCP 客户端对 307 的兼容性不可靠），路径不重写，因此两边路径必须相同。
kb_mcp = FastMCP(
    "kb",
    instructions=(
        "企业内部知识库 + CRM（dsh 基座阶段 3）。只读工具：kb_search 混合检索切片、"
        "kb_read_doc 读整份文档、kb_list 列出可读知识库、crm_list_customers 客户全量名单与总数、"
        "crm_search_customers 模糊检索客户、crm_get_customer 客户详情、"
        "crm_list_followups 跟进清单、crm_list_opportunities 商机清单、crm_list_tasks 任务清单、"
        "crm_stats 经营概览统计、skill_list 列出已启用自定义工具、web_search 联网搜索、web_fetch 抓取网页。"
        "个人记忆：memory_list/memory_search 查用户长期记忆，memory_save 保存用户长期偏好/背景"
        "（跨会话生效），memory_delete 删除错误记忆。"
        "写工具（crm_create_customer / crm_update_customer / crm_delete_customer / "
        "crm_create_opportunity / crm_create_task / crm_add_followup / mail_draft_create / "
        "skill_create_api）不直接生效，只创建审批单，管理员批准后由系统自动执行。"
        "skill_call 用于调用 skill_list 列出的自定义工具。"
        "引用联网搜索结果时，在相关语句末尾用 [序号](URL) 标注来源"
        "（如「……同比增长 5%[1](https://example.com/a)」），前端会渲染为可点击的来源上标。"
        "只能访问令牌所属用户有权限的资料。"
    ),
    streamable_http_path="/api/mcp",
)


def _unauthorized(detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=401)


def decode_mcp_authorization(headers: list[tuple[bytes, bytes]]) -> dict:
    """从 ASGI 头列表解析并校验 dsh MCP 令牌；失败抛 McpAuthError。纯函数（不起 DB）。"""
    auth: str | None = None
    for key, value in headers:
        if key.lower() == b"authorization":
            auth = value.decode("latin-1")
            break
    if not auth or not auth.startswith("Bearer "):
        raise McpAuthError("未提供认证令牌")
    try:
        payload = decode_token(auth[len("Bearer "):].strip(), audience=MCP_TOKEN_AUDIENCE)
        int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise McpAuthError("令牌无效或已过期") from None
    return payload


class BearerMcpAuthMiddleware:
    """纯 ASGI 中间件：/api/mcp 每个请求强制 dsh MCP 令牌，
    通过则把 JWT payload 放进 scope["mcp.token_payload"] 供工具层解析用户。

    不查库（初始化握手也要过这关，保持轻量）；账号态/改密失效在工具层
    resolve_mcp_user 里按次校验。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        try:
            scope["mcp.token_payload"] = decode_mcp_authorization(scope.get("headers", []))
        except McpAuthError as exc:
            await _unauthorized(str(exc))(scope, receive, send)
            return
        await self.app(scope, receive, send)


async def resolve_mcp_user(db: AsyncSession, payload: dict) -> User:
    """由令牌 payload 解析用户并做账号态/改密失效校验（对齐 api/deps.py 的规则）。"""
    user = await db.get(User, int(payload["sub"]))
    if user is None:
        raise McpAuthError("用户不存在")
    if getattr(user, "status", 1) == 0:
        raise McpAuthError("账号已停用")
    changed_at = getattr(user, "password_changed_at", None)
    iat = payload.get("iat")
    if changed_at is not None and iat is not None:
        if iat < changed_at.replace(tzinfo=timezone.utc).timestamp():
            raise McpAuthError("令牌已失效，请重启 dsh 进程刷新令牌")
    return user


def _payload_from_ctx(ctx: Context) -> dict:
    """从 FastMCP 上下文取当前 HTTP 请求里中间件放入的令牌 payload。"""
    request = ctx.request_context.request if ctx is not None else None
    payload = getattr(request, "scope", {}).get("mcp.token_payload") if request else None
    if not payload:
        raise McpAuthError("未通过 MCP 令牌校验")
    return payload


async def kb_search_impl(
    db: AsyncSession, user: User, query: str, top_k: int = 5
) -> list[dict]:
    """混合检索当前用户可读范围内的切片（MCP 工具 kb_search 的实现，可独立测试）。

    复用 rag.py 管线：blend 二阶段检索（失败回退 embedding+trgm RRF）→ Small2Big
    扩展 → 文件信息回填。不做 LLM 问答、不做 rerank（dsh 侧 agent 自行判断相关性）。
    """
    top_k = max(1, min(int(top_k), 20))
    # ACL：只检索用户可读的 KB（管理员为全部）
    kb_ids = await accessible_ids(db, user, "kb")
    if not kb_ids:
        return []
    embed_llm = await resolve_embed_llm(caller="mcp", tenant_id=user.tenant_id)
    query_vec = (await embed_llm.embed([query]))[0]
    try:
        hits = await search_chunks_blend(
            db, user.tenant_id, query_vec, query, top_k,
            settings.RAG_SCORE_THRESHOLD, kb_ids, None,
        )
    except Exception as exc:
        logger.warning("blend 检索失败，回退 embedding+trgm: %s", exc)
        hits = []
    if not hits:
        vector_rows = await search_chunks_vector(db, user.tenant_id, query_vec, top_k * 2, kb_ids, None)
        if settings.RAG_HYBRID:
            keyword_rows = await search_chunks_keyword(db, user.tenant_id, query, top_k * 2, kb_ids, None)
            hits = reciprocal_rank_fusion(vector_rows, keyword_rows)[:top_k]
        else:
            hits = vector_rows[:top_k]
    if not hits:
        return []
    blocks = await expand_contexts(db, user.tenant_id, hits, settings.RAG_NEIGHBOR_WINDOW)
    # 检索行不直接带 kb_id，补一张 doc_id → kb_id 映射
    doc_ids = {h["doc_id"] for h in hits}
    rows = (
        await db.execute(
            select(KnowledgeDocument.id, KnowledgeDocument.kb_id).where(
                KnowledgeDocument.id.in_(doc_ids)
            )
        )
    ).all()
    kb_map = {r.id: r.kb_id for r in rows}
    results = []
    for h in hits:
        block = _find_block(blocks, h)
        results.append(
            {
                "chunk_id": h["chunk_id"],
                "doc_id": h["doc_id"],
                "kb_id": kb_map.get(h["doc_id"]),
                "doc_title": h["doc_title"],
                "score": round(float(h["score"]), 4),
                # Small2Big 扩展后的上下文块（无扩展块时退回命中切片原文）
                "content": block["content"] if block else h["content"],
            }
        )
    await attach_file_info(db, results)
    return results


async def kb_read_doc_impl(
    db: AsyncSession, user: User, doc_id: int, max_chars: int = 8000
) -> dict:
    """读取整份文档的切片全文（MCP 工具 kb_read_doc 的实现，可独立测试）。

    ACL 与 RAG 检索口径一致：按文档所属 KB 的可读权限判断（管理员放行）。
    """
    max_chars = max(200, min(int(max_chars), 100000))
    doc = await db.get(KnowledgeDocument, int(doc_id))
    if doc is None or doc.tenant_id != user.tenant_id:
        raise McpToolError("文档不存在")
    if user.role != "admin":
        perm = await get_access(db, user.tenant_id, user.id, "kb", doc.kb_id) if doc.kb_id else None
        if not satisfies(perm, "read"):
            raise McpToolError("没有该文档的访问权限")
    rows = (
        await db.execute(
            select(DocumentChunk.chunk_index, DocumentChunk.content)
            .where(
                DocumentChunk.document_id == doc.id,
                DocumentChunk.tenant_id == user.tenant_id,
            )
            .order_by(DocumentChunk.chunk_index)
        )
    ).all()
    full = "\n".join(r.content for r in rows)
    return {
        "doc_id": doc.id,
        "kb_id": doc.kb_id,
        "title": doc.title,
        "status": doc.status,
        "chunk_count": len(rows),
        "total_chars": len(full),
        "truncated": len(full) > max_chars,
        "content": full[:max_chars],
    }


@kb_mcp.tool(
    name="kb_search",
    description="在企业知识库中检索与问题相关的文档切片，返回 doc_id、标题、内容、相关度与文件信息。只能检索当前用户有权限的资料。",
)
async def _kb_search_tool(query: str, top_k: int = 5, ctx: Context = None) -> list[dict]:
    async with AsyncSessionLocal() as db:
        user = await resolve_mcp_user(db, _payload_from_ctx(ctx))
        return await kb_search_impl(db, user, query, top_k)


@kb_mcp.tool(
    name="kb_read_doc",
    description="按 doc_id 读取知识库文档的完整内容（按切片序号拼接），超长时按 max_chars 截断。需当前用户对该文档所属知识库有读权限。",
)
async def _kb_read_doc_tool(doc_id: int, max_chars: int = 8000, ctx: Context = None) -> dict:
    async with AsyncSessionLocal() as db:
        user = await resolve_mcp_user(db, _payload_from_ctx(ctx))
        return await kb_read_doc_impl(db, user, doc_id, max_chars)


# ---------------------------------------------------------------------------
# 阶段 2：知识库发现 + CRM 只读工具 + 审批制写工具
#
# CRM 权限口径：CRM 数据（customers/follow_up_records/opportunities）没有内容级
# ACL——permissions 服务只管 kb/file/folder/notebook 四类资源；CRM 是租户内全员
# 可见（与 /customers 等 REST 端点口径一致），因此 CRM 工具只按 tenant_id 隔离、
# 排除软删，不再做逐条权限过滤。
# ---------------------------------------------------------------------------


async def kb_list_impl(db: AsyncSession, user: User) -> list[dict]:
    """当前用户可读知识库列表（MCP 工具 kb_list 的实现，可独立测试）。

    ACL 口径与 kb_search 一致（accessible_ids），帮 agent 发现检索范围。
    """
    kb_ids = await accessible_ids(db, user, "kb")
    if not kb_ids:
        return []
    kbs = (
        (
            await db.execute(
                select(KnowledgeBase)
                .where(
                    KnowledgeBase.id.in_(kb_ids),
                    KnowledgeBase.tenant_id == user.tenant_id,
                    KnowledgeBase.deleted_at.is_(None),
                )
                .order_by(KnowledgeBase.id)
            )
        )
        .scalars()
        .all()
    )
    if not kbs:
        return []
    count_rows = (
        await db.execute(
            select(KnowledgeDocument.kb_id, func.count(KnowledgeDocument.id))
            .where(
                KnowledgeDocument.tenant_id == user.tenant_id,
                KnowledgeDocument.kb_id.in_([k.id for k in kbs]),
            )
            .group_by(KnowledgeDocument.kb_id)
        )
    ).all()
    counts = {r[0]: r[1] for r in count_rows}
    return [
        {
            "kb_id": kb.id,
            "name": kb.name,
            "type": kb.type,
            "doc_count": int(counts.get(kb.id, 0)),
        }
        for kb in kbs
    ]


def _customer_summary(c: Customer) -> dict:
    return {
        "customer_id": c.id,
        "name": c.name,
        "company": c.company,
        "position": c.position,
        "phone": c.phone,
        "email": c.email,
        "status": c.status,
        "industries": c.industries or [],
        "tags": c.tags or [],
        # 前端客户主页路由：模型回答中提及客户时应输出 Markdown 链接 [名称](url)
        "url": f"/customers/{c.id}",
    }


async def crm_search_customers_impl(
    db: AsyncSession, user: User, query: str, limit: int = 10
) -> list[dict]:
    """按名称/公司/职务/电话/邮箱模糊 + 行业包含检索客户（crm_search_customers 的实现）。"""
    query = (query or "").strip()
    if not query:
        return []
    limit = max(1, min(int(limit), 50))
    like = f"%{query}%"
    stmt = (
        select(Customer)
        .where(
            Customer.tenant_id == user.tenant_id,
            Customer.deleted_at.is_(None),
            or_(
                Customer.name.like(like),
                Customer.company.like(like),
                Customer.position.like(like),
                Customer.phone.like(like),
                Customer.email.like(like),
                # JSONB ? 操作符：industries 数组包含该行业名称即命中（同 /customers 列表）
                Customer.industries.has_key(query),  # noqa: W601
            ),
        )
        .order_by(Customer.created_at.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [_customer_summary(c) for c in rows]


async def crm_list_customers_impl(
    db: AsyncSession, user: User, limit: int = 20, offset: int = 0
) -> dict:
    """全量客户名单 + 总数（crm_list_customers 的实现）。

    无需关键词，回答「当前有多少客户 / 分别是谁」类问题；租户内全员可见、
    排除软删，口径与 crm_search_customers 一致。
    """
    limit = max(1, min(int(limit), 50))
    offset = max(0, int(offset))
    base_where = (
        Customer.tenant_id == user.tenant_id,
        Customer.deleted_at.is_(None),
    )
    total = await db.scalar(select(func.count(Customer.id)).where(*base_where))
    rows = (
        (
            await db.execute(
                select(Customer)
                .where(*base_where)
                .order_by(Customer.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return {
        "total": int(total or 0),
        "offset": offset,
        "items": [_customer_summary(c) for c in rows],
    }


async def crm_get_customer_impl(db: AsyncSession, user: User, customer_id: int) -> dict:
    """客户详情 + 最近 10 条跟进 + 进行中商机概要（crm_get_customer 的实现）。"""
    customer = await db.get(Customer, int(customer_id))
    if (
        customer is None
        or customer.tenant_id != user.tenant_id
        or customer.deleted_at is not None
    ):
        raise McpToolError("客户不存在")
    followups = (
        (
            await db.execute(
                select(FollowUpRecord)
                .where(FollowUpRecord.customer_id == customer.id)
                .order_by(FollowUpRecord.created_at.desc())
                .limit(10)
            )
        )
        .scalars()
        .all()
    )
    # 进行中 = 非赢单/输单（阶段枚举见前端 opportunityStageMap）
    opportunities = (
        (
            await db.execute(
                select(Opportunity)
                .where(
                    Opportunity.customer_id == customer.id,
                    Opportunity.stage.not_in(("closed_won", "closed_lost")),
                )
                .order_by(Opportunity.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    result = _customer_summary(customer)
    result.update(
        {
            "wechat": customer.wechat,
            "address": customer.address,
            "source": customer.source,
            "birthday": customer.birthday.isoformat() if customer.birthday else None,
            "profile": customer.profile,
            "recent_followups": [
                {
                    "id": f.id,
                    "type": f.type,
                    "content": f.content,
                    "ai_summary": f.ai_summary,
                    "created_at": f.created_at.isoformat() if f.created_at else None,
                }
                for f in followups
            ],
            "open_opportunities": [
                {
                    "id": o.id,
                    "name": o.name,
                    "amount": float(o.amount or 0),
                    "stage": o.stage,
                    "probability": o.probability,
                    "expected_close_date": (
                        o.expected_close_date.isoformat() if o.expected_close_date else None
                    ),
                }
                for o in opportunities
            ],
        }
    )
    return result


# ---------------------------------------------------------------------------
# 阶段 3：CRM 分析/清单读工具（直接读，无需审批）
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    """naive UTC（项目约定：连接时区已固定 UTC，见 database.py）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def crm_list_followups_impl(
    db: AsyncSession, user: User, customer_id: int | None = None,
    days: int | None = None, limit: int = 20,
) -> list[dict]:
    """跟进记录清单（crm_list_followups 的实现）。经 Customer join 做租户隔离。"""
    limit = max(1, min(int(limit), 100))
    stmt = (
        select(FollowUpRecord, Customer.name)
        .join(Customer, Customer.id == FollowUpRecord.customer_id)
        .where(Customer.tenant_id == user.tenant_id, Customer.deleted_at.is_(None))
        .order_by(FollowUpRecord.created_at.desc())
        .limit(limit)
    )
    if customer_id is not None:
        stmt = stmt.where(FollowUpRecord.customer_id == int(customer_id))
    if days:
        stmt = stmt.where(FollowUpRecord.created_at >= _utcnow() - timedelta(days=int(days)))
    rows = (await db.execute(stmt)).all()
    return [
        {
            "id": f.id,
            "customer_id": f.customer_id,
            "customer_name": cname,
            "type": f.type,
            "content": f.content,
            "next_step": f.next_step,
            "created_at": f.created_at.isoformat() if f.created_at else None,
        }
        for f, cname in rows
    ]


async def crm_list_opportunities_impl(
    db: AsyncSession, user: User, customer_id: int | None = None,
    stage: str | None = None, limit: int = 50,
) -> list[dict]:
    """商机清单（crm_list_opportunities 的实现）。经 Customer join 做租户隔离。"""
    limit = max(1, min(int(limit), 100))
    stmt = (
        select(Opportunity, Customer.name)
        .join(Customer, Customer.id == Opportunity.customer_id)
        .where(Customer.tenant_id == user.tenant_id, Customer.deleted_at.is_(None))
        .order_by(Opportunity.updated_at.desc())
        .limit(limit)
    )
    if customer_id is not None:
        stmt = stmt.where(Opportunity.customer_id == int(customer_id))
    if stage:
        stmt = stmt.where(Opportunity.stage == stage)
    rows = (await db.execute(stmt)).all()
    return [
        {
            "id": o.id,
            "customer_id": o.customer_id,
            "customer_name": cname,
            "name": o.name,
            "amount": float(o.amount or 0),
            "stage": o.stage,
            "probability": o.probability,
            "expected_close_date": (
                o.expected_close_date.isoformat() if o.expected_close_date else None
            ),
        }
        for o, cname in rows
    ]


async def crm_list_tasks_impl(
    db: AsyncSession, user: User, status: str | None = None,
    customer_id: int | None = None, limit: int = 50,
) -> list[dict]:
    """任务清单（crm_list_tasks 的实现）。Task 自带 tenant_id。"""
    limit = max(1, min(int(limit), 100))
    stmt = (
        select(Task, Customer.name)
        .outerjoin(Customer, Customer.id == Task.customer_id)
        .where(Task.tenant_id == user.tenant_id)
        .order_by(Task.created_at.desc())
        .limit(limit)
    )
    if status:
        stmt = stmt.where(Task.status == status)
    if customer_id is not None:
        stmt = stmt.where(Task.customer_id == int(customer_id))
    rows = (await db.execute(stmt)).all()
    return [
        {
            "id": t.id,
            "title": t.title,
            "status": t.status,
            "priority": t.priority,
            "due_date": t.due_date.isoformat() if t.due_date else None,
            "customer_id": t.customer_id,
            "customer_name": cname,
        }
        for t, cname in rows
    ]


async def crm_stats_impl(db: AsyncSession, user: User) -> dict:
    """CRM 经营概览（crm_stats 的实现）：客户阶段分布、商机漏斗、任务与跟进节奏。"""
    now = _utcnow()
    cust_rows = (
        await db.execute(
            select(Customer.status, func.count(Customer.id))
            .where(Customer.tenant_id == user.tenant_id, Customer.deleted_at.is_(None))
            .group_by(Customer.status)
        )
    ).all()
    opp_rows = (
        await db.execute(
            select(
                Opportunity.stage,
                func.count(Opportunity.id),
                func.coalesce(func.sum(Opportunity.amount), 0),
            )
            .join(Customer, Customer.id == Opportunity.customer_id)
            .where(Customer.tenant_id == user.tenant_id, Customer.deleted_at.is_(None))
            .group_by(Opportunity.stage)
        )
    ).all()
    open_tasks = await db.scalar(
        select(func.count(Task.id)).where(
            Task.tenant_id == user.tenant_id, Task.status.in_(("pending", "in_progress"))
        )
    )
    overdue_tasks = await db.scalar(
        select(func.count(Task.id)).where(
            Task.tenant_id == user.tenant_id,
            Task.status.in_(("pending", "in_progress")),
            Task.due_date.is_not(None),
            Task.due_date < now,
        )
    )
    followups_7d = await db.scalar(
        select(func.count(FollowUpRecord.id))
        .join(Customer, Customer.id == FollowUpRecord.customer_id)
        .where(
            Customer.tenant_id == user.tenant_id,
            Customer.deleted_at.is_(None),
            FollowUpRecord.created_at >= now - timedelta(days=7),
        )
    )
    followups_30d = await db.scalar(
        select(func.count(FollowUpRecord.id))
        .join(Customer, Customer.id == FollowUpRecord.customer_id)
        .where(
            Customer.tenant_id == user.tenant_id,
            Customer.deleted_at.is_(None),
            FollowUpRecord.created_at >= now - timedelta(days=30),
        )
    )
    return {
        "customers": {
            "total": sum(int(c) for _, c in cust_rows),
            "by_status": {s: int(c) for s, c in cust_rows},
        },
        "opportunities": {
            "by_stage": {
                s: {"count": int(c), "amount": float(a)} for s, c, a in opp_rows
            },
        },
        "tasks": {"open": int(open_tasks or 0), "overdue": int(overdue_tasks or 0)},
        "followups": {"last_7_days": int(followups_7d or 0), "last_30_days": int(followups_30d or 0)},
    }


async def crm_add_followup_impl(
    db: AsyncSession, user: User, customer_id: int, content: str, next_plan: str = ""
) -> dict:
    """申请新增跟进记录（crm_add_followup 的实现）。

    不直接写库：只创建审批单（summary 含客户名与内容摘要），admin 批准后由
    services/agent_approvals 的执行器写入 FollowUpRecord。
    """
    customer = await db.get(Customer, int(customer_id))
    if (
        customer is None
        or customer.tenant_id != user.tenant_id
        or customer.deleted_at is not None
    ):
        raise McpToolError("客户不存在")
    content = (content or "").strip()
    if not content:
        raise McpToolError("跟进内容为空")
    brief = content[:80] + ("…" if len(content) > 80 else "")
    summary = f"客户「{customer.name}」新增跟进：{brief}"
    if next_plan:
        summary += f"；下一步计划：{next_plan[:50]}"
    approval = await create_approval(
        db, user, "crm_add_followup",
        {"customer_id": customer.id, "content": content, "next_plan": next_plan or ""},
        summary,
    )
    # 工具使用独立 session（不经 get_db），必须自行提交，审批人才能立即看到
    await db.commit()
    return {
        "status": "pending_approval",
        "approval_id": approval.id,
        "message": "已提交审批，管理员批准后自动写入跟进记录",
    }


async def mail_draft_create_impl(
    db: AsyncSession, user: User, customer_id: int | None,
    to: str, subject: str, body: str,
) -> dict:
    """申请发送邮件（mail_draft_create 的实现）。

    不直接发送：只创建审批单（summary 含收件人与主题），admin 批准后由执行器
    经 app/services/email.py 的 SMTP 链路发出（SMTP 未配置时执行落 failed）。
    """
    to = (to or "").strip()
    subject = (subject or "").strip()
    if not to or not subject:
        raise McpToolError("收件人与主题不能为空")
    name: str | None = None
    if customer_id is not None:
        customer = await db.get(Customer, int(customer_id))
        if (
            customer is None
            or customer.tenant_id != user.tenant_id
            or customer.deleted_at is not None
        ):
            raise McpToolError("客户不存在")
        name = customer.name
    summary = f"发送邮件给 {to}"
    if name:
        summary += f"（客户「{name}」）"
    summary += f"，主题「{subject}」"
    approval = await create_approval(
        db, user, "mail_draft_create",
        {
            "customer_id": customer_id,
            "to": to,
            "subject": subject,
            "body": body or "",
        },
        summary,
    )
    await db.commit()
    return {
        "status": "pending_approval",
        "approval_id": approval.id,
        "message": "已提交审批，管理员批准后自动发送邮件",
    }


# ---------------------------------------------------------------------------
# 阶段 3：审批制 CRM 写工具（建/改/删客户、建商机、建任务）
# 统一约定：工具只创建审批单（写前做基础校验，保证摘要准确），
# admin 在 Agent 审批页批准后由 agent_approvals 的执行器落库。
# ---------------------------------------------------------------------------


async def _pending_approval_result(db: AsyncSession, user: User, tool: str, args: dict, summary: str) -> dict:
    """创建审批单 + commit + 统一返回（写工具共用收口）。"""
    approval = await create_approval(db, user, tool, args, summary)
    await db.commit()
    return {
        "status": "pending_approval",
        "approval_id": approval.id,
        "message": "已提交审批，管理员批准后自动执行；请明确告知用户「需管理员审批后生效」",
    }


async def crm_create_customer_impl(db: AsyncSession, user: User, args: dict) -> dict:
    """申请新建客户（crm_create_customer 的实现）。"""
    name = (args.get("name") or "").strip()
    if not name:
        raise McpToolError("客户名称不能为空")
    parts = [f"新建客户「{name}」"]
    if args.get("company"):
        parts.append(f"单位「{args['company']}」")
    if args.get("phone"):
        parts.append(f"电话 {args['phone']}")
    return await _pending_approval_result(db, user, "crm_create_customer", args, "，".join(parts))


async def crm_update_customer_impl(db: AsyncSession, user: User, customer_id: int, fields: dict) -> dict:
    """申请更新客户字段（crm_update_customer 的实现）。fields 只含要改的字段。"""
    customer = await db.get(Customer, int(customer_id))
    if (
        customer is None
        or customer.tenant_id != user.tenant_id
        or customer.deleted_at is not None
    ):
        raise McpToolError("客户不存在")
    fields = {k: v for k, v in (fields or {}).items() if v is not None}
    if not fields:
        raise McpToolError("没有要更新的字段")
    summary = f"更新客户「{customer.name}」字段：{', '.join(fields.keys())}"
    return await _pending_approval_result(
        db, user, "crm_update_customer", {"customer_id": customer.id, "fields": fields}, summary
    )


async def crm_delete_customer_impl(db: AsyncSession, user: User, customer_id: int) -> dict:
    """申请删除客户（crm_delete_customer 的实现）。软删，回收站可恢复。"""
    customer = await db.get(Customer, int(customer_id))
    if (
        customer is None
        or customer.tenant_id != user.tenant_id
        or customer.deleted_at is not None
    ):
        raise McpToolError("客户不存在")
    return await _pending_approval_result(
        db, user, "crm_delete_customer", {"customer_id": customer.id},
        f"删除客户「{customer.name}」（软删，可在回收站恢复）",
    )


async def crm_create_opportunity_impl(db: AsyncSession, user: User, args: dict) -> dict:
    """申请新增商机（crm_create_opportunity 的实现）。"""
    customer = await db.get(Customer, int(args.get("customer_id") or 0))
    if (
        customer is None
        or customer.tenant_id != user.tenant_id
        or customer.deleted_at is not None
    ):
        raise McpToolError("客户不存在")
    name = (args.get("name") or "").strip()
    if not name:
        raise McpToolError("商机名称不能为空")
    summary = f"客户「{customer.name}」新增商机「{name}」"
    if args.get("amount"):
        summary += f"，金额 ¥{args['amount']}"
    return await _pending_approval_result(db, user, "crm_create_opportunity", args, summary)


async def crm_create_task_impl(db: AsyncSession, user: User, args: dict) -> dict:
    """申请新增任务（crm_create_task 的实现）。"""
    title = (args.get("title") or "").strip()
    if not title:
        raise McpToolError("任务标题不能为空")
    summary = f"新增任务「{title}」"
    if args.get("customer_id") is not None:
        customer = await db.get(Customer, int(args["customer_id"]))
        if (
            customer is None
            or customer.tenant_id != user.tenant_id
            or customer.deleted_at is not None
        ):
            raise McpToolError("客户不存在")
        summary += f"（关联客户「{customer.name}」）"
    if args.get("due_date"):
        summary += f"，截止 {args['due_date']}"
    return await _pending_approval_result(db, user, "crm_create_task", args, summary)


# ---------------------------------------------------------------------------
# 阶段 4：skill 发现/调用 + AI 起草 API 工具（审批制）
# ---------------------------------------------------------------------------


async def skill_list_impl(db: AsyncSession, user: User) -> list[dict]:
    """已启用自定义工具清单（skill_list 的实现）。

    只返回概要（名称/描述/参数 schema），不返回 config——headers 里可能含密钥，
    不能经 MCP 暴露给 LLM。
    """
    skills = await get_enabled_skills(db, user.tenant_id)
    return [
        {
            "name": s.name,
            "description": s.description,
            "parameters": getattr(s, "parameters", None) or {"type": "object", "properties": {}},
        }
        for s in skills
    ]


async def skill_call_impl(db: AsyncSession, user: User, skill_name: str, arguments: dict) -> str:
    """调用已启用自定义工具（skill_call 的实现）。复用超时/截断/调用埋点。"""
    skills = await get_enabled_skills(db, user.tenant_id)
    skill = next((s for s in skills if s.name == (skill_name or "").strip()), None)
    if skill is None:
        raise McpToolError(f"工具 {skill_name} 未启用或不存在（先用 skill_list 查看可用工具）")
    try:
        return await execute_skill(
            skill, arguments or {}, {"tenant_id": user.tenant_id, "user_id": user.id, "caller": "mcp"}
        )
    except McpToolError:
        raise
    except Exception as exc:
        raise McpToolError(f"{skill_name} 调用失败: {exc}") from exc


_SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SKILL_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH"}


async def skill_create_api_impl(db: AsyncSession, user: User, args: dict) -> dict:
    """申请新建 API 工具（skill_create_api 的实现）。

    写前做基础校验（name/method/url），保证审批摘要准确；完整的重名/格式校验
    在执行器里还会再做一次（批准时状态可能已变化）。
    """
    name = (args.get("name") or "").strip()
    if not name or len(name) > 50 or not _SKILL_NAME_RE.match(name):
        raise McpToolError(f"工具名非法（小写字母开头的 snake_case，≤50 字符）：{name}")
    method = (args.get("method") or "GET").strip().upper()
    if method not in _SKILL_METHODS:
        raise McpToolError(f"请求方法非法：{method}")
    url = (args.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise McpToolError("url 需以 http:// 或 https:// 开头")
    label = args.get("display_name") or name
    summary = f"新建 API 工具「{label}」（{method} {url}）"
    return await _pending_approval_result(db, user, "skill_create_api", args, summary)


@kb_mcp.tool(
    name="kb_list",
    description="列出当前用户可读的知识库（kb_id、名称、类型、文档数），用于发现可检索范围。",
)
async def _kb_list_tool(ctx: Context = None) -> list[dict]:
    async with AsyncSessionLocal() as db:
        user = await resolve_mcp_user(db, _payload_from_ctx(ctx))
        return await kb_list_impl(db, user)


@kb_mcp.tool(
    name="crm_list_customers",
    description=(
        "列出全部客户名单与总数（无需关键词，租户内全员可见）。"
        "用户问「有多少客户 / 客户名单 / 分别是谁」时用本工具；"
        "返回 total（客户总数）与 items（概要列表），超过 limit 时用 offset 翻页。"
        "按关键词查找特定客户请改用 crm_search_customers。"
        "回答中提及客户时，必须用 Markdown 链接指向客户主页：[客户名称](该客户的 url 字段)。"
    ),
)
async def _crm_list_customers_tool(
    limit: int = 20, offset: int = 0, ctx: Context = None
) -> dict:
    async with AsyncSessionLocal() as db:
        user = await resolve_mcp_user(db, _payload_from_ctx(ctx))
        return await crm_list_customers_impl(db, user, limit, offset)


@kb_mcp.tool(
    name="crm_search_customers",
    description=(
        "按名称/公司/职务/电话/邮箱/行业模糊检索客户，返回客户概要列表（租户内全员可见）。"
        "需要全部客户名单或客户总数时不要猜关键词，改用 crm_list_customers。"
        "回答中提及客户时，必须用 Markdown 链接指向客户主页：[客户名称](该客户的 url 字段)。"
    ),
)
async def _crm_search_customers_tool(
    query: str, limit: int = 10, ctx: Context = None
) -> list[dict]:
    async with AsyncSessionLocal() as db:
        user = await resolve_mcp_user(db, _payload_from_ctx(ctx))
        return await crm_search_customers_impl(db, user, query, limit)


@kb_mcp.tool(
    name="crm_get_customer",
    description=(
        "按 customer_id 获取客户详情，含最近 10 条跟进记录与进行中的商机概要。"
        "回答中提及客户时，必须用 Markdown 链接指向客户主页：[客户名称](该客户的 url 字段)。"
    ),
)
async def _crm_get_customer_tool(customer_id: int, ctx: Context = None) -> dict:
    async with AsyncSessionLocal() as db:
        user = await resolve_mcp_user(db, _payload_from_ctx(ctx))
        return await crm_get_customer_impl(db, user, customer_id)


@kb_mcp.tool(
    name="crm_add_followup",
    description=(
        "为客户申请新增跟进记录（写操作）。本工具不直接写库，只创建审批单；"
        "管理员批准后系统自动写入。返回 approval_id 供跟踪审批结果。"
    ),
)
async def _crm_add_followup_tool(
    customer_id: int, content: str, next_plan: str = "", ctx: Context = None
) -> dict:
    async with AsyncSessionLocal() as db:
        user = await resolve_mcp_user(db, _payload_from_ctx(ctx))
        return await crm_add_followup_impl(db, user, customer_id, content, next_plan)


# ---------------------------------------------------------------------------
# 阶段 3 工具注册：CRM 清单/分析读工具 + 审批制写工具
# ---------------------------------------------------------------------------


@kb_mcp.tool(
    name="crm_list_followups",
    description=(
        "列出跟进记录（可按客户 customer_id 或最近 days 天过滤），返回客户名、类型、内容、"
        "下一步与时间。回答「最近跟进了哪些客户 / 某客户最近沟通情况」时使用。"
    ),
)
async def _crm_list_followups_tool(
    customer_id: int | None = None, days: int | None = None, limit: int = 20, ctx: Context = None
) -> list[dict]:
    async with AsyncSessionLocal() as db:
        user = await resolve_mcp_user(db, _payload_from_ctx(ctx))
        return await crm_list_followups_impl(db, user, customer_id, days, limit)


@kb_mcp.tool(
    name="crm_list_opportunities",
    description=(
        "列出商机（可按客户 customer_id 或阶段 stage 过滤），返回客户名、金额、阶段、"
        "赢单概率与预计成交日。回答「在手商机 / 商机漏斗 / 预计成交」时使用。"
    ),
)
async def _crm_list_opportunities_tool(
    customer_id: int | None = None, stage: str | None = None, limit: int = 50, ctx: Context = None
) -> list[dict]:
    async with AsyncSessionLocal() as db:
        user = await resolve_mcp_user(db, _payload_from_ctx(ctx))
        return await crm_list_opportunities_impl(db, user, customer_id, stage, limit)


@kb_mcp.tool(
    name="crm_list_tasks",
    description=(
        "列出任务/待办（可按状态 status: pending/in_progress/completed/cancelled 或客户过滤），"
        "返回标题、状态、优先级、截止日与关联客户。回答「待办有哪些 / 逾期任务」时使用。"
    ),
)
async def _crm_list_tasks_tool(
    status: str | None = None, customer_id: int | None = None, limit: int = 50, ctx: Context = None
) -> list[dict]:
    async with AsyncSessionLocal() as db:
        user = await resolve_mcp_user(db, _payload_from_ctx(ctx))
        return await crm_list_tasks_impl(db, user, status, customer_id, limit)


@kb_mcp.tool(
    name="crm_stats",
    description=(
        "CRM 经营概览统计：客户总数与阶段分布、商机分阶段数量与金额、进行中/逾期任务数、"
        "近 7/30 天跟进次数。做客户经营分析、月度复盘类问题时先用本工具取数。"
    ),
)
async def _crm_stats_tool(ctx: Context = None) -> dict:
    async with AsyncSessionLocal() as db:
        user = await resolve_mcp_user(db, _payload_from_ctx(ctx))
        return await crm_stats_impl(db, user)


@kb_mcp.tool(
    name="crm_create_customer",
    description=(
        "申请新建客户（写操作，需管理员审批后生效）。从会议纪要/文档中提取到联系人时，"
        "把整理好的资料写进 profile（Markdown，会作为客户画像展示）。"
        "status 取值：potential/intention/negotiating/closed/lost。"
        "成功后请在回答中说明「已提交审批，管理员批准后生效」。"
    ),
)
async def _crm_create_customer_tool(
    name: str,
    company: str = "",
    position: str = "",
    phone: str = "",
    email: str = "",
    wechat: str = "",
    address: str = "",
    source: str = "",
    status: str = "potential",
    industries: list[str] | None = None,
    tags: list[str] | None = None,
    birthday: str = "",
    profile: str = "",
    ctx: Context = None,
) -> dict:
    async with AsyncSessionLocal() as db:
        user = await resolve_mcp_user(db, _payload_from_ctx(ctx))
        args = {
            "name": name, "company": company, "position": position, "phone": phone,
            "email": email, "wechat": wechat, "address": address, "source": source,
            "status": status, "industries": industries or [], "tags": tags or [],
            "birthday": birthday, "profile": profile,
        }
        return await crm_create_customer_impl(db, user, args)


@kb_mcp.tool(
    name="crm_update_customer",
    description=(
        "申请更新客户资料（写操作，需管理员审批后生效）。fields 只放要改的字段，"
        "可改：name/company/position/wechat/phone/email/address/source/status/"
        "industries/tags/birthday(YYYY-MM-DD)/ddq_status(none/pending/completed)/profile。"
    ),
)
async def _crm_update_customer_tool(
    customer_id: int, fields: dict, ctx: Context = None
) -> dict:
    async with AsyncSessionLocal() as db:
        user = await resolve_mcp_user(db, _payload_from_ctx(ctx))
        return await crm_update_customer_impl(db, user, customer_id, fields)


@kb_mcp.tool(
    name="crm_delete_customer",
    description=(
        "申请删除客户（写操作，需管理员审批后生效；软删，回收站可恢复）。"
        "仅在用户明确要求删除时使用，调用前先用 crm_get_customer 确认对象。"
    ),
)
async def _crm_delete_customer_tool(customer_id: int, ctx: Context = None) -> dict:
    async with AsyncSessionLocal() as db:
        user = await resolve_mcp_user(db, _payload_from_ctx(ctx))
        return await crm_delete_customer_impl(db, user, customer_id)


@kb_mcp.tool(
    name="crm_create_opportunity",
    description=(
        "申请为客户新增商机（写操作，需管理员审批后生效）。"
        "stage 取值：prospecting/qualification/proposal/negotiation/closed_won/closed_lost；"
        "expected_close_date 格式 YYYY-MM-DD；probability 0-100。"
    ),
)
async def _crm_create_opportunity_tool(
    customer_id: int,
    name: str,
    amount: float = 0,
    stage: str = "prospecting",
    probability: int = 0,
    expected_close_date: str = "",
    ctx: Context = None,
) -> dict:
    async with AsyncSessionLocal() as db:
        user = await resolve_mcp_user(db, _payload_from_ctx(ctx))
        args = {
            "customer_id": customer_id, "name": name, "amount": amount,
            "stage": stage, "probability": probability,
            "expected_close_date": expected_close_date,
        }
        return await crm_create_opportunity_impl(db, user, args)


@kb_mcp.tool(
    name="crm_create_task",
    description=(
        "申请新增任务/待办（写操作，需管理员审批后生效），可关联客户。"
        "priority 取值：high/medium/low；due_date 为 ISO 时间（如 2026-09-30T18:00:00）。"
        "从跟进记录/会议纪要提取后续行动时使用。"
    ),
)
async def _crm_create_task_tool(
    title: str,
    customer_id: int | None = None,
    due_date: str = "",
    priority: str = "medium",
    description: str = "",
    ctx: Context = None,
) -> dict:
    async with AsyncSessionLocal() as db:
        user = await resolve_mcp_user(db, _payload_from_ctx(ctx))
        args = {
            "title": title, "customer_id": customer_id, "due_date": due_date,
            "priority": priority, "description": description,
        }
        return await crm_create_task_impl(db, user, args)


@kb_mcp.tool(
    name="mail_draft_create",
    description=(
        "申请向客户发送邮件（写操作）。本工具不直接发送，只创建审批单；"
        "管理员批准后系统经 SMTP 自动发出。customer_id 可空（仅用于审批摘要关联客户）。"
    ),
)
async def _mail_draft_create_tool(
    customer_id: int | None, to: str, subject: str, body: str, ctx: Context = None
) -> dict:
    async with AsyncSessionLocal() as db:
        user = await resolve_mcp_user(db, _payload_from_ctx(ctx))
        return await mail_draft_create_impl(db, user, customer_id, to, subject, body)


# ---------------------------------------------------------------------------
# 联网工具：复用管理端「Skill 管理」配置的 builtin web_search / web_fetch
# （provider/key 在 DB 里维护，agent 与后台对话共用同一配置与调用埋点）。
# dsh 内置的 web_search（deepseek 原生搜索）要求官方平台 key，与聊天的
# llm-pi-ai 网关 key 不通用，已在 acp-model.yml 进程 patch 中禁用，统一走这里。
# ---------------------------------------------------------------------------


async def _run_tenant_skill(db: AsyncSession, user: User, name: str, args: dict) -> str:
    """按名取租户已启用的 skill 并执行（带超时/截断/调用埋点）。"""
    return await skill_call_impl(db, user, name, args)


@kb_mcp.tool(
    name="web_search",
    description=(
        "联网搜索最新信息，返回 Top 结果（标题+摘要+链接）。需要实时资讯/新闻/知识库以外的"
        "公开信息时使用；结果不理想可换关键词重试，并用 web_fetch 打开链接阅读全文。"
        "引用搜索结果时，在相关语句末尾用 [序号](URL) 标注来源，前端会渲染为可点击上标。"
    ),
)
async def _web_search_tool(query: str, ctx: Context = None) -> str:
    async with AsyncSessionLocal() as db:
        user = await resolve_mcp_user(db, _payload_from_ctx(ctx))
        return await _run_tenant_skill(db, user, "web_search", {"query": query})


@kb_mcp.tool(
    name="web_fetch",
    description="抓取指定 URL 的网页正文（转纯文本，截断返回）。需要阅读某个网页全文时使用。",
)
async def _web_fetch_tool(url: str, ctx: Context = None) -> str:
    async with AsyncSessionLocal() as db:
        user = await resolve_mcp_user(db, _payload_from_ctx(ctx))
        return await _run_tenant_skill(db, user, "web_fetch", {"url": url})


# ---------------------------------------------------------------------------
# 阶段 4 工具注册：自定义工具发现/调用 + AI 起草 API 工具（审批制）
# ---------------------------------------------------------------------------


@kb_mcp.tool(
    name="skill_list",
    description=(
        "列出当前租户已启用的自定义工具（名称、描述、参数 schema），"
        "含管理端配置的 API 工具与联网工具。现有工具不够用、或调用 skill_call 前先查可用工具时用。"
    ),
)
async def _skill_list_tool(ctx: Context = None) -> list[dict]:
    async with AsyncSessionLocal() as db:
        user = await resolve_mcp_user(db, _payload_from_ctx(ctx))
        return await skill_list_impl(db, user)


@kb_mcp.tool(
    name="skill_call",
    description=(
        "调用 skill_list 列出的自定义工具，skill_name 为工具名，arguments 按其参数 schema 填写。"
        "调用失败时检查参数是否符合 schema；工具不存在时先 skill_list 确认。"
    ),
)
async def _skill_call_tool(skill_name: str, arguments: dict | None = None, ctx: Context = None) -> str:
    async with AsyncSessionLocal() as db:
        user = await resolve_mcp_user(db, _payload_from_ctx(ctx))
        return await skill_call_impl(db, user, skill_name, arguments)


@kb_mcp.tool(
    name="skill_create_api",
    description=(
        "申请新建一个 API 工具（写操作，需管理员审批后生效并自动启用）。"
        "当现有工具无法满足需求、且用户提供了第三方 HTTP 接口文档时使用："
        "先用 web_fetch 阅读接口文档，再起草配置——name 为小写 snake_case；"
        "url/body 中用 {{参数名}} 作占位符；parameters 为 JSON Schema（描述每个参数）；"
        "headers 放鉴权头（如 Authorization）。批准后工具立即可用 skill_call 调用。"
        "成功后请在回答中说明「已提交审批，管理员批准后生效」。"
    ),
)
async def _skill_create_api_tool(
    name: str,
    url: str,
    method: str = "GET",
    display_name: str = "",
    description: str = "",
    headers: dict | None = None,
    body: str = "",
    parameters: dict | None = None,
    timeout: int = 60,
    ctx: Context = None,
) -> dict:
    async with AsyncSessionLocal() as db:
        user = await resolve_mcp_user(db, _payload_from_ctx(ctx))
        args = {
            "name": name, "url": url, "method": method,
            "display_name": display_name, "description": description,
            "headers": headers, "body": body,
            "parameters": parameters, "timeout": timeout,
        }
        return await skill_create_api_impl(db, user, args)


# ---------------------------------------------------------------------------
# 个人记忆工具（跨工作区长期记忆；严格按 user_id 隔离，无需审批——用户本人数据）
# ---------------------------------------------------------------------------


@kb_mcp.tool(
    name="memory_save",
    description=(
        "保存一条当前用户的长期个人记忆（跨会话/跨工作区有效）。当用户透露长期偏好、身份背景、"
        "常用约定（如「我是做 xx 行业的」「报告默认用中文」「以后都给我表格形式」）时主动调用；"
        "一次性上下文（当前任务细节）不要存。内容限 500 字，重复内容自动去重。"
    ),
)
async def _memory_save_tool(content: str, ctx: Context = None) -> dict:
    async with AsyncSessionLocal() as db:
        user = await resolve_mcp_user(db, _payload_from_ctx(ctx))
        try:
            mem, created = await add_memory(db, user.id, content, source="agent")
        except ValueError as exc:
            raise McpToolError(str(exc)) from None
        await db.commit()
        return {
            "ok": True,
            "created": created,
            "memory_id": mem.id,
            "message": "已保存到用户长期记忆" if created else "相同内容已存在，未重复保存",
        }


@kb_mcp.tool(
    name="memory_list",
    description="列出当前用户的全部长期个人记忆（id + 内容 + 时间），最近更新的在前。",
)
async def _memory_list_tool(ctx: Context = None) -> list[dict]:
    async with AsyncSessionLocal() as db:
        user = await resolve_mcp_user(db, _payload_from_ctx(ctx))
        memories = await list_memories(db, user.id)
        return [
            {"id": m.id, "content": m.content, "updated_at": m.updated_at.isoformat() if m.updated_at else None}
            for m in memories
        ]


@kb_mcp.tool(
    name="memory_search",
    description="在当前用户的长期个人记忆中按关键词检索（记忆较多时用，避免全量列出）。",
)
async def _memory_search_tool(query: str, ctx: Context = None) -> list[dict]:
    async with AsyncSessionLocal() as db:
        user = await resolve_mcp_user(db, _payload_from_ctx(ctx))
        memories = await search_memories(db, user.id, query)
        return [
            {"id": m.id, "content": m.content, "updated_at": m.updated_at.isoformat() if m.updated_at else None}
            for m in memories
        ]


@kb_mcp.tool(
    name="memory_delete",
    description=(
        "删除当前用户的一条长期个人记忆（按 memory_list/memory_search 返回的 id）。"
        "记忆过期或被用户要求纠正时使用；删错可用 memory_save 重新保存正确内容。"
    ),
)
async def _memory_delete_tool(memory_id: int, ctx: Context = None) -> dict:
    async with AsyncSessionLocal() as db:
        user = await resolve_mcp_user(db, _payload_from_ctx(ctx))
        removed = await delete_memory(db, user.id, memory_id)
        await db.commit()
        if not removed:
            raise McpToolError(f"记忆 {memory_id} 不存在")
        return {"ok": True, "message": "已删除"}


def build_mcp_asgi_app():
    """构造挂载到 FastAPI /api/mcp 的 ASGI 应用（FastMCP streamable-http + 令牌中间件）。"""
    return BearerMcpAuthMiddleware(kb_mcp.streamable_http_app())


@asynccontextmanager
async def session_manager_lifespan():
    """StreamableHTTP session manager 的 task group 需要在 FastAPI lifespan 内运行
    （mcp>=1.8 要求，否则处理请求时抛 RuntimeError）。须先于本函数调用
    build_mcp_asgi_app()（session manager 在那里懒创建）。"""
    async with kb_mcp.session_manager.run():
        yield
