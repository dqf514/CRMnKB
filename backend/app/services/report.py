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
from app.models.notification import Notification
from app.models.opportunity import Opportunity
from app.models.report import Report
from app.services.llm import resolve_chat_llm, resolve_embed_llm
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


async def aggregate_custom(
    db: AsyncSession,
    tenant_id: int,
    prompt: str,
    kb_ids: list[int] | None = None,
    file_ids: list[int] | None = None,
) -> dict:
    """自定义报告素材：选中文件小件直读全文；否则混合检索（向量+trgm RRF）。
    kb_ids/file_ids 为空则全租户检索。
    返回 {context, sources}；嵌入不可用抛 EmbeddingUnavailable。"""
    # PR-H：选中的 file_ids 总字符低于阈值时直接读全文（跳过嵌入/检索），
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
        reranked = await rerank_chunks(prompt, candidates[: CUSTOM_TOP_K * 2])
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
            if report.type == "customer_analysis":
                report.progress = "正在汇总客户资料…"
                await session.commit()
                data = await aggregate_customer_analysis(
                    session, report.tenant_id, int(params["customer_id"])
                )
                messages = build_report_prompt(report.type, data, lang)
            elif report.type in ("sales_weekly", "sales_monthly"):
                report.progress = "正在统计销售数据…"
                await session.commit()
                default_start, default_end = default_date_range(report.type)
                start = date.fromisoformat(params.get("start_date") or str(default_start))
                end = date.fromisoformat(params.get("end_date") or str(default_end))
                data = await aggregate_sales(session, report.tenant_id, start, end)
                messages = build_report_prompt(report.type, data, lang)
            elif report.type == "custom":
                report.progress = "正在检索知识库资料…"
                await session.commit()
                result = await aggregate_custom(
                    session, report.tenant_id, params["prompt"],
                    params.get("kb_ids"), params.get("file_ids"),
                )
                sources = result["sources"]
                messages = build_custom_report_prompt(params["prompt"], result["context"], lang)
            else:
                raise ValueError(f"未知的报告类型: {report.type}")

            chat_llm = await resolve_chat_llm(caller="report", tenant_id=report.tenant_id, user_id=report.user_id)
            timeout = params.get("timeout")
            # 流式生成 + 每 ~2s 增量写回 content/progress，前端轮询即可看到"滚动生成"
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
            if report.type == "custom":
                content = _strip_code_fence(_strip_think(content))
                report.params = {**params, "sources": sources}
            else:
                content = _strip_think(content)
                report.params = dict(params)
            report.content = content
            # 演示版：独立 16:9 HTML 幻灯片（best-effort，失败则演示按钮不可用）
            try:
                report.progress = "正在排版演示版…"
                await session.commit()
                pres = await chat_llm.chat(
                    build_presentation_prompt(content, report.format), timeout=timeout
                )
                report.params["presentation_html"] = _strip_code_fence(_strip_think(pres))
            except Exception:
                logger.warning("报告 %s 演示版生成失败", report_id, exc_info=True)
                report.params.setdefault("presentation_html", "")
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
