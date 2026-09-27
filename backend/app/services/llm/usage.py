import logging
from collections import defaultdict

from app.models.llm_call_log import LlmCallLog

logger = logging.getLogger(__name__)


async def record_call_log(entry: dict) -> None:
    """写一条 LLM 调用日志；任何失败静默（绝不影响主流程）。"""
    try:
        from app.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            session.add(LlmCallLog(**entry))
            await session.commit()
    except Exception as exc:
        logger.debug("LLM 调用日志写入失败（忽略）: %s", exc)


def aggregate_stats(rows: list[dict]) -> dict:
    """把窗口内的调用日志聚合成监控统计。纯函数。
    rows 字段：model, caller, success, latency_ms, prompt_tokens, completion_tokens,
    error, created_at(datetime)。"""
    total = len(rows)
    success_count = sum(1 for r in rows if r["success"])
    total_tokens = sum(
        (r.get("prompt_tokens") or 0) + (r.get("completion_tokens") or 0) for r in rows
    )
    avg_latency = round(sum(r["latency_ms"] for r in rows) / total) if total else 0

    models: dict[str, dict] = {}
    days: dict[str, dict] = {}
    callers: dict[str, int] = defaultdict(int)
    for r in rows:
        tokens = (r.get("prompt_tokens") or 0) + (r.get("completion_tokens") or 0)
        m = models.setdefault(r["model"], {"model": r["model"], "calls": 0, "tokens": 0, "success": 0})
        m["calls"] += 1
        m["tokens"] += tokens
        m["success"] += 1 if r["success"] else 0
        day = r["created_at"].strftime("%Y-%m-%d")
        d = days.setdefault(day, {"date": day, "calls": 0, "tokens": 0})
        d["calls"] += 1
        d["tokens"] += tokens
        callers[r["caller"]] += 1

    by_model = [
        {
            "model": m["model"],
            "calls": m["calls"],
            "tokens": m["tokens"],
            "success_rate": round(m["success"] / m["calls"], 4) if m["calls"] else 0.0,
        }
        for m in sorted(models.values(), key=lambda x: -x["calls"])
    ]
    failures = sorted(
        (r for r in rows if not r["success"]),
        key=lambda r: r["created_at"],
        reverse=True,
    )[:5]
    return {
        "total_calls": total,
        "success_rate": round(success_count / total, 4) if total else 0.0,
        "avg_latency_ms": avg_latency,
        "total_tokens": total_tokens,
        "by_model": by_model,
        "by_day": [days[k] for k in sorted(days)],
        "by_caller": [{"caller": c, "calls": n} for c, n in sorted(callers.items(), key=lambda x: -x[1])],
        "recent_failures": [
            {
                "model": r["model"],
                "caller": r["caller"],
                "error": r.get("error"),
                "created_at": r["created_at"].isoformat(),
            }
            for r in failures
        ],
    }
