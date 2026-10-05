"""模型字段 ↔ init_db 幂等迁移对照护栏。

背景：项目没有 Alembic。老库升级靠 database.py init_db() 里的
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS ...` 幂等补列；新库靠 create_all。
风险是「模型加了列但忘补 ALTER」——新库正常、老库启动后缺列运行时炸。

本测试的约定（与 database.py 头注释一致）：
- **整表新增**（新模型）：create_all 对新老库都会建全表，无需 ALTER，不在管控范围；
- **已有表新增列**：必须在 init_db() 里追加幂等 ALTER，否则本测试 fail。

实现：解析 database.py 源码提取 ALTER 覆盖的 (table, column)，与模型列比对；
下方 _KNOWN_COLUMNS 是本测试落地时（2026-10）的存量列快照。
⚠️ 给已有表加列导致 fail 时，正确做法是在 database.py 补 ALTER，
   而不是把新列加进快照；快照只允许随「列被删除/重命名」收敛。
"""
import re
from pathlib import Path

from app.models import Base  # noqa: F401  导入即注册全部模型

_DATABASE_PY = Path(__file__).resolve().parents[1] / "app" / "database.py"

_ALTER_RE = re.compile(
    r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+(\w+)",
    re.IGNORECASE,
)

# 存量列快照（2026-10 批次五落地时全量生成）：{表名: [列名...]}。
# 含义：这些列已由历史 create_all + 既有 ALTER 覆盖，属已发布 schema，无需再校验。
_KNOWN_COLUMNS: dict[str, list[str]] = {
    'agent_approvals': ['arguments', 'chat_session_id', 'created_at', 'decided_at', 'decided_by', 'decision_reason', 'id', 'requester_user_id', 'result', 'status', 'summary', 'tenant_id', 'tool_name', 'updated_at'],
    'ai_feedback': ['comment', 'created_at', 'id', 'query_log_id', 'rating', 'tenant_id', 'user_id'],
    'audit_logs': ['action', 'created_at', 'detail', 'id', 'ip', 'resource_id', 'resource_type', 'tenant_id', 'user_id'],
    'brand_settings': ['id', 'logo_path', 'system_name', 'updated_at'],
    'chat_messages': ['content', 'created_at', 'grounded', 'id', 'query_log_id', 'role', 'session_id', 'sources'],
    'chat_sessions': ['created_at', 'dsh_session_id', 'id', 'tenant_id', 'title', 'updated_at', 'user_id'],
    'chunk_questions': ['chunk_id', 'content', 'created_at', 'embedding', 'hit_num', 'id', 'tenant_id'],
    'customers': ['address', 'ai_brief', 'ai_brief_at', 'attributes', 'birthday', 'company', 'created_at', 'ddq_status', 'deleted_at', 'email', 'id', 'industries', 'industry', 'name', 'owner_id', 'phone', 'position', 'profile', 'profile_status', 'profile_updated_at', 'source', 'status', 'tags', 'tenant_id', 'updated_at', 'wechat'],
    'document_chunks': ['chunk_index', 'content', 'created_at', 'document_id', 'embedding', 'id', 'tenant_id'],
    'document_versions': ['chunk_count', 'content', 'created_at', 'document_id', 'id', 'note'],
    'error_logs': ['created_at', 'detail', 'id', 'level', 'message', 'module', 'resolved', 'tenant_id'],
    'follow_up_records': ['ai_summary', 'content', 'created_at', 'customer_id', 'id', 'next_step', 'type', 'user_id'],
    'industries': ['created_at', 'enabled', 'id', 'name', 'sort', 'tenant_id'],
    'knowledge_bases': ['created_at', 'customer_id', 'deleted_at', 'description', 'id', 'is_auto', 'is_private', 'name', 'owner_id', 'tenant_id', 'type'],
    'knowledge_documents': ['chunk_count', 'content', 'created_at', 'directly_return_answer', 'directly_return_similarity', 'file_id', 'file_name', 'file_path', 'file_type', 'id', 'kb_id', 'metadata', 'status', 'tenant_id', 'title', 'updated_at'],
    'library_files': ['content_hash', 'created_at', 'customer_id', 'deleted_at', 'file_name', 'file_path', 'file_size', 'file_type', 'folder_id', 'id', 'is_private', 'owner_id', 'supported', 'tenant_id', 'updated_at'],
    'library_folders': ['created_at', 'deleted_at', 'id', 'is_private', 'name', 'owner_id', 'parent_id', 'tenant_id'],
    'llm_call_logs': ['caller', 'completion_tokens', 'created_at', 'error', 'id', 'latency_ms', 'model', 'model_type', 'prompt_tokens', 'provider', 'success', 'tenant_id', 'user_id'],
    'llm_models': ['api_key', 'base_url', 'created_at', 'enabled', 'id', 'is_default', 'model', 'model_type', 'name', 'provider', 'tenant_id'],
    'login_attempts': ['created_at', 'id', 'ip', 'success', 'tenant_id', 'username'],
    'login_codes': ['attempts', 'channel', 'code_hash', 'created_at', 'expires_at', 'id', 'ip', 'phone', 'used'],
    'mcp_servers': ['config', 'created_at', 'discovered_tools', 'display_name', 'enabled', 'id', 'last_connected_at', 'last_error', 'name', 'status', 'tenant_id', 'transport', 'updated_at'],
    'notebook_notes': ['content', 'created_at', 'id', 'notebook_id', 'sort', 'source_ref', 'source_type', 'tenant_id', 'title', 'updated_at'],
    'notebooks': ['created_at', 'created_by', 'deleted_at', 'description', 'id', 'is_private', 'name', 'source_file_ids', 'source_kb_ids', 'tenant_id', 'updated_at'],
    'notifications': ['content', 'created_at', 'dedupe_key', 'id', 'is_read', 'resource_id', 'resource_type', 'task_id', 'tenant_id', 'title', 'type', 'user_id'],
    'opportunities': ['amount', 'created_at', 'customer_id', 'expected_close_date', 'id', 'name', 'owner_id', 'probability', 'stage', 'updated_at'],
    'rag_query_logs': ['answer', 'created_at', 'grounded', 'id', 'question', 'sources', 'tenant_id', 'user_id'],
    'reminder_rules': ['action_config', 'created_at', 'enabled', 'id', 'name', 'tenant_id', 'trigger_config', 'trigger_type'],
    'reports': ['content', 'created_at', 'error', 'id', 'params', 'progress', 'status', 'tenant_id', 'title', 'type', 'user_id'],
    'resource_permissions': ['created_at', 'id', 'permission', 'resource_id', 'resource_type', 'tenant_id', 'user_id'],
    'skill_call_logs': ['caller', 'created_at', 'error', 'id', 'latency_ms', 'skill_name', 'success', 'tenant_id', 'user_id'],
    'skills': ['config', 'created_at', 'description', 'display_name', 'enabled', 'id', 'name', 'tenant_id', 'type'],
    'system_settings': ['key', 'updated_at', 'value'],
    'tasks': ['ai_generated', 'completed_at', 'created_at', 'customer_id', 'description', 'due_date', 'id', 'priority', 'source', 'status', 'tenant_id', 'title', 'type', 'user_id'],
    'tenants': ['created_at', 'id', 'name', 'status'],
    'user_groups': ['created_at', 'description', 'id', 'name', 'tenant_id'],
    'user_memories': ['chat_session_id', 'content', 'created_at', 'id', 'source', 'updated_at', 'user_id'],
    'users': ['avatar_url', 'created_at', 'email', 'group_id', 'id', 'last_login_at', 'must_change_password', 'name', 'password_changed_at', 'password_hash', 'phone', 'preferences', 'role', 'status', 'tenant_id', 'username', 'wechat_openid', 'wechat_unionid'],
    'workflow_runs': ['created_at', 'detail', 'id', 'matched_count', 'status', 'workflow_id'],
    'workflows': ['action_config', 'action_type', 'conditions', 'created_at', 'description', 'enabled', 'id', 'last_run_at', 'name', 'tenant_id', 'trigger_config', 'trigger_type'],
}


def _alter_coverage() -> set[tuple[str, str]]:
    """从 database.py 源码提取幂等 ALTER 覆盖的 (table, column) 集合。"""
    src = _DATABASE_PY.read_text(encoding="utf-8")
    return {(m.group(1), m.group(2)) for m in _ALTER_RE.finditer(src)}


def test_alter_parser_sanity():
    """解析器自保：database.py 里应能提到几十条 ADD COLUMN，提不到说明正则失效。"""
    covered = _alter_coverage()
    assert len(covered) >= 40, f"只解析到 {len(covered)} 条 ALTER，正则可能已失效"


def test_new_columns_on_existing_tables_have_alter():
    """已有表（快照内的表）出现快照外的新列 → 必须在 init_db() 有幂等 ALTER。"""
    covered = _alter_coverage()
    missing: list[str] = []
    for table_name, table in Base.metadata.tables.items():
        known = _KNOWN_COLUMNS.get(table_name)
        if known is None:
            continue  # 整表新增：create_all 覆盖，不管控
        for col in table.columns:
            if col.name not in known and (table_name, col.name) not in covered:
                missing.append(f"{table_name}.{col.name}")
    assert not missing, (
        "以下既有表的新列在 database.py init_db() 中缺少幂等 ALTER"
        "（ALTER TABLE ... ADD COLUMN IF NOT EXISTS ...），老库升级会缺列："
        + ", ".join(missing)
    )


def test_snapshot_stays_in_sync_with_models():
    """快照收敛性：快照里的 (表, 列) 必须仍存在于模型中——列被删除/重命名时
    同步收敛快照，防止快照静默膨胀失去意义。"""
    stale: list[str] = []
    for table_name, columns in _KNOWN_COLUMNS.items():
        table = Base.metadata.tables.get(table_name)
        if table is None:
            stale.append(f"{table_name}（整表已删除）")
            continue
        for col in columns:
            if col not in table.columns:
                stale.append(f"{table_name}.{col}")
    assert not stale, "快照中存在模型里已没有的列，请收敛 _KNOWN_COLUMNS：" + ", ".join(stale)
