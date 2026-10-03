"""Agent 审批链服务（dsh 基座阶段 2）。

信任边界：dsh 进程与 LLM 均不可信——MCP 写工具不直接落库，只创建审批单；
admin 在 /agent-approvals/{id}/decide 批准后由本模块同步执行对应动作
（执行器按 tool_name 分发）。审批与执行全程记审计日志、发站内通知。

并发与事务约定：
- 状态翻转走条件 UPDATE（WHERE status='pending'）抢占，并发审批只有一个
  请求能翻走状态，其余报 409，杜绝重复执行副作用；
- approve 的执行包在 SAVEPOINT（begin_nested）里，中途失败回滚半截副作用，
  保证审批单状态（failed）与业务数据一致；
- 外部副作用（发邮件、跟进 AI 钩子）不入库执行，收集到 post_commit 列表，
  由调用方在 commit 成功后触发（钩子另开会话读库，必须在提交后运行）。
"""
import logging
import re
from datetime import date, datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_approval import AgentApproval
from app.models.customer import Customer
from app.models.follow_up import FollowUpRecord
from app.models.notification import Notification
from app.models.opportunity import Opportunity
from app.models.skill import Skill as SkillRow
from app.models.task import Task
from app.models.user import User
from app.services.audit import record_audit
from app.services.email import send_email
from app.services.followup_hooks import run_after_followup_created
from app.services.skills.builtin import BUILTIN_SKILLS
from app.schemas.task import _to_naive_utc

logger = logging.getLogger(__name__)

# 支持审批流的写工具白名单（MCP 写工具 ↔ 执行器的一一映射）
APPROVAL_TOOL_NAMES = {
    "crm_add_followup",
    "mail_draft_create",
    "crm_create_customer",
    "crm_update_customer",
    "crm_delete_customer",
    "crm_create_opportunity",
    "crm_create_task",
    "skill_create_api",
}

# 客户阶段/任务优先级/商机阶段合法值（与前端 format.js 枚举一致）
_CUSTOMER_STATUSES = {"potential", "intention", "negotiating", "closed", "lost"}
_TASK_PRIORITIES = {"high", "medium", "low"}
_OPP_STAGES = {"prospecting", "qualification", "proposal", "negotiation", "closed_won", "closed_lost"}


class ApprovalError(Exception):
    """审批链业务错误（未知工具、状态非法、执行参数无效等）。"""


def _utcnow() -> datetime:
    """naive UTC（项目约定：连接时区已固定 UTC，见 database.py）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _notify(
    db: AsyncSession, tenant_id: int, user_id: int | None,
    title: str, content: str, approval_id: int,
) -> None:
    """发站内通知（type=agent_approval，resource 指回审批单便于前端跳转）。"""
    if user_id is None:
        return
    db.add(
        Notification(
            tenant_id=tenant_id,
            user_id=user_id,
            task_id=None,
            title=title,
            content=content,
            type="agent_approval",
            is_read=False,
            resource_type="agent_approval",
            resource_id=approval_id,
        )
    )


async def create_approval(
    db: AsyncSession,
    user: User,
    tool_name: str,
    arguments: dict,
    summary: str,
    chat_session_id: int | None = None,
) -> AgentApproval:
    """创建 pending 审批单并通知租户内全部启用中的 admin（调用方负责 commit）。"""
    if tool_name not in APPROVAL_TOOL_NAMES:
        raise ApprovalError(f"工具 {tool_name} 不支持审批流")
    approval = AgentApproval(
        tenant_id=user.tenant_id,
        requester_user_id=user.id,
        chat_session_id=chat_session_id,
        tool_name=tool_name,
        arguments=arguments or {},
        summary=summary,
        status="pending",
    )
    db.add(approval)
    await db.flush()  # 先取 approval.id，通知里要关联
    record_audit(
        db, user, "create", "agent_approval", approval.id,
        {"tool_name": tool_name, "summary": summary[:200]},
    )
    admins = (
        (
            await db.execute(
                select(User).where(
                    User.tenant_id == user.tenant_id,
                    User.role == "admin",
                    User.status == 1,
                )
            )
        )
        .scalars()
        .all()
    )
    for a in admins:
        await _notify(
            db, user.tenant_id, a.id,
            "Agent 操作待审批",
            f"{user.name or user.username} 的 Agent 申请执行 {tool_name}：{summary}",
            approval.id,
        )
    return approval


# ---------------------------------------------------------------------------
# 执行器：按 tool_name 分发，返回人话执行结果（写 approval.result）
# post_commit：外部副作用回调列表，由调用方在事务 commit 成功后逐个触发
# ---------------------------------------------------------------------------


async def _exec_crm_add_followup(
    db: AsyncSession, approval: AgentApproval, args: dict, post_commit: list
) -> str:
    customer_id = int(args.get("customer_id") or 0)
    customer = await db.get(Customer, customer_id)
    if (
        customer is None
        or customer.tenant_id != approval.tenant_id
        or customer.deleted_at is not None
    ):
        raise ApprovalError("客户不存在或已删除")
    content = (args.get("content") or "").strip()
    if not content:
        raise ApprovalError("跟进内容为空")
    next_plan = (args.get("next_plan") or "").strip()
    if next_plan:
        content = f"{content}\n\n下一步计划：{next_plan}"
    # FollowUpRecord.type 的常规取值是 call/meeting/email/visit（沟通渠道）；
    # Agent 代写的记录不属于任何渠道，用 other 标记以便与人工记录区分
    record = FollowUpRecord(
        customer_id=customer.id,
        user_id=approval.requester_user_id,
        type="other",
        content=content,
    )
    db.add(record)
    await db.flush()  # 取 record.id 写进执行结果
    # 与手动创建跟进一致的后置钩子（AI 摘要/任务抽取/客户简报刷新）；
    # 钩子另开会话读库，必须在 commit 后触发，故挂到 post_commit
    record_id, cid = record.id, customer.id
    post_commit.append(lambda: run_after_followup_created(record_id, cid))
    return f"跟进记录已写入（id={record.id}，客户「{customer.name}」）"


async def _exec_mail_draft_create(
    db: AsyncSession, approval: AgentApproval, args: dict, post_commit: list
) -> str:
    to = (args.get("to") or "").strip()
    subject = (args.get("subject") or "").strip()
    body = args.get("body") or ""
    if not to or not subject:
        raise ApprovalError("收件人或主题为空")
    customer_id = args.get("customer_id")
    if customer_id is not None:
        customer = await db.get(Customer, int(customer_id))
        if customer is None or customer.tenant_id != approval.tenant_id:
            raise ApprovalError("客户不存在")
    # 发邮件是外部副作用，移到 commit 成功后执行（SMTP 故障不再翻转审批单
    # 状态，由路由层记日志）；这里只做静态校验并登记回调
    post_commit.append(lambda: send_email(to, subject, body))
    return f"邮件已批准，提交后发送至 {to}（主题「{subject}」）"


# ---------------------------------------------------------------------------
# CRM 写工具执行器（dsh 基座阶段 3：agent 自然语言驱动业务操作）
# ---------------------------------------------------------------------------

# crm_update_customer 允许更新的字段白名单（其余字段一律忽略，防越权改 tenant/owner）
_CUSTOMER_UPDATABLE = {
    "name", "company", "position", "wechat", "phone", "email", "address",
    "source", "status", "birthday", "industries", "tags", "ddq_status", "profile",
}


def _parse_date(value: str | None, field: str) -> date | None:
    """ISO 日期字符串 → date；空返回 None，非法抛 ApprovalError。"""
    value = (value or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        raise ApprovalError(f"{field} 日期格式非法（应为 YYYY-MM-DD）：{value}") from None


async def _get_tenant_customer(db: AsyncSession, tenant_id: int, customer_id) -> Customer:
    """取客户并强制租户归属与软删校验（所有客户相关执行器共用）。"""
    customer = await db.get(Customer, int(customer_id or 0))
    if (
        customer is None
        or customer.tenant_id != tenant_id
        or customer.deleted_at is not None
    ):
        raise ApprovalError("客户不存在或已删除")
    return customer


async def _exec_crm_create_customer(db: AsyncSession, approval: AgentApproval, args: dict) -> str:
    name = (args.get("name") or "").strip()
    if not name:
        raise ApprovalError("客户名称为空")
    status = (args.get("status") or "potential").strip()
    if status not in _CUSTOMER_STATUSES:
        raise ApprovalError(f"客户状态非法：{status}")
    customer = Customer(
        tenant_id=approval.tenant_id,
        name=name,
        company=(args.get("company") or "").strip() or None,
        position=(args.get("position") or "").strip() or None,
        wechat=(args.get("wechat") or "").strip() or None,
        phone=(args.get("phone") or "").strip() or None,
        email=(args.get("email") or "").strip() or None,
        address=(args.get("address") or "").strip() or None,
        source=(args.get("source") or "").strip() or None,
        status=status,
        industries=[str(x) for x in (args.get("industries") or []) if str(x).strip()],
        tags=[str(x) for x in (args.get("tags") or []) if str(x).strip()],
        birthday=_parse_date(args.get("birthday"), "birthday"),
        owner_id=approval.requester_user_id,
    )
    # 提取资料写入客户画像（agent 从会议纪要等文档整理出的 Markdown）
    profile = (args.get("profile") or "").strip()
    if profile:
        customer.profile = profile
        customer.profile_status = "ready"
        customer.profile_updated_at = _utcnow()
    db.add(customer)
    await db.flush()
    return f"客户已创建（id={customer.id}，主页 /customers/{customer.id}）"


async def _exec_crm_update_customer(db: AsyncSession, approval: AgentApproval, args: dict) -> str:
    customer = await _get_tenant_customer(db, approval.tenant_id, args.get("customer_id"))
    fields = args.get("fields") or {}
    changed: list[str] = []
    for key in _CUSTOMER_UPDATABLE:
        if key not in fields:
            continue
        value = fields[key]
        if key == "status" and value not in _CUSTOMER_STATUSES:
            raise ApprovalError(f"客户状态非法：{value}")
        if key == "ddq_status" and value not in {"none", "pending", "completed"}:
            raise ApprovalError(f"DDQ 状态非法：{value}")
        if key in {"industries", "tags"}:
            value = [str(x) for x in (value or []) if str(x).strip()]
        if key == "birthday":
            value = _parse_date(value, "birthday")
        if key == "name" and not (value or "").strip():
            raise ApprovalError("客户名称不能为空")
        setattr(customer, key, value)
        changed.append(key)
    if not changed:
        raise ApprovalError("没有可更新的字段")
    if "profile" in changed:
        customer.profile_status = "ready"
        customer.profile_updated_at = _utcnow()
    await db.flush()
    return f"客户「{customer.name}」已更新（字段：{', '.join(changed)}）"


async def _exec_crm_delete_customer(db: AsyncSession, approval: AgentApproval, args: dict) -> str:
    customer = await _get_tenant_customer(db, approval.tenant_id, args.get("customer_id"))
    customer.deleted_at = _utcnow()  # 软删，回收站可恢复
    await db.flush()
    return f"客户「{customer.name}」已删除（软删，可在回收站恢复）"


async def _exec_crm_create_opportunity(db: AsyncSession, approval: AgentApproval, args: dict) -> str:
    customer = await _get_tenant_customer(db, approval.tenant_id, args.get("customer_id"))
    name = (args.get("name") or "").strip()
    if not name:
        raise ApprovalError("商机名称为空")
    stage = (args.get("stage") or "prospecting").strip()
    if stage not in _OPP_STAGES:
        raise ApprovalError(f"商机阶段非法：{stage}")
    try:
        amount = float(args.get("amount") or 0)
    except (TypeError, ValueError):
        raise ApprovalError("商机金额非法") from None
    probability = max(0, min(int(args.get("probability") or 0), 100))
    opp = Opportunity(
        customer_id=customer.id,
        name=name,
        amount=amount,
        stage=stage,
        probability=probability,
        expected_close_date=_parse_date(args.get("expected_close_date"), "expected_close_date"),
        owner_id=approval.requester_user_id,
    )
    db.add(opp)
    await db.flush()
    return f"商机已创建（id={opp.id}，客户「{customer.name}」，金额 ¥{amount:,.2f}）"


async def _exec_crm_create_task(db: AsyncSession, approval: AgentApproval, args: dict) -> str:
    title = (args.get("title") or "").strip()
    if not title:
        raise ApprovalError("任务标题为空")
    customer_id = args.get("customer_id")
    customer: Customer | None = None
    if customer_id is not None:
        customer = await _get_tenant_customer(db, approval.tenant_id, customer_id)
    priority = (args.get("priority") or "medium").strip()
    if priority not in _TASK_PRIORITIES:
        raise ApprovalError(f"任务优先级非法：{priority}")
    due_raw = (args.get("due_date") or "").strip()
    due: datetime | None = None
    if due_raw:
        try:
            # aware ISO 统一转 naive UTC（与 schemas/task.py 的 TaskCreate 校验同款口径），
            # 避免带偏移的时间被直接丢弃时区导致偏差
            due = _to_naive_utc(datetime.fromisoformat(due_raw.replace("Z", "+00:00")))
        except ValueError:
            raise ApprovalError(f"截止日期格式非法：{due_raw}") from None
    task = Task(
        tenant_id=approval.tenant_id,
        customer_id=customer.id if customer else None,
        user_id=approval.requester_user_id,
        title=title,
        description=(args.get("description") or "").strip() or None,
        type="follow_up",
        priority=priority,
        due_date=due,
        ai_generated=True,
        source="ai_analysis",
    )
    db.add(task)
    await db.flush()
    suffix = f"，关联客户「{customer.name}」" if customer else ""
    return f"任务已创建（id={task.id}{suffix}）"


# ---------------------------------------------------------------------------
# Skill 生成执行器（AI 起草 API 工具配置，admin 批准后建 skills 行并启用）
# ---------------------------------------------------------------------------

_SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SKILL_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH"}


async def _exec_skill_create_api(db: AsyncSession, approval: AgentApproval, args: dict) -> str:
    """批准 AI 起草的 API skill 配置：建 skills 行（type=api，enabled=True）。

    安全边界：生成的只是声明式 HTTP 配置，运行时由 ApiSkill 执行——
    URL 每次调用前过 check_url_safe（SSRF 防护），这里只做静态校验；
    密钥类 header 在管理端列表接口由 _mask_config 脱敏。
    """
    name = (args.get("name") or "").strip()
    if not name or len(name) > 50 or not _SKILL_NAME_RE.match(name):
        raise ApprovalError(f"工具名非法（小写字母开头的 snake_case，≤50 字符）：{name}")
    if name in BUILTIN_SKILLS:
        raise ApprovalError(f"与内置工具重名：{name}")
    exists = await db.scalar(
        select(SkillRow.id).where(
            SkillRow.tenant_id == approval.tenant_id, SkillRow.name == name
        )
    )
    if exists:
        raise ApprovalError(f"同名工具已存在：{name}")
    method = (args.get("method") or "GET").strip().upper()
    if method not in _SKILL_METHODS:
        raise ApprovalError(f"请求方法非法：{method}")
    url = (args.get("url") or "").strip()
    # url 可含 {{arg}} 占位符（路径/查询部分），scheme 必须是 http/https；
    # 渲染后的完整 URL 在每次调用时还会过 check_url_safe（SSRF 防护）
    if not url.startswith(("http://", "https://")):
        raise ApprovalError(f"URL 非法（需 http/https 开头）：{url}")
    parameters = args.get("parameters")
    if parameters is not None and not isinstance(parameters, dict):
        raise ApprovalError("parameters 必须是 JSON Schema 对象")
    headers = args.get("headers")
    if headers is not None and not isinstance(headers, dict):
        raise ApprovalError("headers 必须是对象")
    try:
        timeout = max(10, min(int(args.get("timeout") or 60), 300))
    except (TypeError, ValueError):
        raise ApprovalError("timeout 非法") from None
    skill = SkillRow(
        tenant_id=approval.tenant_id,
        name=name,
        display_name=(args.get("display_name") or "").strip() or None,
        description=(args.get("description") or "").strip() or None,
        type="api",
        enabled=True,
        config={
            "method": method,
            "url": url,
            "headers": headers or {},
            "body": args.get("body") or "",
            "parameters": parameters or {"type": "object", "properties": {}},
            "timeout": timeout,
        },
    )
    db.add(skill)
    await db.flush()
    return f"API 工具已创建并启用（name={name}，{method} {url}；agent 可用 skill_call 调用）"


async def execute_approval(db: AsyncSession, approval: AgentApproval, post_commit: list) -> str:
    """按 tool_name 分发执行已批准的动作，返回执行结果文本。

    post_commit：执行器登记的外部副作用回调（发邮件、跟进 AI 钩子等），
    由调用方在 commit 成功后触发。
    """
    args = approval.arguments or {}
    if approval.tool_name == "crm_add_followup":
        return await _exec_crm_add_followup(db, approval, args, post_commit)
    if approval.tool_name == "mail_draft_create":
        return await _exec_mail_draft_create(db, approval, args, post_commit)
    if approval.tool_name == "crm_create_customer":
        return await _exec_crm_create_customer(db, approval, args)
    if approval.tool_name == "crm_update_customer":
        return await _exec_crm_update_customer(db, approval, args)
    if approval.tool_name == "crm_delete_customer":
        return await _exec_crm_delete_customer(db, approval, args)
    if approval.tool_name == "crm_create_opportunity":
        return await _exec_crm_create_opportunity(db, approval, args)
    if approval.tool_name == "crm_create_task":
        return await _exec_crm_create_task(db, approval, args)
    if approval.tool_name == "skill_create_api":
        return await _exec_skill_create_api(db, approval, args)
    raise ApprovalError(f"未知工具: {approval.tool_name}")


async def decide_approval(
    db: AsyncSession,
    approval: AgentApproval,
    decider: User,
    decision: str,
    reason: str | None = None,
    post_commit: list | None = None,
) -> AgentApproval:
    """审批状态流转（调用方负责 commit，并在 commit 后触发 post_commit 回调）。

    reject → rejected；approve → 同步执行，成功 executed、失败 failed
    （失败原因写 result，不向审批人抛错）。结果通知发起人。

    并发安全：状态翻转用条件 UPDATE（WHERE status='pending'）抢占——两个
    admin 同时审批时只有一个请求影响行数为 1，另一个报 409，不会重复执行
    副作用。执行包在 SAVEPOINT 里：中途失败回滚半截落库，审批单落 failed，
    业务数据与状态保持一致。
    """
    if approval.status != "pending":
        raise ApprovalError("审批单已处理，不能重复审批")
    # 条件 UPDATE 抢占 pending → 目标状态；影响行数为 0 说明已被并发请求处理
    now = _utcnow()
    claimed = "rejected" if decision == "reject" else "approved"
    result = await db.execute(
        update(AgentApproval)
        .where(AgentApproval.id == approval.id, AgentApproval.status == "pending")
        .values(
            status=claimed,
            decided_by=decider.id,
            decided_at=now,
            decision_reason=reason,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise ApprovalError("审批单已处理，不能重复审批")
    # synchronize_session=False 不回写已加载的 ORM 实例，手动同步内存状态
    approval.status = claimed
    approval.decided_by = decider.id
    approval.decided_at = now
    approval.decision_reason = reason

    if decision == "reject":
        record_audit(
            db, decider, "reject", "agent_approval", approval.id,
            {"tool_name": approval.tool_name, "reason": reason},
        )
        await _notify(
            db, approval.tenant_id, approval.requester_user_id,
            "Agent 审批已拒绝",
            f"{approval.summary}\n拒绝原因：{reason or '未填写'}",
            approval.id,
        )
        return approval

    record_audit(
        db, decider, "approve", "agent_approval", approval.id,
        {"tool_name": approval.tool_name},
    )
    hooks = post_commit if post_commit is not None else []
    try:
        # SAVEPOINT：执行中途失败回滚半截副作用（如已 flush 的半截记录），
        # 外层的审批单状态翻转与审计不受影响，随外层事务正常提交
        async with db.begin_nested():
            approval.result = await execute_approval(db, approval, hooks)
        approval.status = "executed"
    except Exception as exc:
        approval.status = "failed"
        approval.result = f"{type(exc).__name__}: {exc}"
        hooks.clear()  # 执行失败：已登记的 post_commit 副作用一并作废
        logger.warning("审批单 %s 执行失败: %s", approval.id, exc)
    record_audit(
        db, decider, "execute", "agent_approval", approval.id,
        {"status": approval.status, "result": approval.result},
    )
    title = "Agent 审批已通过并执行" if approval.status == "executed" else "Agent 审批通过但执行失败"
    await _notify(
        db, approval.tenant_id, approval.requester_user_id,
        title,
        f"{approval.summary}\n结果：{approval.result}",
        approval.id,
    )
    return approval


async def retry_approval(
    db: AsyncSession,
    approval: AgentApproval,
    actor: User,
    post_commit: list | None = None,
) -> AgentApproval:
    """重试执行失败的审批单（仅 failed 态；调用方负责 commit 并触发 post_commit）。

    与 decide_approval 的 approve 分支同模式：条件 UPDATE（WHERE status='failed'）
    抢占防并发重试重复执行；执行包在 SAVEPOINT 里，失败仍落 failed 可再次重试。
    """
    if approval.status != "failed":
        raise ApprovalError("仅执行失败的审批单可重试")
    now = _utcnow()
    result = await db.execute(
        update(AgentApproval)
        .where(AgentApproval.id == approval.id, AgentApproval.status == "failed")
        .values(status="approved", decided_by=actor.id, decided_at=now)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise ApprovalError("审批单已被并发重试处理")
    # synchronize_session=False 不回写已加载的 ORM 实例，手动同步内存状态
    approval.status = "approved"
    approval.decided_by = actor.id
    approval.decided_at = now

    record_audit(
        db, actor, "retry", "agent_approval", approval.id,
        {"tool_name": approval.tool_name},
    )
    hooks = post_commit if post_commit is not None else []
    try:
        # SAVEPOINT：执行中途失败回滚半截副作用，审批单落 failed 可再次重试
        async with db.begin_nested():
            approval.result = await execute_approval(db, approval, hooks)
        approval.status = "executed"
    except Exception as exc:
        approval.status = "failed"
        approval.result = f"{type(exc).__name__}: {exc}"
        hooks.clear()  # 执行失败：已登记的 post_commit 副作用一并作废
        logger.warning("审批单 %s 重试执行失败: %s", approval.id, exc)
    record_audit(
        db, actor, "execute", "agent_approval", approval.id,
        {"status": approval.status, "result": approval.result},
    )
    title = "Agent 审批重试执行成功" if approval.status == "executed" else "Agent 审批重试仍失败"
    await _notify(
        db, approval.tenant_id, approval.requester_user_id,
        title,
        f"{approval.summary}\n结果：{approval.result}",
        approval.id,
    )
    return approval
