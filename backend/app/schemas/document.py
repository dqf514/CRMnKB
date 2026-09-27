from datetime import datetime

from pydantic import BaseModel


class DocumentOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    tenant_id: int
    title: str
    file_name: str
    file_type: str
    status: str
    chunk_count: int
    doc_metadata: dict = {}
    created_at: datetime
    updated_at: datetime


class DocumentDetailOut(DocumentOut):
    content: str | None = None


class DocumentListOut(BaseModel):
    items: list[DocumentOut]
    total: int


class ChunkOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    document_id: int
    chunk_index: int
    content: str
    created_at: datetime
