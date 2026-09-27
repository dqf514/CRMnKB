from pydantic import BaseModel


class RagQueryRequest(BaseModel):
    question: str
    top_k: int | None = None
    kb_ids: list[int] | None = None  # 限定知识库范围，缺省检索全部


class RagSource(BaseModel):
    chunk_id: int
    doc_id: int
    doc_title: str
    score: float
    excerpt: str
    # 直达文档库文件预览；doc 无 file_id 或文件已软删时为 None（前端降级不可点击）
    file_id: int | None = None
    file_name: str | None = None
    file_type: str | None = None
    file_size: int | None = None


class RagQueryResponse(BaseModel):
    answer: str
    sources: list[RagSource]
    grounded: bool
    query_log_id: int | None = None
