"""客户 Pipeline AI 阶段简报：聚合客户资料/跟进/商机/任务 → chat LLM 生成简短 Markdown 简报。

- build_brief_prompt：纯函数（便于测试），固定输出三个小节。
- generate_brief：后台任务，异常只记日志不抛出（仿 services/profile.py 风格）。
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.customer import Customer
from app.models.follow_up import FollowUpRecord
from app.models.opportunity import Opportunity
from app.models.task import Task
from app.services.llm import resolve_chat_llm

logger = logging.getLogger(__name__)

BRIEF_SYSTEM = (
    "你是 CRM 销售助手。基于给定的客户资料、跟进记录、商机与任务，输出简短的中文 Markdown 阶段简报，"
    "必须且只包含以下三个小节：## 当前阶段判断、## 近期动态、## 建议下一步。"
    "每节 1-3 条要点，只基于给定资料，不要编造没有的事实；资料不足的小节注明「资料不足」。"
)


def _fmt_dt(value) -> str:
    """datetime 格式化为 YYYY-MM-DD，无值返回 '-'。"""
    return f"{value:%Y-%m-%d}" if value else "-"


def build_brief_prompt(customer, followups, opportunities, tasks) -> list[dict]:
    """构建阶段简报 Prompt。纯函数（参数为 ORM 对象或同名属性的鸭子类型）。

    - customer：客户资料（name/company/status/ddq_status/industries 等属性）
    - followups：最近 10 条跟进（取 ai_summary，无则 content 前 100 字）
    - opportunities：商机列表（名称/金额/阶段/赢单率）
    - tasks：未完成任务列表（标题/优先级/到期日）
    """
    lines = [f"客户：{getattr(customer, 'name', '-')}"]
    industries = getattr(customer, "industries", None) or []
    lines.append(f"- 公司: {getattr(customer, 'company', None) or '-'}")
    lines.append(f"- 行业: {'、'.join(industries) if industries else '-'}")
    lines.append(f"- 客户状态: {getattr(customer, 'status', None) or '-'}")
    lines.append(f"- DDQ 状态: {getattr(customer, 'ddq_status', None) or 'none'}")
    lines.append("")
    lines.append("## 最近跟进")
    for f in followups:
        summary = getattr(f, "ai_summary", None) or (getattr(f, "content", "") or "")[:100]
        lines.append(f"- [{_fmt_dt(getattr(f, 'created_at', None))}] [{getattr(f, 'type', '-')}] {summary}")
        next_step = getattr(f, "next_step", None)
        if next_step:
            lines.append(f"  下一步：{next_step}")
    if not followups:
        lines.append("（无）")
    lines.append("")
    lines.append("## 商机")
    for o in opportunities:
        lines.append(
            f"- {getattr(o, 'name', '-')}（金额 {getattr(o, 'amount', 0)}，"
            f"阶段 {getattr(o, 'stage', '-')}，赢单率 {getattr(o, 'probability', 0)}%）"
        )
    if not opportunities:
        lines.append("（无）")
    lines.append("")
    lines.append("## 未完成任务")
    for t in tasks:
        lines.append(
            f"- {getattr(t, 'title', '-')}（优先级 {getattr(t, 'priority', '-')}，"
            f"到期 {_fmt_dt(getattr(t, 'due_date', None))}）"
        )
    if not tasks:
        lines.append("（无）")
    return [
        {"role": "system", "content": BRIEF_SYSTEM},
        {"role": "user", "content": "\n".join(lines)},
    ]


async def generate_brief(customer_id: int) -> None:
    """后台任务：聚合客户数据 → 调 chat LLM 生成阶段简报并写回 customer.ai_brief。

    任何异常只记日志，绝不抛出（不阻塞跟进创建等主流程）。"""
    async with AsyncSessionLocal() as session:
        customer = await session.get(Customer, customer_id)
        if customer is None or customer.deleted_at is not None:
            return
        try:
            followups = (
                (
                    await session.execute(
                        select(FollowUpRecord)
                        .where(FollowUpRecord.customer_id == customer_id)
                        .order_by(FollowUpRecord.created_at.desc())
                        .limit(10)
                    )
                )
                .scalars()
                .all()
            )
            opportunities = (
                (
                    await session.execute(
                        select(Opportunity)
                        .where(Opportunity.customer_id == customer_id)
                        .order_by(Opportunity.created_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            tasks = (
                (
                    await session.execute(
                        select(Task)
                        .where(
                            Task.customer_id == customer_id,
                            Task.status.in_(["pending", "in_progress"]),
                        )
                        .order_by(Task.created_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            prompt = build_brief_prompt(customer, followups, opportunities, tasks)
            chat_llm = await resolve_chat_llm(caller="pipeline_brief", tenant_id=customer.tenant_id)
            brief = await chat_llm.chat(prompt)
            customer.ai_brief = brief.strip()
            # ai_brief_at 为 TIMESTAMP（不带时区），写 naive UTC
            customer.ai_brief_at = datetime.now(timezone.utc).replace(tzinfo=None)
            await session.commit()
        except Exception as exc:
            logger.warning("客户 %s 阶段简报生成失败（忽略）: %s", customer_id, exc)
            from app.services.error_log import log_error

            await log_error(
                "error", "pipeline_brief", f"客户 {customer_id} 阶段简报生成失败",
                str(exc), tenant_id=customer.tenant_id,
            )
