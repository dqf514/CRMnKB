from datetime import date, datetime

from pydantic import BaseModel, Field


class ReportGenerateRequest(BaseModel):
    type: str  # customer_analysis / sales_weekly / sales_monthly / custom
    customer_id: int | None = None
    start_date: date | None = None
    end_date: date | None = None
    # custom 类型必填：自然语言报告需求
    prompt: str | None = Field(default=None, min_length=1, max_length=2000)
    kb_ids: list[int] | None = None  # 检索范围，空则全租户
    file_ids: list[int] | None = None  # 对话附加文件（标注来源用）
    # AI 生成超时（秒），默认读后端 LLM_CHAT_TIMEOUT_SECONDS；范围 30~600
    timeout: int | None = Field(default=None, ge=30, le=600)
    # 报告语言：zh=中文 / en=英文 / zh_en=中英双语（HTML 版本可切换显示）
    language: str = "zh"


class ReportReviseRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=1000)


class ReportOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    tenant_id: int
    user_id: int
    type: str
    title: str
    status: str
    params: dict
    format: str = "markdown"  # html / markdown
    error: str | None = None
    progress: str | None = None  # 生成/修改中的进度提示
    created_at: datetime


class ReportDetailOut(ReportOut):
    content: str | None = None
    revisions: list = []


class ReportListOut(BaseModel):
    items: list[ReportOut]
    total: int
