"""可观测性基础设施：request-id 追踪 + 进程内 Prometheus 指标。

- RequestIdFilter：logging 过滤器，把 contextvars 里的 request-id 注入日志记录；
  日志格式保持人类可读，仅追加 request_id 字段（无请求上下文时为 "-"）。
- ObservabilityMiddleware：纯 ASGI 中间件（刻意不用 BaseHTTPMiddleware，
  避免其响应包装干扰 SSE 流式接口），生成/透传 X-Request-ID（响应头回写），
  并累计请求计数 / 延迟直方图。
- render_metrics()：手写 Prometheus 文本格式（0.0.4）输出，不引入 prometheus-client 依赖。
"""
import logging
import time
import uuid
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    """把当前请求的 request-id 注入日志记录（无请求上下文时为 "-"）。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def install_request_id_logging() -> None:
    """配置根日志（人类可读格式 + request_id 字段）并给已有 handler 挂 RequestIdFilter。

    basicConfig 在已有 handler 时是 no-op（如 pytest/uvicorn 预配置场景），
    此时不会引入含 %(request_id)s 的格式，也就不会有缺字段的 KeyError。
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] [%(request_id)s] %(message)s",
    )
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, RequestIdFilter) for f in handler.filters):
            handler.addFilter(RequestIdFilter())


# ---------------------------------------------------------------------------
# 进程内指标（请求计数 / 延迟直方图 / uptime），由 /metrics 端点渲染输出
# ---------------------------------------------------------------------------

_START_TIME = time.time()

# 直方图桶边界（秒），覆盖常见 API 延迟分布
_HISTOGRAM_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

# (method, path, status_class) -> 请求数
_request_counts: dict[tuple[str, str, str], int] = {}
# (method, path) -> [各桶计数..., 总次数, 耗时总和]
_histograms: dict[tuple[str, str], list] = {}


def _normalize_path(path: str) -> str:
    """路径标签归一化：纯数字段替换为 {id}，避免高基数标签打爆指标内存。"""
    return "/".join("{id}" if seg.isdigit() else seg for seg in path.split("/"))


def record_request(method: str, path: str, status: int, elapsed: float) -> None:
    """记录一次请求：累计计数 + 直方图（桶计数为累积式，与 Prometheus 约定一致）。"""
    path = _normalize_path(path)
    status_class = f"{status // 100}xx"
    key = (method, path, status_class)
    _request_counts[key] = _request_counts.get(key, 0) + 1
    hkey = (method, path)
    hist = _histograms.setdefault(hkey, [0] * len(_HISTOGRAM_BUCKETS) + [0, 0.0])
    for i, bound in enumerate(_HISTOGRAM_BUCKETS):
        if elapsed <= bound:
            hist[i] += 1
    hist[-2] += 1
    hist[-1] += elapsed


def _esc(value: str) -> str:
    """Prometheus 标签值转义。"""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render_metrics(db_up: bool) -> str:
    """生成 Prometheus 文本格式（0.0.4）指标输出。db_up 为抓取时实时 DB ping 结果。"""
    lines = [
        "# HELP http_requests_total HTTP 请求总数（按方法/路径/状态类别）",
        "# TYPE http_requests_total counter",
    ]
    for (method, path, status_class), count in sorted(_request_counts.items()):
        lines.append(
            f'http_requests_total{{method="{_esc(method)}",path="{_esc(path)}",'
            f'status="{status_class}"}} {count}'
        )
    lines += [
        "# HELP http_request_duration_seconds HTTP 请求处理耗时（秒）",
        "# TYPE http_request_duration_seconds histogram",
    ]
    bucket_n = len(_HISTOGRAM_BUCKETS)
    for (method, path), hist in sorted(_histograms.items()):
        labels = f'method="{_esc(method)}",path="{_esc(path)}"'
        for bound, count in zip(_HISTOGRAM_BUCKETS, hist[:bucket_n]):
            lines.append(f"http_request_duration_seconds_bucket{{{labels},le=\"{bound:g}\"}} {count}")
        lines.append(f'http_request_duration_seconds_bucket{{{labels},le="+Inf"}} {hist[-2]}')
        lines.append(f"http_request_duration_seconds_sum{{{labels}}} {hist[-1]:.6f}")
        lines.append(f"http_request_duration_seconds_count{{{labels}}} {hist[-2]}")
    lines += [
        "# HELP process_uptime_seconds 进程启动至今的秒数",
        "# TYPE process_uptime_seconds gauge",
        f"process_uptime_seconds {time.time() - _START_TIME:.3f}",
        "# HELP db_up 数据库连通性（1=可达，0=不可达，抓取时实时 SELECT 1）",
        "# TYPE db_up gauge",
        f"db_up {1 if db_up else 0}",
    ]
    return "\n".join(lines) + "\n"


class ObservabilityMiddleware:
    """纯 ASGI 中间件：X-Request-ID 透传/生成与回写 + 请求指标采集。

    - 请求头带 X-Request-ID 则透传，否则生成 uuid4 hex；一律回写响应头。
    - request-id 写入 contextvar（RequestIdFilter 注入日志），请求结束复位。
    - /metrics 自身不计入指标，避免抓取流量自我污染。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.lower(): v for k, v in scope.get("headers") or []}
        request_id = headers.get(b"x-request-id", b"").decode("latin-1").strip() or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        start = time.perf_counter()
        status_code = 500  # 异常中断（无响应）时按 500 计

        async def send_with_request_id(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                raw = message.setdefault("headers", [])
                raw.append((b"x-request-id", request_id.encode("latin-1")))
            await send(message)

        path = scope.get("path", "")
        method = scope.get("method", "")
        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            if path != "/metrics":
                record_request(method, path, status_code, time.perf_counter() - start)
            request_id_var.reset(token)
