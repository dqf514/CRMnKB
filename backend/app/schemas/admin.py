from datetime import datetime

from pydantic import BaseModel, Field


# ---------- 用户与分组 ----------

class AdminUserOut(BaseModel):
    id: int
    username: str
    name: str
    email: str | None = None
    role: str
    group_id: int | None = None
    group_name: str | None = None
    status: int
    last_login_at: datetime | None = None
    created_at: datetime


class AdminUserListOut(BaseModel):
    items: list[AdminUserOut]
    total: int


class AdminUserCreate(BaseModel):
    username: str
    password: str = Field(min_length=8)
    name: str
    role: str = "user"
    group_id: int | None = None
    email: str | None = None


class AdminUserUpdate(BaseModel):
    name: str | None = None
    email: str | None = None
    role: str | None = None
    group_id: int | None = None
    status: int | None = None


class AdminPasswordReset(BaseModel):
    new_password: str = Field(min_length=8)


class GroupCreate(BaseModel):
    name: str
    description: str | None = None


class GroupUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class GroupOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    tenant_id: int
    name: str
    description: str | None = None
    created_at: datetime


# ---------- LLM 模型管理 ----------

class LlmModelCreate(BaseModel):
    name: str
    provider: str  # api / ollama
    model_type: str  # chat / embed / vision / asr / rerank
    base_url: str
    api_key: str | None = None
    model: str
    is_default: bool = False
    enabled: bool = True


class LlmModelUpdate(BaseModel):
    name: str | None = None
    provider: str | None = None
    model_type: str | None = None  # chat / embed / vision / asr / rerank
    base_url: str | None = None
    api_key: str | None = None  # 传空/None 表示不修改
    model: str | None = None
    is_default: bool | None = None
    enabled: bool | None = None


class LlmModelOut(BaseModel):
    id: int
    name: str
    provider: str
    model_type: str
    base_url: str
    api_key_masked: str
    model: str
    is_default: bool
    enabled: bool
    created_at: datetime


class LlmTestResult(BaseModel):
    ok: bool
    latency_ms: int
    detail: str


class LlmRemoteModelsRequest(BaseModel):
    """拉取远端可用模型列表（未保存的配置）。"""

    provider: str  # api / ollama
    base_url: str
    api_key: str | None = None


class LlmRemoteModelsResult(BaseModel):
    ok: bool
    models: list[str] = []
    detail: str = ""


class LlmTestConfigRequest(BaseModel):
    """不保存直接测试一组模型配置。"""

    provider: str  # api / ollama
    model_type: str  # chat / embed / vision / asr / rerank
    base_url: str
    api_key: str | None = None
    model: str
    # 编辑场景：传入 model_id 且 api_key 为空时，用库中已保存的 key 测试
    model_id: int | None = None


# ---------- 异常日志 ----------

class ErrorLogOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    tenant_id: int | None = None
    level: str
    module: str
    message: str
    detail: str | None = None
    resolved: bool
    created_at: datetime


class ErrorListOut(BaseModel):
    items: list[ErrorLogOut]
    total: int
