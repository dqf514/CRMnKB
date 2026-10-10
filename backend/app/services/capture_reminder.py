"""随手记提醒意图识别：从捕获文本中抽取"某日提醒我干嘛"，自动创建带到期时间的任务。

流程：正则快速门控（意向词+时间词，双命中才打扰 LLM，省 token 零延迟）
→ chat LLM 抽取 JSON（remind_at 本地时间 + title）
→ 建任务（type=todo / source=capture，due_date 转 naive UTC 落库）
→ 任务进日历（/calendar 按 due_date 聚合），临期通知靠 task_due_soon 规则：
  首个随手记提醒创建时为租户幂等补一条默认启用的 task_due_soon 规则（24h 阈值），
  用户手动关掉过则不重建（尊重选择）。

任何异常只记日志返回 None，绝不阻塞随手记主流程。
"""
import json
import logging
import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.reminder_rule import ReminderRule
from app.models.task import Task
from app.models.user import User
from app.services.llm import resolve_chat_llm

logger = logging.getLogger(__name__)

# 意向词 + 时间词双命中才进 LLM 抽取（单时间词如"3月14日的会议纪要"不构成提醒）
_INTENT_RE = re.compile(r"提醒我|提醒一下|记得|别忘|勿忘|到时候(告诉|提醒|喊)|帮我记")
_TIME_RE = re.compile(
    r"今天|今晚|今早|明早|明天|后天|大后天|下周|下月|月底|月初|周末"
    r"|周[一二三四五六日天]|星期[一二三四五六日天]"
    r"|\d{1,2}\s*月\s*\d{1,2}\s*[日号]"
    r"|\d{4}\s*[-/年]\s*\d{1,2}\s*[-/月]\s*\d{1,2}\s*[日号]?"
    r"|\d{1,2}\s*[点时]\s*(半|\d{1,2}\s*分?)?"
    r"|上午|中午|下午|晚上|早上|凌晨"
)

_EXTRACT_SYSTEM = (
    "你是提醒意图抽取器。从用户的随手记文本中判断是否需要创建定时提醒。"
    "只输出 JSON（不要任何其他文字）："
    '{"has_reminder": true/false, "remind_at": "YYYY-MM-DD HH:MM", "title": "提醒事项（≤30字）"}。\n'
    "规则：remind_at 为服务器本地时间，无具体时间默认当天 09:00；"
    "没有提醒意图、或无法确定未来时间时 has_reminder=false。"
)

# 默认临期提醒规则（首个随手记提醒创建时幂等补种）
_DEFAULT_RULE_NAME = "任务临期提醒"
_DEFAULT_RULE_THRESHOLD_HOURS = 24


def has_reminder_intent(text: str) -> bool:
    """正则门控：意向词与时间词双命中。纯函数。"""
    return bool(_INTENT_RE.search(text) and _TIME_RE.search(text))


def parse_extract_response(raw: str, now_local: datetime) -> dict | None:
    """解析 LLM 抽取结果；无效/过去时间返回 None。纯函数。

    返回 {"remind_at": aware 本地 datetime, "title": str}。
    """
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not data.get("has_reminder"):
        return None
    title = str(data.get("title") or "").strip()[:50]
    remind_raw = str(data.get("remind_at") or "").strip()
    if not title or not remind_raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(remind_raw, fmt)
            break
        except ValueError:
            continue
    else:
        return None
    remind_at = dt.replace(tzinfo=now_local.tzinfo)
    # 过去时间不建（LLM 解析错日期时的护栏）
    if remind_at <= now_local:
        return None
    return {"remind_at": remind_at, "title": title}


async def _ensure_due_soon_rule(db: AsyncSession, tenant_id: int) -> None:
    """租户没有任何 task_due_soon 规则时补一条默认启用的（24h 阈值）。

    用户曾建过（哪怕已禁用）则不重建——尊重用户关掉的选择。
    """
    exists = await db.scalar(
        select(ReminderRule.id).where(
            ReminderRule.tenant_id == tenant_id,
            ReminderRule.trigger_type == "task_due_soon",
        ).limit(1)
    )
    if exists:
        return
    db.add(
        ReminderRule(
            tenant_id=tenant_id,
            name=_DEFAULT_RULE_NAME,
            trigger_type="task_due_soon",
            trigger_config={"threshold_hours": _DEFAULT_RULE_THRESHOLD_HOURS},
            action_config={"template": "提醒：{{title}}"},
            enabled=True,
        )
    )


async def maybe_create_capture_reminder(
    db: AsyncSession, user: User, text: str, customer_id: int | None = None
) -> dict | None:
    """识别随手记中的提醒意图并建任务；无意图/失败返回 None（不抛异常）。

    返回 {"task_id", "title", "due_at_local": "MM-DD HH:mm"}（前端提示用，本地时间）。
    """
    if not has_reminder_intent(text):
        return None
    try:
        now_local = datetime.now(timezone.utc).astimezone()
        llm = await resolve_chat_llm(
            caller="capture_reminder", tenant_id=user.tenant_id, user_id=user.id
        )
        weekday_cn = "一二三四五六日"[now_local.weekday()]
        raw = await llm.chat([
            {"role": "system", "content": _EXTRACT_SYSTEM},
            {"role": "user", "content": (
                f"当前本地时间：{now_local:%Y-%m-%d %H:%M}（周{weekday_cn}）\n"
                f"随手记文本：{text[:2000]}"
            )},
        ])
        parsed = parse_extract_response(raw, now_local)
        if parsed is None:
            return None
        # 本地 aware → naive UTC（项目约定：TIMESTAMP 列存 naive UTC）
        due_utc = parsed["remind_at"].astimezone(timezone.utc).replace(tzinfo=None)
        task = Task(
            tenant_id=user.tenant_id,
            customer_id=customer_id,
            user_id=user.id,
            title=parsed["title"],
            description=f"来自随手记：{text[:200]}",
            type="todo",
            priority="medium",
            status="pending",
            due_date=due_utc,
            ai_generated=False,
            source="capture",
        )
        db.add(task)
        await _ensure_due_soon_rule(db, user.tenant_id)
        await db.flush()
        return {
            "task_id": task.id,
            "title": task.title,
            "due_at_local": f"{parsed['remind_at']:%m-%d %H:%M}",
        }
    except Exception:
        logger.warning("随手记提醒识别失败（忽略，笔记已正常保存）", exc_info=True)
        return None
