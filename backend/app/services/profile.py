import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.customer import Customer
from app.models.document import KnowledgeDocument
from app.models.follow_up import FollowUpRecord
from app.models.knowledge_base import KnowledgeBase
from app.models.opportunity import Opportunity
from app.services.llm import resolve_chat_llm

logger = logging.getLogger(__name__)

PROFILE_SYSTEM = (
    "你是 CRM 客户分析专家。基于给定的客户资料生成结构化客户画像（Markdown 格式），"
    "必须包含以下章节：## 基本情况、## 需求与痛点、## 决策链与关键人、## 合作进展、"
    "## 风险与跟进建议。只基于给定资料，不要编造没有的事实；资料不足的章节注明"
    "「资料不足」。"
)


def build_profile_prompt(
    customer: dict,
    followups: list[str],
    opportunities: list[str],
    doc_texts: list[str],
) -> list[dict]:
    """构建客户画像 Prompt。纯函数。"""
    lines = [f"客户：{customer.get('name', '-')}"]
    for key in ("industry", "company", "phone", "email", "status", "source"):
        lines.append(f"- {key}: {customer.get(key) or '-'}")
    lines.append("")
    lines.append("## 跟进记录")
    lines.extend(f"- {f}" for f in followups)
    if not followups:
        lines.append("（无）")
    lines.append("")
    lines.append("## 商机")
    lines.extend(f"- {o}" for o in opportunities)
    if not opportunities:
        lines.append("（无）")
    lines.append("")
    lines.append("## 客户文档摘要")
    lines.extend(doc_texts)
    if not doc_texts:
        lines.append("（无）")
    return [
        {"role": "system", "content": PROFILE_SYSTEM},
        {"role": "user", "content": "\n".join(lines)},
    ]


async def generate_profile(customer_id: int) -> None:
    """后台任务：聚合客户资料 → 调 chat LLM 生成 Markdown 画像。
    失败只标记 profile_status=failed，绝不抛出。"""
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
                        .limit(20)
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
            docs = (
                (
                    await session.execute(
                        select(KnowledgeDocument)
                        .join(KnowledgeBase, KnowledgeBase.id == KnowledgeDocument.kb_id)
                        .where(
                            KnowledgeBase.customer_id == customer_id,
                            KnowledgeDocument.status == "ready",
                        )
                    )
                )
                .scalars()
                .all()
            )

            # 聚合内容按 PROFILE_MAX_CHARS 截断
            budget = settings.PROFILE_MAX_CHARS
            doc_texts: list[str] = []
            for doc in docs:
                if budget <= 0:
                    break
                snippet = (doc.content or "")[:budget]
                if snippet:
                    doc_texts.append(f"### {doc.title}\n{snippet}")
                    budget -= len(snippet)

            prompt = build_profile_prompt(
                {
                    "name": customer.name,
                    # 七期起行业为多选数组，兼容旧单列
                    "industry": "、".join(customer.industries or []) or customer.industry,
                    "company": customer.company,
                    "phone": customer.phone,
                    "email": customer.email,
                    "status": customer.status,
                    "source": customer.source,
                },
                [f"[{f.created_at:%Y-%m-%d}] {f.ai_summary or f.content[:100]}" for f in followups],
                [
                    f"{o.name}（金额 {o.amount}，阶段 {o.stage}，赢单率 {o.probability}%）"
                    for o in opportunities
                ],
                doc_texts,
            )
            chat_llm = await resolve_chat_llm(caller="profile", tenant_id=customer.tenant_id)
            profile = await chat_llm.chat(prompt)
            customer.profile = profile
            customer.profile_status = "ready"
        except Exception as exc:
            logger.warning("客户 %s 画像生成失败: %s", customer_id, exc)
            customer.profile_status = "failed"
            from app.services.error_log import log_error

            await log_error("error", "profile", f"客户 {customer_id} 画像生成失败", str(exc), tenant_id=customer.tenant_id)
        # profile_updated_at 为 TIMESTAMP（不带时区），写 naive UTC
        customer.profile_updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await session.commit()
