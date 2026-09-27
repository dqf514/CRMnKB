"""PR-G：工作区（Notebook）及其内容（Note）Pydantic schema。"""
from datetime import datetime

from pydantic import BaseModel, Field


class NotebookCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    source_kb_ids: list[int] = []
    source_file_ids: list[int] = []


class NotebookUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    source_kb_ids: list[int] | None = None
    source_file_ids: list[int] | None = None


class NotebookOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    name: str
    description: str | None
    source_kb_ids: list = []
    source_file_ids: list = []
    created_by: int | None
    created_at: datetime
    updated_at: datetime
    note_count: int = 0  # 由服务层补
    # 权限：is_private/当前用户权限（created_by 即 owner）
    is_private: bool | None = None
    perm: str | None = None


class NotebookNoteCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = ""
    source_type: str = "manual"
    source_ref: dict | None = None


class NotebookNoteUpdate(BaseModel):
    title: str | None = None
    content: str | None = None
    sort: int | None = None


class NotebookNoteOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    notebook_id: int
    title: str
    content: str
    source_type: str
    source_ref: dict | None
    sort: int
    created_at: datetime
    updated_at: datetime


class NotebookNoteFromChat(BaseModel):
    """从 chat 助手消息生成 note 的请求体。"""

    notebook_id: int
    session_id: int | None = None
    query_log_id: int | None = None
    question: str
    answer: str
    sources: list = []


class SaveAsDocumentRequest(BaseModel):
    """note → KB 文档"""

    kb_id: int