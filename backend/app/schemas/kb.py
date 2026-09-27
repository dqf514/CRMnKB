from datetime import datetime

from pydantic import BaseModel


class KbCreate(BaseModel):
    name: str
    description: str | None = None


class KbUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class KbOut(BaseModel):
    id: int
    name: str
    description: str | None = None
    type: str  # general / customer / auto
    customer_id: int | None = None
    doc_count: int = 0
    chunk_count: int = 0
    is_auto: bool = False  # PR-H：系统自动 KB（上传无 kb_ids 时归档），前端默认隐藏
    created_at: datetime
    # 权限：owner/is_private/当前用户权限（read/edit/owner）
    owner_id: int | None = None
    is_private: bool | None = None
    perm: str | None = None


class KbDocOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    file_id: int | None = None
    title: str
    file_name: str
    file_type: str
    status: str  # processing / ready / failed / unsupported
    chunk_count: int
    processing_method: str | None = None  # text / vision_ocr / image_describe / asr / video_asr / ocr
    error: str | None = None  # 失败原因（status=failed 时）
    vision_reviewed_at: str | None = None  # 视觉复核时间（本地 OCR 识别后人工复核用）
    vision_review_error: str | None = None  # 视觉复核失败原因
    created_at: datetime


class KbChunkOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    chunk_index: int
    content: str
    created_at: datetime


class AssociateFilesRequest(BaseModel):
    file_ids: list[int]


class AssociateResult(BaseModel):
    associated: int
    already: int


class HitTestRequest(BaseModel):
    query: str
    top_k: int = 5
    threshold: float | None = None
