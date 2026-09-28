import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/

# Playwright 浏览器二进制放项目内（须在 import playwright 之前生效）
os.environ.setdefault(
    "PLAYWRIGHT_BROWSERS_PATH", str(BASE_DIR / "data" / "ms-playwright")
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 运行环境：dev / sandbox / prod（非 dev/test 时启动校验 JWT 密钥强度；
    # sandbox 不在弱 JWT 白名单内，与 prod 一样强制强密钥）
    ENV: str = "dev"

    # 数据库
    DATABASE_URL: str = "postgresql+asyncpg://crm:crm123@localhost:5432/crmnkb"

    # JWT
    JWT_SECRET: str = "dev-secret-change-me"
    JWT_EXPIRE_MINUTES: int = 720

    # CORS 允许来源（逗号分隔显式域名；鉴权走 Authorization 头，不开 credentials）
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"

    # 单文件上传大小上限（MB），超限 413
    MAX_UPLOAD_MB: int = 100

    # 文件上传目录（相对 backend 目录）
    UPLOAD_DIR: str = "data/uploads"

    # 前端构建产物目录（裸机单端口部署用，相对 backend 目录或绝对路径）。
    # 非空且存在 index.html 时，由后端直接托管前端静态文件与 SPA 路由回退，
    # 无需 nginx；留空则不启用，不影响"独立 nginx 托管前端"等既有部署方式。
    FRONTEND_DIST: str = ""

    # LLM 提供方: api / ollama
    LLM_CHAT_PROVIDER: str = "api"
    LLM_EMBED_PROVIDER: str = "ollama"

    # OpenAI 兼容 API（chat 与 embed 共用 base/key）
    LLM_API_BASE_URL: str = "https://api.deepseek.com/v1"
    LLM_API_KEY: str = ""
    LLM_API_CHAT_MODEL: str = "deepseek-chat"
    LLM_API_EMBED_MODEL: str = "text-embedding-3-small"
    # 非流式 chat 调用超时（秒）：自定义报告等长文 HTML 生成可能超过 60s，默认放宽到 180
    LLM_CHAT_TIMEOUT_SECONDS: int = 180

    # 备份用的 PG 容器名：非空则用 `docker exec <容器> pg_dump/psql`，空则用本机二进制
    PG_DOCKER_CONTAINER: str = "crmnkb-postgres"

    # 系统告警检查间隔（秒）；磁盘/内存/LLM 失败超阈值时通知管理员
    ALERT_INTERVAL_SECONDS: int = 300
    ALERT_DISK_PERCENT: int = 85
    ALERT_MEM_PERCENT: int = 90
    ALERT_LLM_FAIL_WINDOW_MIN: int = 10
    ALERT_LLM_FAIL_THRESHOLD: int = 5

    # Ollama 本地服务
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_CHAT_MODEL: str = "qwen2.5:7b"
    OLLAMA_EMBED_MODEL: str = "bge-m3"

    # 向量维度（bge-m3 = 1024）
    EMBEDDING_DIM: int = 1024

    # RAG
    # 兜底阈值：只用于"完全没有候选"的粗筛，不追求精确校准（本地 qwen-embed 的
    # 相似度分布较窄，0.7 会误杀真实命中）。真实相关性由 Rerank 排序 + LLM 判断。
    RAG_SCORE_THRESHOLD: float = 0.5
    RAG_TOP_K: int = 5
    # 混合检索：true=向量+关键词(pg_trgm) RRF 融合，false=纯向量
    RAG_HYBRID: bool = True
    # Small2Big：命中切片向同文档相邻 chunk_index ±N 扩展
    RAG_NEIGHBOR_WINDOW: int = 1
    # 多轮对话：检索前结合对话历史改写问题（指代消解），失败自动用原问题
    RAG_QUERY_REWRITE: bool = True
    # Rerank 精排：三级降级——专用 rerank 模型（Jina/SiliconFlow/TEI 兼容接口）→ chat LLM 打分 → 回退 RRF 序
    RAG_RERANK: bool = True
    # PR-H：选中的 file_ids 总字符数低于此阈值时，直接读全文塞进 prompt（跳过 RAG）。
    # 适合"快速小文档问答"，避免等 ingestion。超阈值走原 RAG file_ids 检索。
    RAG_DIRECT_FILE_MAX_CHARS: int = 50000
    # 上下文长度裁剪：按融合排序累计加入直到达到字符上限
    RAG_MAX_CONTEXT_CHARS: int = 4000
    # 检索模式：blend=pgvector+tsvector 二阶段（推荐），embedding=纯向量，keywords=纯 tsvector BM25
    RAG_SEARCH_MODE: str = "blend"
    # 自动生成问题：ingestion 时为每个 chunk 用 LLM 生成 N 个候选问题入 chunk_questions 表
    RAG_GENERATE_QUESTIONS: bool = True
    # 每个 chunk 生成几个候选问题（3-5 性价比最好）
    RAG_QUESTIONS_PER_CHUNK: int = 3

    # 工作台聊天未命中知识库时的兜底策略：
    # true=降级为普通对话，由聊天模型用通用知识正常回答（提示词会声明"未参考企业知识库"，
    #     涉及内部信息时会如实告知库内暂无资料）；生成失败仍回退固定话术。
    # false=维持原行为：固定回复 FALLBACK_ANSWER（严格"只答知识库"模式）。
    # 仅影响聊天管线（chat_ask / stream_chat_events）；知识库检索问答接口保持严格的库内回答。
    CHAT_FALLBACK_TO_LLM: bool = True

    # 工作台聊天"深度思考"开关：
    # 1) CHAT_DEFAULT_THINKING：未显式指定时聊天默认是否开启思考（推理）模式。
    # 2) LLM_CHAT_THINKING_PARAM：关闭思考时随请求体发送的模型参数名，值为 false。
    #    MiniMax 等 OpenAI 兼容接口为 enable_thinking；其他服务商按实际文档调整。
    #    置空则关闭思考时不发送该参数（仅剥离输出中的 <think> 块，不省 token）。
    CHAT_DEFAULT_THINKING: bool = True
    LLM_CHAT_THINKING_PARAM: str = "enable_thinking"

    # 图片/扫描 PDF 的 OCR 方案（处理三级递进：文字提取 → 本地 OCR → 视觉模型）：
    # auto=本地 OCR 优先（RapidOCR → Tesseract，均不可用时回退视觉）
    # rapidocr / tesseract = 强制指定本地引擎（不可用回退视觉）
    # vision=跳过本地 OCR，直接用视觉模型（最慢但零额外依赖）
    OCR_PROVIDER: str = "auto"
    # 图片本地 OCR 文本达到该字符数视为"含文字"，直接用 OCR；否则交给视觉模型描述
    OCR_IMAGE_MIN_CHARS: int = 10
    # 扫描 PDF 每页本地 OCR 最低字符数，低于则整份回退视觉模型
    OCR_PDF_MIN_CHARS_PER_PAGE: int = 20

    # 客户画像：聚合文档内容等的字符上限
    PROFILE_MAX_CHARS: int = 8000

    # 提醒规则引擎调度间隔（分钟）
    REMINDER_INTERVAL_MINUTES: int = 10

    # 系统更新脚本（裸机部署用；「系统设置 → 系统更新」按钮触发，admin 限定）。
    # 指向服务器上的 shell 脚本绝对路径（模板见 deploy/update.sh），脚本负责
    # git pull → 依赖安装/前端构建 → 延迟 systemctl restart。留空 = 功能关闭，
    # 前端按钮不显示。脚本路径只能在这里配置，不接受前端传参（防命令注入）。
    UPDATE_SCRIPT: str = ""

    # SMTP 邮件服务（工作流 send_email 动作用；SMTP_HOST 为空时邮件功能降级）
    SMTP_HOST: str = ""
    SMTP_PORT: int = 465
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""
    SMTP_USE_SSL: bool = True

    # 切片
    CHUNK_SIZE: int = 512
    CHUNK_OVERLAP: int = 64

    # dsh agent 基座（dsh 基座实施方案阶段 2：ACP 切换，单租户单进程 + 每会话挂 MCP）
    # 总开关：false 时 /chat/ask/agent/stream 返回 503，dsh ACP 进程不启动
    DSH_AGENT_ENABLED: bool = False
    # dsh 可执行文件路径（ACP 模式必填）：Windows 必须指 .cmd shim，Linux 指无后缀 shim
    DSH_BIN: str = ""
    # dsh 运行时 home（profile/凭据/插件注册态，相对 backend 目录）
    DSH_HOME: str = "data/dsh/home"
    # 进程级 cordis patch yml 目录（base.yml 为 PG 会话持久化静态 patch，
    # acp-model.yml 由 acp_bridge 按 DB 默认 chat 模型自动重写）
    DSH_PATCHES_DIR: str = "data/dsh/patches"
    # agent 会话工作目录（所有会话共用同一 cwd：session/resume 要求 cwd 与创建时一致）
    DSH_WORKSPACE_ROOT: str = "data/dsh/workspace"
    # （ACP 切换后不再使用：模型路由固定为进程级 patch 里的 llm-pi-ai kbcrm 路由）
    DSH_PROVIDER: str = "deepseek-official"
    # 注入 dsh 的知识库 MCP server 地址（streamable-http，每会话 session/new|resume 挂载）
    DSH_MCP_URL: str = "http://127.0.0.1:8100/api/mcp"
    # dsh 专用 MCP 令牌有效期（分钟）：每次会话激活（session/new|resume）时新签，
    # 随 mcpServers headers 注入，不落盘；过期只需下次会话激活，无需重建进程
    DSH_MCP_TOKEN_EXPIRE_MINUTES: int = 10080

    @property
    def upload_path(self) -> Path:
        p = Path(self.UPLOAD_DIR)
        if not p.is_absolute():
            p = BASE_DIR / p
        return p

    @property
    def dsh_home_path(self) -> Path:
        """dsh 运行时 home 目录（profile/凭据/会话存储）。"""
        p = Path(self.DSH_HOME)
        if not p.is_absolute():
            p = BASE_DIR / p
        return p

    @property
    def dsh_patches_path(self) -> Path:
        """进程级 cordis patch yml 目录（base.yml + acp-model.yml）。"""
        p = Path(self.DSH_PATCHES_DIR)
        if not p.is_absolute():
            p = BASE_DIR / p
        return p

    @property
    def dsh_workspace_path(self) -> Path:
        """dsh agent 工作目录根。"""
        p = Path(self.DSH_WORKSPACE_ROOT)
        if not p.is_absolute():
            p = BASE_DIR / p
        return p

    @property
    def brand_path(self) -> Path:
        """品牌资源目录：自定义 logo 等（通过 /brand 静态服务公开）。"""
        return BASE_DIR / "data" / "brand"

    @property
    def backup_path(self) -> Path:
        """备份目录（DB dump + 上传目录归档）。"""
        return BASE_DIR / "data" / "backups"

    @property
    def ocr_models_path(self) -> Path:
        """本地 OCR（RapidOCR）模型缓存目录。"""
        return BASE_DIR / "data" / "ocr-models"

    @property
    def avatars_path(self) -> Path:
        """个人头像目录（经 /avatars 静态服务公开）。"""
        return BASE_DIR / "data" / "avatars"

    @property
    def sync_app_path(self) -> Path:
        """同步客户端安装包目录（网页端提供下载，客户端自动更新用）。"""
        return BASE_DIR / "data" / "sync_app"

    @property
    def frontend_dist_path(self) -> Path | None:
        """前端构建产物目录；未配置或目录不存在时返回 None（不启用托管）。"""
        if not self.FRONTEND_DIST:
            return None
        p = Path(self.FRONTEND_DIST)
        if not p.is_absolute():
            p = BASE_DIR / p
        return p

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    def validate_jwt_secret(self) -> str | None:
        """返回告警信息；非 dev 环境密钥不合规时抛 RuntimeError（拒绝启动）。"""
        weak = self.JWT_SECRET == "dev-secret-change-me" or len(self.JWT_SECRET) < 32
        if not weak:
            return None
        msg = "JWT_SECRET 仍为默认值或长度不足 32，存在伪造令牌风险"
        if self.ENV.lower() in ("dev", "development", "test"):
            return msg
        raise RuntimeError(f"{msg}，请先设置强随机 JWT_SECRET（当前 ENV={self.ENV}）")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
