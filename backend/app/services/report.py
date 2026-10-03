import logging
import re
import time as _time
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.customer import Customer
from app.models.follow_up import FollowUpRecord
from app.models.knowledge_base import KnowledgeBase
from app.models.notification import Notification
from app.models.opportunity import Opportunity
from app.models.report import Report
from app.models.user import User
from app.services.acp_bridge import bridge
from app.services.llm import resolve_chat_llm, resolve_embed_llm
from app.services.permissions import accessible_ids, filter_accessible_ids
from app.services.rag import (
    EmbeddingUnavailable,
    _find_block,
    attach_file_info,
    expand_contexts,
    reciprocal_rank_fusion,
    rerank_chunks,
    search_chunks_keyword,
    search_chunks_vector,
    trim_blocks,
)

logger = logging.getLogger(__name__)

REPORT_TYPES = {"customer_analysis", "sales_weekly", "sales_monthly", "custom"}


def _notify_report(
    session: AsyncSession, report: Report, success: bool, action: str = "生成", err: str | None = None
) -> None:
    """报告生成/修改完成或失败的通知（best-effort，绝不抛出，不影响主流程）。"""
    try:
        title = getattr(report, "title", "") or ""
        session.add(
            Notification(
                tenant_id=report.tenant_id,
                user_id=report.user_id,
                title=f"报告{action}完成" if success else f"报告{action}失败",
                content=(
                    f"《{title}》已{action}完成，点击查看。"
                    if success
                    else f"《{title}》{action}失败：{(err or '')[:120]}"
                ),
                type="report",
                resource_type="report",
                resource_id=report.id,
            )
        )
    except Exception:
        logger.warning("报告通知写入失败 report=%s", getattr(report, "id", None), exc_info=True)

# 自定义报告检索参数
CUSTOM_TOP_K = 12
CUSTOM_MAX_CONTEXT_CHARS = 4000
# revisions 历史版本上限，超出丢最旧
REVISIONS_CAP = 20


def _lang_instruction(language: str, html: bool = False) -> str:
    """报告语言指令。zh_en 时 HTML 版用 data-lang 标记，供前端一键切换显示。"""
    language = (language or "zh").strip().lower()
    if language == "en":
        return "报告语言：使用英文撰写（English），数据与数字保持原样，专业术语用标准英文表达。"
    if language == "zh_en":
        if html:
            return (
                '报告语言：中英双语。全文每个标题、段落、列表项、表格都要同时提供中文与英文两个版本，'
                '中文在前、英文紧随其后；两版内容分别用 data-lang="zh" 与 data-lang="en" 属性标记'
                '（如 <p data-lang="zh">中文</p><p data-lang="en">English</p>，表格内同理），'
                '以便阅读端一键切换语言。'
            )
        return "报告语言：中英双语。每条内容先给出中文，紧接着给出对应的英文翻译（标题/要点/表格均双语呈现）。"
    return "报告语言：简体中文。"


def build_report_prompt(report_type: str, data: dict, language: str = "zh") -> list[dict]:
    """构建报告生成 prompt。纯函数。"""
    type_names = {
        "customer_analysis": "客户分析报告",
        "sales_weekly": "销售周报",
        "sales_monthly": "销售月报",
    }
    name = type_names.get(report_type, report_type)
    lines = [f"请基于以下数据生成一份{name}：", ""]
    for key, value in data.items():
        lines.append(f"## {key}")
        if isinstance(value, list):
            if not value:
                lines.append("（无）")
            for item in value:
                lines.append(f"- {item}")
        elif isinstance(value, dict):
            for k, v in value.items():
                lines.append(f"- {k}: {v}")
        else:
            lines.append(str(value))
        lines.append("")
    system = (
        "你是 CRM 数据分析助手。基于给定的真实数据生成 Markdown 格式的分析报告，"
        "必须包含数据小结与可执行的行动建议；不要编造数据中没有的事实。"
        + _lang_instruction(language)
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n".join(lines)},
    ]


async def aggregate_customer_analysis(
    session: AsyncSession, tenant_id: int, customer_id: int
) -> dict:
    """客户分析数据：客户资料 + 商机 + 最近跟进记录。"""
    customer = await session.get(Customer, customer_id)
    if customer is None or customer.tenant_id != tenant_id or customer.deleted_at is not None:
        raise ValueError("客户不存在")

    opps = (
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
    return {
        "客户资料": {
            "姓名": customer.name,
            # 七期起行业为多选数组，兼容旧单列
            "行业": "、".join(customer.industries or []) or customer.industry or "-",
            "单位": customer.company or "-",
            "电话": customer.phone or "-",
            "邮箱": customer.email or "-",
            "状态": customer.status,
            "来源": customer.source or "-",
        },
        "商机列表": [
            f"{o.name}（金额 {o.amount}，阶段 {o.stage}，赢单率 {o.probability}%）"
            for o in opps
        ],
        "最近跟进记录": [
            f"[{f.created_at:%Y-%m-%d}] {f.type}: {f.ai_summary or f.content[:100]}"
            for f in followups
        ],
    }


async def aggregate_sales(
    session: AsyncSession, tenant_id: int, start: date, end: date
) -> dict:
    """销售周报/月报数据：时间范围内新增客户数/跟进数/商机阶段分布/成交金额。"""
    start_dt = datetime.combine(start, time.min)
    end_dt = datetime.combine(end, time.max)

    new_customers = await session.scalar(
        select(func.count())
        .select_from(Customer)
        .where(Customer.tenant_id == tenant_id, Customer.deleted_at.is_(None), Customer.created_at.between(start_dt, end_dt))
    )
    followup_count = await session.scalar(
        select(func.count())
        .select_from(FollowUpRecord)
        .join(Customer, Customer.id == FollowUpRecord.customer_id)
        .where(
            Customer.tenant_id == tenant_id,
            Customer.deleted_at.is_(None),
            FollowUpRecord.created_at.between(start_dt, end_dt),
        )
    )
    stage_rows = (
        await session.execute(
            select(Opportunity.stage, func.count(), func.coalesce(func.sum(Opportunity.amount), 0))
            .join(Customer, Customer.id == Opportunity.customer_id)
            .where(
                Customer.tenant_id == tenant_id,
                Customer.deleted_at.is_(None),
                Opportunity.created_at.between(start_dt, end_dt),
            )
            .group_by(Opportunity.stage)
        )
    ).all()
    stage_dist = {stage: count for stage, count, _ in stage_rows}
    closed_amount = sum(float(amount) for stage, _, amount in stage_rows if stage == "closed")

    return {
        "统计周期": f"{start} ~ {end}",
        "新增客户数": new_customers or 0,
        "跟进记录数": followup_count or 0,
        "商机阶段分布": stage_dist or {"（无新增商机）": 0},
        "成交金额(closed)": closed_amount,
    }


def default_date_range(report_type: str) -> tuple[date, date]:
    today = datetime.now(timezone.utc).date()
    days = 7 if report_type == "sales_weekly" else 30
    return (today - timedelta(days=days), today)


def build_presentation_prompt(source: str, fmt: str = "html") -> list[dict]:
    """演示版（16:9 HTML 幻灯片）prompt：把报告内容转成独立、可全屏演示的 HTML。纯函数。"""
    system = (
        '你是演示文稿设计助手。把给定的报告内容制作成一份独立、完整的 16:9 演示文稿 HTML 文档。'
        '要求：每个幻灯片是一个 <section class="slide">，宽度 100vw、高度 100vh，多个 slide 纵向堆叠；'
        '使用内联 CSS 做专业的商务演示设计（大标题、要点列表、彩色强调条、数据表格、纯 CSS 图表），'
        '整体视觉与报告主题一致、美观耐看；'
        '内置一个最小导航脚本：左右方向键 / PageUp / PageDown / Home / End 切换幻灯片，'
        '并在屏幕右下角提供「上一页/下一页」按钮与页码显示；'
        '禁止使用任何外部资源与网络请求，图表一律用纯 HTML/CSS 实现；'
        '只输出 HTML 文档本身，不要输出 markdown 代码围栏或任何解释。'
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"报告内容（{fmt} 格式）：\n{(source or '')[:12000]}"},
    ]


def _strip_code_fence(text: str) -> str:
    """容错去掉 LLM 输出可能带的 markdown 代码围栏。纯函数。"""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```\w*\s*\n?", "", t)
        t = re.sub(r"\n?```\s*$", "", t)
    return t.strip()


def _strip_think(text: str) -> str:
    """去掉推理型模型输出的 <think>…</think> / <thinking>…</thinking> 思考过程块。

    这类模型（如 MiniMax reasoner）会把推理过程原文夹在输出里，若直接存为报告
    正文/HTML 会污染版面。纯函数。"""
    if not text:
        return text
    stripped = re.sub(r"<(think|thinking)>.*?</\1>", "", text, flags=re.S | re.I)
    return stripped.strip()


def build_custom_report_prompt(prompt: str, context: str, language: str = "zh") -> list[dict]:
    """自定义 HTML 报告 prompt：system 约束完整独立 HTML，user 给资料+需求。纯函数。"""
    system = (
        "你是专业商务报告撰写助手。基于给定资料与用户需求生成一份完整、独立的 HTML 报告文档。"
        "要求：使用内联 CSS 做专业商务排版（标题层级、表格、要点列表），采用语义化标签；"
        "如需图表，用纯 HTML/CSS（如色块条形图）实现，禁止使用 JavaScript 或外部资源；"
        "不要编造资料中没有的事实。只输出 HTML 文档本身，不要输出 markdown 代码围栏或任何解释。"
        + _lang_instruction(language, html=True)
    )
    parts = []
    if context.strip():
        parts.append(f"资料：\n{context}")
    parts.append(f"报告需求：{prompt}")
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def build_revise_prompt(instruction: str, html: str) -> list[dict]:
    """对话式修改 prompt：给当前 HTML 全文 + 修改要求，输出修改后的完整 HTML。纯函数。"""
    system = (
        "你是报告修改助手。给定一份 HTML 报告全文和用户的修改要求，输出修改后的完整 HTML 文档。"
        "保持原有的内联 CSS 与整体排版风格，仅按修改要求调整内容。"
        "只输出 HTML 文档本身，不要输出 markdown 代码围栏或任何解释。"
    )
    user = f"当前报告 HTML：\n{html}\n\n修改要求：{instruction}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


async def _kb_names(session: AsyncSession, tenant_id: int, kb_ids: list[int] | None) -> list[str]:
    """按 id 取知识库名称（agent 模式的检索范围提示用，仅作软约束写进任务指令）。"""
    if not kb_ids:
        return []
    rows = (
        await session.execute(
            select(KnowledgeBase.name).where(
                KnowledgeBase.id.in_(kb_ids), KnowledgeBase.tenant_id == tenant_id
            )
        )
    ).scalars().all()
    return [n for n in rows if n]


def build_agent_task_prompt(
    report_type: str,
    *,
    data: dict | None = None,
    prompt: str | None = None,
    kb_names: list[str] | None = None,
    extra_context: str = "",
    language: str = "zh",
) -> str:
    """Agent 模式的任务指令（纯函数）：把报告需求交给 dsh agent，由其自行规划
    检索（kb_search/kb_read_doc）/阅读/撰写，产出报告正文。

    - 数据类报告（客户分析/销售周报月报）：数据已聚合好，直接内联，无需检索；
    - 自定义报告：给需求 + 可选知识库范围提示 + 指定文件全文内联，agent 自行检索补充。

    统一要求输出 **Markdown**：长上下文多步工具调用后直接吐完整 HTML 容易触发模型
    退化（实测出现整段 "!!!" 刷屏），custom 的 HTML 排版由后续单独的排版调用完成。
    """
    if report_type == "custom":
        parts = [
            "你是专业商务报告撰写助手。基于资料与用户需求撰写一份结构完整的 Markdown 格式报告"
            "（标题层级、表格、要点列表）。不要编造资料中没有的事实。"
            + _lang_instruction(language),
            "你可以使用知识库工具 kb_search（语义检索）与 kb_read_doc（按文档读全文）获取资料，"
            "需要最新公开信息时也可以联网搜索；多查几轮、交叉核实后再动笔。",
        ]
        if kb_names:
            parts.append("重点知识库（优先在这些范围内检索）：" + "、".join(kb_names))
        if extra_context.strip():
            parts.append(f"用户指定的附加资料（文件全文）：\n{extra_context}")
        parts.append(f"报告需求：{prompt}")
    else:
        msgs = build_report_prompt(report_type, data or {}, language)
        parts = [msgs[0]["content"], msgs[1]["content"]]
    parts.append("完成后只输出报告正文本身，不要输出任何解释、前后缀或代码围栏。")
    return "\n\n".join(parts)


def build_html_from_markdown_prompt(markdown_text: str, language: str = "zh") -> list[dict]:
    """把 Agent 产出的 Markdown 报告排版成完整独立 HTML（纯函数）。"""
    system = (
        "你是专业商务报告排版助手。把给定的 Markdown 报告内容转换成一份完整、独立的 HTML 报告文档。"
        "要求：使用内联 CSS 做专业商务排版（标题层级、表格、要点列表），采用语义化标签；"
        "如需图表，用纯 HTML/CSS（如色块条形图）实现，禁止使用 JavaScript 或外部资源；"
        "不得增删报告中的事实内容。只输出 HTML 文档本身，不要输出 markdown 代码围栏或任何解释。"
        + _lang_instruction(language, html=True)
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"报告 Markdown：\n{(markdown_text or '')[:30000]}"},
    ]


def looks_degenerate(text: str) -> bool:
    """Agent 输出退化检测（纯函数）：过短或单字符刷屏（如 "!!!…"）视为异常，触发固定管线兜底。"""
    t = (text or "").strip()
    if len(t) < 80:
        return True
    from collections import Counter

    top = Counter(t).most_common(1)[0][1]
    return top / len(t) > 0.7


async def _run_agent_turn(session: AsyncSession, report: Report, user: User, dsh_sid: str, prompt: str) -> str:
    """跑一轮 agent 对话并返回最终文本；工具调用/正文增量写回 progress/content。"""
    parts: list[str] = []
    last_commit = _time.monotonic()
    final_response = ""
    async for ev in bridge.run_turn(user, dsh_sid, prompt):
        kind = ev.get("kind")
        if kind == "error":
            raise RuntimeError(ev.get("detail") or "dsh 运行失败")
        if kind == "done":
            final_response = ev.get("final_response") or ""
            break
        # kind == "event"：工具调用显示为进度；正文 chunk 累积并周期写回（前端滚动展示）
        event = ev.get("event") or {}
        upd = event.get("session_update")
        if upd == "tool_call":
            title = str(event.get("title") or "工具调用")
            report.progress = f"Agent：{title[:60]}"
        elif upd == "agent_message_chunk":
            chunk = event.get("content") or {}
            if chunk.get("type") == "text":
                parts.append(chunk.get("text") or "")
                report.progress = f"Agent 撰写中… {sum(len(p) for p in parts)} 字"
        if _time.monotonic() - last_commit >= 2.0:
            report.content = "".join(parts) or report.content
            await session.commit()
            last_commit = _time.monotonic()
    return final_response or "".join(parts)


# Agent 输出退化后的纠偏追问（实测 kimi-k2.6 在多步工具调用后的最终撰写步偶发整段 "!" 刷屏，
# 同会话追问一次明确"不要调用工具、直接写正文"通常能恢复正常输出）
_AGENT_RETRY_NUDGE = (
    "你的上一次回复是乱码（一连串“!”），内容无效。现在请不要再调用任何工具，"
    "直接基于已经收集到的资料，把完整的 Markdown 报告正文一次性写出来。"
)


async def _run_agent_report(session: AsyncSession, report: Report, task_prompt: str) -> str:
    """Agent 模式生成正文：新建 dsh 会话跑 agent，退化时同会话纠偏重试一次。

    失败抛异常，由 generate_report 统一标记 failed。"""
    user = await session.get(User, report.user_id)
    if user is None:
        raise ValueError("报告属主用户不存在")
    report.progress = "Agent 正在启动…"
    await session.commit()
    dsh_sid = await bridge.new_session(user)
    content = await _run_agent_turn(session, report, user, dsh_sid, task_prompt)
    if looks_degenerate(content):
        logger.warning("报告 %s Agent 输出退化（%d 字），同会话纠偏重试", report.id, len(content))
        report.progress = "Agent 输出异常，正在纠偏重试…"
        report.content = None
        await session.commit()
        content = await _run_agent_turn(session, report, user, dsh_sid, _AGENT_RETRY_NUDGE)
    return content


async def aggregate_custom(
    db: AsyncSession,
    tenant_id: int,
    prompt: str,
    kb_ids: list[int] | None = None,
    file_ids: list[int] | None = None,
    user: User | None = None,
) -> dict:
    """自定义报告素材：选中文件小件直读全文；否则混合检索（向量+trgm RRF）。
    kb_ids/file_ids 为空则全租户检索；提供 user 时按其 ACL 限定可读范围（与 rag_query 同口径）。
    返回 {context, sources}；嵌入不可用抛 EmbeddingUnavailable。"""
    # 权限范围：未指定 kb_ids 时检索"用户可读的所有 KB"；指定时过滤掉无权限的；
    # 文件直读/检索同样只允许用户可读的文件。过滤后无可读范围则返回空素材，
    # 避免空列表在 search_chunks_* 里退化为"全租户不设限"。
    if user is not None:
        if kb_ids is None:
            kb_ids = await accessible_ids(db, user, "kb")
        else:
            kb_ids = await filter_accessible_ids(db, user, "kb", kb_ids)
        if file_ids:
            file_ids = await filter_accessible_ids(db, user, "file", file_ids)
        if not kb_ids and not file_ids:
            return {"context": "", "sources": []}    # PR-H：选中的 file_ids 总字符低于阈值时直接读全文（跳过嵌入/检索），
    # 确保"分析这个文件"的报告素材真的用上所选文件，而不是被静默忽略
    if file_ids:
        from app.services.file_context import build_direct_file_context

        direct = await build_direct_file_context(db, tenant_id, file_ids)
        if direct["context"] and not direct["too_large"]:
            prompt_chunks = []
            sources = []
            for info in direct["files"]:
                if info.get("chars", 0) <= 0 or info.get("skipped"):
                    continue
                marker = f"## {info['name']}\n"
                idx = direct["context"].find(marker)
                if idx == -1:
                    continue
                end = direct["context"].find("\n## ", idx + len(marker))
                body = direct["context"][idx + len(marker): end if end != -1 else None]
                prompt_chunks.append({"doc_title": info["name"], "content": body})
                sources.append({
                    "chunk_id": 0, "doc_id": 0,
                    "doc_title": info["name"],
                    "score": 1.0,
                    "excerpt": body[:200],
                    "direct": True,
                })
            context = "\n\n".join(
                f"【{c['doc_title']}】\n{c['content']}" for c in prompt_chunks
            )
            return {"context": context, "sources": sources}

    try:
        embed_llm = await resolve_embed_llm(caller="report", tenant_id=tenant_id)
        query_vec = (await embed_llm.embed([prompt]))[0]
    except EmbeddingUnavailable:
        raise
    except Exception as exc:
        logger.warning("自定义报告嵌入失败: %s", exc)
        raise EmbeddingUnavailable() from exc

    vector_rows = await search_chunks_vector(db, tenant_id, query_vec, CUSTOM_TOP_K * 2, kb_ids, file_ids)
    if settings.RAG_HYBRID:
        keyword_rows = await search_chunks_keyword(db, tenant_id, prompt, CUSTOM_TOP_K * 2, kb_ids, file_ids)
        candidates = reciprocal_rank_fusion(vector_rows, keyword_rows)
    else:
        candidates = vector_rows

    # 精排：rerank 开启时重排候选（三级降级，失败回退 RRF 序）。
    # 报告场景不做硬阈值过滤——保持素材广度，与对话的阈值过滤不同。
    hits = candidates[:CUSTOM_TOP_K]
    if settings.RAG_RERANK and candidates:
        reranked = await rerank_chunks(
            prompt, candidates[: CUSTOM_TOP_K * 2],
            tenant_id=tenant_id, user_id=user.id if user else None,
        )
        if reranked:
            hits = reranked[:CUSTOM_TOP_K]
    if not hits:
        return {"context": "", "sources": []}

    blocks = await expand_contexts(db, tenant_id, hits, 0)
    blocks = trim_blocks(blocks, CUSTOM_MAX_CONTEXT_CHARS)
    context = "\n\n".join(f"【{b['doc_title']}】\n{b['content']}" for b in blocks)

    sources = []
    for h in hits:
        block = _find_block(blocks, h)
        excerpt_source = block["content"] if block else h["content"]
        sources.append(
            {
                "chunk_id": h["chunk_id"],
                "doc_id": h["doc_id"],
                "doc_title": h["doc_title"],
                "score": round(float(h.get("rerank_score", h["score"])), 4),
                "excerpt": excerpt_source[:200],
            }
        )
    await attach_file_info(db, sources)
    return {"context": context, "sources": sources}


async def generate_report(report_id: int) -> None:
    """后台任务：聚合数据 → 流式调 chat LLM → 增量写回 content 供前端滚动展示。

    失败只标记 failed，绝不抛出。progress 字段实时记录阶段提示。"""
    async with AsyncSessionLocal() as session:
        report = await session.get(Report, report_id)
        if report is None:
            logger.warning("generate_report: 报告 %s 不存在", report_id)
            return
        try:
            params = report.params or {}
            lang = (params.get("language") or "zh").lower()
            sources = None
            use_agent = bool(params.get("agent"))
            messages: list[dict] | None = None
            agent_task: str | None = None
            if report.type == "customer_analysis":
                report.progress = "正在汇总客户资料…"
                await session.commit()
                data = await aggregate_customer_analysis(
                    session, report.tenant_id, int(params["customer_id"])
                )
                if use_agent:
                    agent_task = build_agent_task_prompt(report.type, data=data, language=lang)
                else:
                    messages = build_report_prompt(report.type, data, lang)
            elif report.type in ("sales_weekly", "sales_monthly"):
                report.progress = "正在统计销售数据…"
                await session.commit()
                default_start, default_end = default_date_range(report.type)
                start = date.fromisoformat(params.get("start_date") or str(default_start))
                end = date.fromisoformat(params.get("end_date") or str(default_end))
                data = await aggregate_sales(session, report.tenant_id, start, end)
                if use_agent:
                    agent_task = build_agent_task_prompt(report.type, data=data, language=lang)
                else:
                    messages = build_report_prompt(report.type, data, lang)
            elif report.type == "custom":
                # 报告属主：服务层检索/直读文件需按其 ACL 限定可读范围
                owner = await session.get(User, report.user_id)
                if use_agent:
                    # Agent 模式：kb_ids 只作检索范围提示（软约束），file_ids 小件内联全文
                    report.progress = "Agent 正在准备资料…"
                    await session.commit()
                    kb_names = await _kb_names(session, report.tenant_id, params.get("kb_ids"))
                    extra = ""
                    if params.get("file_ids"):
                        from app.services.file_context import build_direct_file_context

                        direct = await build_direct_file_context(
                            session, report.tenant_id, params["file_ids"], user=owner
                        )
                        if not direct["too_large"]:
                            extra = direct["context"]
                    agent_task = build_agent_task_prompt(
                        "custom", prompt=params["prompt"], kb_names=kb_names,
                        extra_context=extra, language=lang,
                    )
                else:
                    report.progress = "正在检索知识库资料…"
                    await session.commit()
                    result = await aggregate_custom(
                        session, report.tenant_id, params["prompt"],
                        params.get("kb_ids"), params.get("file_ids"), user=owner,
                    )
                    sources = result["sources"]
                    messages = build_custom_report_prompt(params["prompt"], result["context"], lang)
            else:
                raise ValueError(f"未知的报告类型: {report.type}")

            chat_llm = await resolve_chat_llm(caller="report", tenant_id=report.tenant_id, user_id=report.user_id)
            timeout = params.get("timeout")
            agent_used = False
            if agent_task is not None:
                # Agent 模式：dsh 多步检索/阅读/撰写（Markdown 正文），增量写回（进度由工具事件驱动）
                try:
                    content = await _run_agent_report(session, report, agent_task)
                except Exception as exc:
                    # agent 运行失败（如模型配额 429）不应让报告整体失败：回退固定管线
                    logger.warning("报告 %s Agent 运行失败，回退固定管线: %s", report_id, exc)
                    content = ""
                if looks_degenerate(content):
                    # 模型长上下文偶发退化（如整段 "!!!" 刷屏）或运行失败：回退固定管线兜底
                    if content:
                        logger.warning("报告 %s Agent 输出退化（%d 字），回退固定管线", report_id, len(content))
                    report.progress = "Agent 不可用，改用固定管线生成…"
                    report.content = None
                    await session.commit()
                    if report.type == "custom":
                        result = await aggregate_custom(
                            session, report.tenant_id, params["prompt"],
                            params.get("kb_ids"), params.get("file_ids"),
                            user=await session.get(User, report.user_id),
                        )
                        sources = result["sources"]
                        messages = build_custom_report_prompt(params["prompt"], result["context"], lang)
                    else:
                        messages = build_report_prompt(report.type, data, lang)
                else:
                    agent_used = True
            if not agent_used:
                # 固定管线：流式生成 + 每 ~2s 增量写回 content/progress，前端轮询即可看到"滚动生成"
                report.progress = "AI 正在生成报告…"
                await session.commit()
                parts: list[str] = []
                last_commit = _time.monotonic()
                async for token in chat_llm.chat_stream(messages, timeout=timeout):
                    parts.append(token)
                    if _time.monotonic() - last_commit >= 2.0:
                        report.content = "".join(parts)
                        report.progress = f"AI 生成中… {len(report.content)} 字"
                        await session.commit()
                        last_commit = _time.monotonic()
                content = "".join(parts)
            if not content.strip():
                raise ValueError("报告正文为空")
            if report.type == "custom":
                content = _strip_code_fence(_strip_think(content))
                if agent_used:
                    # Agent 产出的是 Markdown：单独一次排版调用转成完整独立 HTML
                    report.progress = "正在排版 HTML…"
                    report.content = content
                    await session.commit()
                    content = _strip_code_fence(_strip_think(
                        await chat_llm.chat(build_html_from_markdown_prompt(content, lang), timeout=timeout)
                    ))
                report.params = {**params, "sources": sources, "agent_used": agent_used}
            else:
                content = _strip_think(content)
                report.params = {**dict(params), "agent_used": agent_used}
            report.content = content
            # 演示版：独立 16:9 HTML 幻灯片（best-effort，失败则演示按钮不可用）
            try:
                report.progress = "正在排版演示版…"
                await session.commit()
                pres = await chat_llm.chat(
                    build_presentation_prompt(content, report.format), timeout=timeout
                )
                # JSONB 列必须整体重赋值，就地修改 SQLAlchemy 感知不到会丢更新
                report.params = {**(report.params or {}), "presentation_html": _strip_code_fence(_strip_think(pres))}
            except Exception:
                logger.warning("报告 %s 演示版生成失败", report_id, exc_info=True)
                report.params = {**(report.params or {}), "presentation_html": ""}
            report.status = "ready"
            report.error = None
            report.progress = None
            _notify_report(session, report, True)
        except Exception as exc:
            logger.exception("报告 %s 生成失败", report_id)
            report.status = "failed"
            report.error = str(exc)[:500]
            report.progress = None
            from app.services.error_log import log_error

            await log_error("error", "report", f"报告 {report_id} 生成失败", str(exc), tenant_id=report.tenant_id)
            _notify_report(session, report, False, err=str(exc))
        await session.commit()


async def revise_report(report_id: int, instruction: str) -> None:
    """后台任务：对话式修改 custom 报告。旧版本存入 params["revisions"]（上限 REVISIONS_CAP）。
    失败只把 status 回 ready 并记录 error，绝不抛出。"""
    async with AsyncSessionLocal() as session:
        report = await session.get(Report, report_id)
        if report is None:
            logger.warning("revise_report: 报告 %s 不存在", report_id)
            return
        try:
            timeout = (report.params or {}).get("timeout")
            report.progress = "AI 正在修改报告…"
            await session.commit()
            chat_llm = await resolve_chat_llm(caller="report", tenant_id=report.tenant_id, user_id=report.user_id)
            new_content = await chat_llm.chat(build_revise_prompt(instruction, report.content or ""), timeout=timeout)
            params = dict(report.params or {})
            revisions = list(params.get("revisions") or [])
            revisions.append(
                {
                    "instruction": instruction,
                    "content": report.content,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            params["revisions"] = revisions[-REVISIONS_CAP:]
            report.params = params
            report.content = _strip_code_fence(_strip_think(new_content))
            # 修改后内容变了：重新生成演示版（best-effort）
            try:
                report.progress = "正在重新排版演示版…"
                await session.commit()
                pres = await chat_llm.chat(
                    build_presentation_prompt(report.content, report.format), timeout=timeout
                )
                params["presentation_html"] = _strip_code_fence(_strip_think(pres))
            except Exception:
                logger.warning("报告 %s 修改后演示版生成失败", report_id, exc_info=True)
                params.setdefault("presentation_html", "")
            report.status = "ready"
            report.error = None
            report.progress = None
            _notify_report(session, report, True, action="修改")
        except Exception as exc:
            logger.exception("报告 %s 修改失败", report_id)
            report.status = "ready"  # 修改失败保留旧版本可读
            report.error = str(exc)[:500]
            report.progress = None
            from app.services.error_log import log_error

            await log_error("error", "report", f"报告 {report_id} 修改失败", str(exc), tenant_id=report.tenant_id)
            _notify_report(session, report, False, action="修改", err=str(exc))
        await session.commit()
