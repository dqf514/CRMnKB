from datetime import datetime

from pydantic import BaseModel, Field


class SkillOut(BaseModel):
    id: int | None = None  # builtin 未建行时为 None（首次保存自动建行）
    name: str
    display_name: str
    description: str
    type: str  # builtin / api / mcp
    enabled: bool
    config: dict = {}  # 敏感字段已脱敏
    timeout: int = 60  # PR-E：当前超时秒数（来自 config.timeout）
    created_at: datetime | None = None


class SkillUpdate(BaseModel):
    enabled: bool | None = None
    config: dict | None = None
    description: str | None = None
    display_name: str | None = None
    # builtin 未建行时首次保存需带 name 以便自动建行
    name: str | None = None
    # PR-E：超时秒数（10~300），写入 config.timeout
    timeout: int | None = Field(default=None, ge=10, le=300)


class ApiSkillCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50, pattern=r"^[a-z][a-z0-9_]*$")
    display_name: str | None = None
    description: str | None = None
    enabled: bool = False
    config: dict = {}  # method/url/headers/body/parameters
    timeout: int | None = Field(default=None, ge=10, le=300)


class SkillStats(BaseModel):
    """PR-E：每个 skill 的调用统计（最近 30 天）。"""

    skill_name: str
    total: int
    failed: int
    avg_latency_ms: int
    last_called_at: datetime | None = None


class SkillTestRequest(BaseModel):
    args: dict = {}


class SkillTestResult(BaseModel):
    ok: bool
    result: str | None = None
    error: str | None = None
    latency_ms: int
