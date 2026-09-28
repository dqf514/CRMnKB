from datetime import datetime

from pydantic import BaseModel

# 客户文档资料类型（存 KnowledgeDocument.metadata["category"]，不改表）：
# 取值列表改为 system_settings 可配置（services/doc_categories.py），管理端可增删改


class FolderCreate(BaseModel):
    name: str
    parent_id: int | None = None


class FolderUpdate(BaseModel):
    name: str | None = None
    parent_id: int | None = None


class FolderNode(BaseModel):
    id: int
    name: str
    children: list["FolderNode"] = []
    # 权限透传（前端据此决定是否显示 共享/重命名/删除）
    perm: str | None = None  # owner / edit / read / None（仅路径可见）
    owner_id: int | None = None
    is_private: bool | None = None


class LibraryFileOut(BaseModel):
    id: int
    folder_id: int | None = None
    customer_id: int | None = None
    file_name: str
    file_type: str
    file_size: int
    supported: bool
    kb_count: int = 0
    created_at: datetime
    # 权限：owner/is_private/当前用户权限
    owner_id: int | None = None
    is_private: bool | None = None
    perm: str | None = None
    # 同步字段（本地 App 冲突检测用）
    content_hash: str | None = None
    updated_at: datetime | None = None
    # 客户文档资料类型（取自关联知识库文档的 metadata.category，无则 None）
    category: str | None = None


class LibraryFileListOut(BaseModel):
    items: list[LibraryFileOut]
    total: int


class LibraryFileUpdate(BaseModel):
    file_name: str | None = None
    folder_id: int | None = None
    customer_id: int | None = None


class LibraryFileCategoryUpdate(BaseModel):
    """修改文件（客户文档）的资料类型。取值在端点按配置校验（services/doc_categories）。"""

    category: str


class DocCategoryItem(BaseModel):
    """资料类型配置项（管理端整体替换用；value 标识 + label 显示名）。"""

    value: str
    label: str


class DocCategoriesUpdate(BaseModel):
    items: list[DocCategoryItem]


class UploadedFileInfo(BaseModel):
    id: int
    file_name: str
    supported: bool


class UploadResult(BaseModel):
    uploaded: int
    supported: int
    unsupported: int
    files: list[UploadedFileInfo]
    folder_id: int | None = None
    # PR-H：未指定 KB 时系统自动归档的隐藏 KB id（前端可展示）
    auto_kb_id: int | None = None
    # 单个文件跳过原因（超限 / 文件被占用 / 写入失败等），供前端友好提示
    skipped: int = 0
    skipped_files: list[str] = []


class BatchAssociateRequest(BaseModel):
    file_ids: list[int]
    kb_ids: list[int]


# ========== 同步（本地 App / OneDrive 式） ==========


class ContentUpdateResult(BaseModel):
    id: int
    file_name: str
    file_size: int
    content_hash: str
    updated_at: datetime
    # 已排队重解析的知识库文档数（关联 KB 同步）
    reparse_docs: int


class LibraryFileChangeOut(BaseModel):
    id: int
    path: str  # 相对路径（含目录，"a/b/c.pdf"）
    file_name: str
    content_hash: str | None = None
    file_size: int
    action: str  # modified / deleted
    updated_at: datetime | None = None


class LibraryChangesOut(BaseModel):
    items: list[LibraryFileChangeOut]
    # 服务端时间：客户端作为下次 since 游标（避免本机时钟偏差）
    server_time: datetime
