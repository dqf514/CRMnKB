import json
import logging
import re
from datetime import datetime, timezone

from app.database import AsyncSessionLocal
from app.models.customer import Customer
from app.models.follow_up import FollowUpRecord
from app.models.task import Task
from app.services.llm import resolve_chat_llm

logger = logging.getLogger(__name__)

TODO_EXTRACT_PROMPT = (
    "请从以下客户跟进记录中提取需要后续执行的待办事项。"
    '只输出 JSON 数组，格式为 [{"title": "待办标题", "due_date": "YYYY-MM-DD", "priority": "high|medium|low"}]，'
    "due_date 和 priority 可省略；如果没有待办事项，输出 []。不要输出任何其他文字。\n\n"
    "跟进记录：\n{content}"
)

VALID_PRIORITIES = {"high", "medium", "low"}


def parse_todos(text: str | None) -> list[dict]:
    r"""从 LLM 输出中容错解析待办 JSON 数组，解析失败返回 []。纯函数。

    用 raw_decode 从每个可能的 '[' 位置尝试解析，避免贪婪正则 `\[.*\]`
    在嵌套/多数组时过捕获导致解析失败。"""
    if not text:
        return []
    decoder = json.JSONDecoder()
    idx = text.find("[")
    data = None
    while idx != -1:
        try:
            candidate, _ = decoder.raw_decode(text[idx:])
            if isinstance(candidate, list):
                data = candidate
                break
        except (json.JSONDecodeError, ValueError):
            pass
        idx = text.find("[", idx + 1)
    if data is None:
        return []
    todos = []
    for item in data:
        if not isinstance(item, dict) or not item.get("title"):
            continue
        todo = {"title": str(item["title"])[:200]}
        if item.get("priority") in VALID_PRIORITIES:
            todo["priority"] = item["priority"]
        due = item.get("due_date")
        if isinstance(due, str):
            try:
                dt = datetime.fromisoformat(due)
                # 统一转 naive UTC 再存（Task.due_date 为不带时区的 TIMESTAMP 列）
                if dt.tzinfo is not None:
                    dt = dt.astimezone(timezone.utc)
                todo["due_date"] = dt.replace(tzinfo=None)
            except ValueError:
                pass
        todos.append(todo)
    return todos


async def extract_and_create_tasks(record_id: int) -> None:
    """后台任务：从跟进记录提取待办并创建 AI 任务。LLM 不可用或解析失败静默跳过。"""
    async with AsyncSessionLocal() as session:
        record = await session.get(FollowUpRecord, record_id)
        if record is None:
            return
        customer = await session.get(Customer, record.customer_id)
        if customer is None:
            return
        try:
            chat_llm = await resolve_chat_llm(caller="ai_tasks", tenant_id=customer.tenant_id)
            raw = await chat_llm.chat(
                [{"role": "user", "content": TODO_EXTRACT_PROMPT.format(content=record.content)}]
            )
        except Exception as exc:
            logger.warning("跟进记录 %s 待办提取失败（忽略）: %s", record_id, exc)
            return
        todos = parse_todos(raw)
        if not todos:
            return
        for todo in todos:
            session.add(
                Task(
                    tenant_id=customer.tenant_id,
                    customer_id=record.customer_id,
                    user_id=record.user_id,
                    title=todo["title"],
                    type="follow_up",
                    priority=todo.get("priority", "medium"),
                    due_date=todo.get("due_date"),
                    status="pending",
                    ai_generated=True,
                    source="ai_analysis",
                )
            )
        await session.commit()
        logger.info("跟进记录 %s 提取到 %s 条待办任务", record_id, len(todos))
