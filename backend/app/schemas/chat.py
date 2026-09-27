from datetime import datetime

from pydantic import BaseModel

from app.schemas.rag import RagSource


class ChatSessionCreate(BaseModel):
    title: str | None = None


class ChatSessionOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    title: str
    created_at: datetime
    updated_at: datetime


class ChatMessageOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    role: str
    content: str
    sources: list = []
    grounded: bool
    created_at: datetime


class ChatAskRequest(BaseModel):
    question: str
    session_id: int | None = None
    kb_ids: list[int] | None = None  # 限定知识库范围，缺省检索全部
    # PR-G：文件级 scope（Studio 用），优先级高于 kb_ids（更精确）
    file_ids: list[int] | None = None
    # 深度思考（推理）开关：null=按 CHAT_DEFAULT_THINKING 默认；
    # false 时向模型发送 LLM_CHAT_THINKING_PARAM=false 并剥离 <think> 输出
    thinking: bool | None = None


class ChatAskResponse(BaseModel):
    answer: str
    sources: list[RagSource]
    grounded: bool
    query_log_id: int | None = None
    session_id: int
    tools_used: list[str] = []  # 本轮对话调用过的工具名
