"""AI 邮件草稿：基于客户资料 + 阶段简报 + 最近跟进 + Email Guide 生成邮件草稿。

- build_email_draft_prompt：纯函数（便于测试）。
- parse_email_draft：容错解析 LLM 输出的严格 JSON（去代码围栏 + raw_decode 逐位尝试，
  仿 services/ai_tasks.parse_todos 思路）。
"""
import json
import logging

from app.services.report import _lang_instruction

logger = logging.getLogger(__name__)

EMAIL_DRAFT_SYSTEM = (
    "你是商务邮件撰写助手。基于给定的客户资料、阶段简报与最近跟进记录，按用户意图撰写一封商务邮件。"
    "语气专业、简洁、有针对性，只基于给定资料，不要编造事实或承诺。"
)


def build_email_draft_prompt(
    customer,
    recent_followups,
    brief: str | None,
    guide: str | None,
    intent: str,
    language: str = "zh",
) -> list[dict]:
    """构建邮件草稿 Prompt。纯函数（customer/followups 为 ORM 对象或同名属性的鸭子类型）。

    - guide：管理员配置的 Email Guide（写作规范），为空则忽略该段
    - language：zh / en / zh_en（口径与报告一致，复用 _lang_instruction）
    - 输出约定：严格 JSON {"subject": "...", "body": "..."}，body 为纯文本邮件正文
    """
    system_parts = [EMAIL_DRAFT_SYSTEM]
    if guide and guide.strip():
        system_parts.append(f"写作规范（Email Guide，必须遵守）：\n{guide.strip()}")
    system_parts.append(_lang_instruction(language))
    system_parts.append(
        '输出格式：严格输出一个 JSON 对象，不要输出任何其他内容：'
        '{"subject": "邮件主题", "body": "邮件正文（纯文本，段落间用空行分隔）"}'
    )

    lines = [f"客户：{getattr(customer, 'name', '-')}"]
    lines.append(f"- 公司: {getattr(customer, 'company', None) or '-'}")
    lines.append(f"- 联系人职位: {getattr(customer, 'position', None) or '-'}")
    industries = getattr(customer, "industries", None) or []
    lines.append(f"- 行业: {'、'.join(industries) if industries else '-'}")
    lines.append(f"- 客户状态: {getattr(customer, 'status', None) or '-'}")
    lines.append("")
    lines.append("## 阶段简报")
    lines.append(brief.strip() if brief and brief.strip() else "（无）")
    lines.append("")
    lines.append("## 最近跟进")
    for f in recent_followups:
        summary = getattr(f, "ai_summary", None) or (getattr(f, "content", "") or "")[:100]
        created = getattr(f, "created_at", None)
        lines.append(f"- [{f'{created:%Y-%m-%d}' if created else '-'}] {summary}")
    if not recent_followups:
        lines.append("（无）")
    lines.append("")
    lines.append("## 用户意图")
    lines.append(intent)
    return [
        {"role": "system", "content": "\n\n".join(system_parts)},
        {"role": "user", "content": "\n".join(lines)},
    ]


def parse_email_draft(text: str | None) -> dict:
    r"""从 LLM 输出中容错解析邮件草稿 JSON，返回 {"subject": ..., "body": ...}；失败返回 {}。纯函数。

    先去 Markdown 代码围栏，再用 raw_decode 从每个可能的 '{' 位置尝试解析，
    避免贪婪正则 `\{.*\}` 在嵌套时过捕获。"""
    if not text:
        return {}
    cleaned = text.strip()
    # 去代码围栏（```json ... ``` / ``` ... ```）
    if cleaned.startswith("```"):
        first_nl = cleaned.find("\n")
        if first_nl != -1:
            cleaned = cleaned[first_nl + 1:]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
        cleaned = cleaned.strip()
    decoder = json.JSONDecoder()
    idx = cleaned.find("{")
    while idx != -1:
        try:
            candidate, _ = decoder.raw_decode(cleaned[idx:])
            if isinstance(candidate, dict) and (candidate.get("subject") or candidate.get("body")):
                return {
                    "subject": str(candidate.get("subject") or ""),
                    "body": str(candidate.get("body") or ""),
                }
        except (json.JSONDecodeError, ValueError):
            pass
        idx = cleaned.find("{", idx + 1)
    return {}
