"""跟进记录创建后的后置钩子（手动创建与 Agent 审批创建共用）。

三个钩子各自开新会话读库、失败只记日志不抛出：
- generate_ai_summary：一句话 AI 摘要（写回 follow_up_records.ai_summary）
- extract_and_create_tasks：AI 待办抽取（写 tasks 表）
- generate_brief：客户 AI 阶段简报刷新（写 customers.ai_brief）

必须在所属事务 commit 成功后调用——钩子另开会话，未提交的数据读不到。
（generate_ai_summary 原在 api/followups.py，为审批路径复用移至此处。）
"""
import logging

from app.database import AsyncSessionLocal
from app.models.customer import Customer
from app.models.follow_up import FollowUpRecord
from app.services.ai_tasks import extract_and_create_tasks
from app.services.llm import resolve_chat_llm
from app.services.pipeline_brief import generate_brief

logger = logging.getLogger(__name__)


async def generate_ai_summary(record_id: int) -> None:
    """后台任务：调用 chat LLM 生成一句话总结，失败则留空不报错。"""
    async with AsyncSessionLocal() as session:
        record = await session.get(FollowUpRecord, record_id)
        if record is None:
            return
        try:
            customer = await session.get(Customer, record.customer_id)
            tenant_id = customer.tenant_id if customer else None
            chat_llm = await resolve_chat_llm(caller="summary", tenant_id=tenant_id)
            summary = await chat_llm.chat(
                [
                    {
                        "role": "user",
                        "content": f"请用一句话总结以下客户跟进记录（不超过50字）：\n{record.content}",
                    }
                ]
            )
            record.ai_summary = summary.strip()
            await session.commit()
        except Exception as exc:
            logger.warning("跟进记录 %s AI 总结生成失败（忽略）: %s", record_id, exc)


async def run_after_followup_created(record_id: int, customer_id: int) -> None:
    """跟进创建后的统一后置动作（与手动创建路径行为一致）。

    逐个执行、单钩子失败不影响其余（各钩子内部已兜底，这里再保险一层）。
    """
    for name, coro in (
        ("ai_summary", generate_ai_summary(record_id)),
        ("extract_tasks", extract_and_create_tasks(record_id)),
        ("pipeline_brief", generate_brief(customer_id)),
    ):
        try:
            await coro
        except Exception as exc:
            logger.warning("跟进 %s 后置钩子 %s 失败（忽略）: %s", record_id, name, exc)
