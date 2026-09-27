<template>
  <div>
    <el-card>
      <div class="toolbar">
        <span class="hint">管理 MCP（Model Context Protocol）外部工具服务器；stdio 启动本地子进程，SSE 连远程 HTTP。</span>
        <el-button type="primary" :icon="Plus" style="margin-left: auto" @click="openForm()">添加 MCP Server</el-button>
      </div>

      <el-table :data="list" v-loading="loading" stripe>
        <el-table-column label="名称" min-width="160" show-overflow-tooltip>
          <template #default="{ row }">
            {{ row.display_name || row.name }}
            <span class="mcp-name">{{ row.name }}</span>
          </template>
        </el-table-column>
        <el-table-column label="Transport" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="row.transport === 'stdio' ? 'primary' : 'success'">
              {{ row.transport }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="160">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small">
              {{ statusLabel(row.status) }}
            </el-tag>
            <el-tooltip v-if="row.last_error" :content="row.last_error" placement="top">
              <el-icon style="margin-left: 4px; color: #f56c6c; cursor: help"><Warning /></el-icon>
            </el-tooltip>
            <div v-if="row.last_connected_at" class="muted">
              {{ formatDateTime(row.last_connected_at) }}
            </div>
          </template>
        </el-table-column>
        <el-table-column label="发现工具" width="100">
          <template #default="{ row }">{{ (row.discovered_tools || []).length }}</template>
        </el-table-column>
        <el-table-column label="启用" width="80">
          <template #default="{ row }">
            <el-switch :model-value="row.enabled" @change="(val) => toggleEnabled(row, val)" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="280" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="connect(row)">连接</el-button>
            <el-button link type="warning" size="small" @click="disconnect(row)">断开</el-button>
            <el-button link type="success" size="small" @click="syncTools(row)">同步工具</el-button>
            <el-button link size="small" @click="showTools(row)">查看工具</el-button>
            <el-button link size="small" @click="openForm(row)">编辑</el-button>
            <el-button link type="danger" size="small" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && !list.length" description="暂无 MCP server" :image-size="80" />
    </el-card>

    <!-- 新增 / 编辑 -->
    <el-drawer v-model="formDrawer" :title="form.id ? '编辑 MCP Server' : '添加 MCP Server'" size="min(92vw, 560px)">
      <el-form :model="form" :rules="formRules" ref="formRef" label-width="100px">
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" :disabled="!!form.id" placeholder="英文小写下划线，如 fs / git" />
        </el-form-item>
        <el-form-item label="显示名">
          <el-input v-model="form.display_name" placeholder="展示给管理员的名称" />
        </el-form-item>
        <el-form-item label="Transport" prop="transport">
          <el-radio-group v-model="form.transport">
            <el-radio-button label="stdio">stdio（本地子进程）</el-radio-button>
            <el-radio-button label="sse">sse（远程 HTTP）</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>

        <!-- stdio -->
        <template v-if="form.transport === 'stdio'">
          <el-form-item label="Command" prop="command">
            <el-input v-model="form.command" placeholder="如 npx / python / uvicorn" />
          </el-form-item>
          <el-form-item label="Args">
            <el-input
              v-model="form.argsText"
              type="textarea"
              :rows="2"
              placeholder="空格分隔的参数，如：-y @modelcontextprotocol/server-filesystem /tmp"
            />
          </el-form-item>
          <el-form-item label="Env (JSON)">
            <el-input v-model="form.envText" type="textarea" :rows="3" placeholder='{"KEY": "value"}' />
          </el-form-item>
        </template>

        <!-- sse -->
        <template v-if="form.transport === 'sse'">
          <el-form-item label="URL" prop="url">
            <el-input v-model="form.url" placeholder="https://example.com/mcp/sse" />
          </el-form-item>
          <el-form-item label="Headers (JSON)">
            <el-input v-model="form.headersText" type="textarea" :rows="3" placeholder='{"Authorization": "Bearer ..."}' />
          </el-form-item>
          <el-form-item label="Auth Token">
            <el-input v-model="form.authToken" type="password" placeholder="自动加到 Authorization: Bearer" />
          </el-form-item>
        </template>
      </el-form>

      <div class="drawer-footer">
        <el-button @click="formDrawer = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
      </div>
    </el-drawer>

    <!-- 工具详情 -->
    <el-dialog v-model="toolsDialog" :title="`已发现工具 — ${toolsServerName}`" width="min(90vw, 720px)">
      <el-table :data="toolsList" max-height="500">
        <el-table-column prop="name" label="工具名" min-width="140" />
        <el-table-column prop="description" label="描述" min-width="200" show-overflow-tooltip />
        <el-table-column label="Input Schema" min-width="180">
          <template #default="{ row }">
            <code class="schema-code">{{ JSON.stringify(row.input_schema) }}</code>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!toolsList.length" description="无工具" :image-size="60" />
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { Plus, Warning } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { formatDateTime } from '../../utils/format'
import {
  getMcpServers,
  createMcpServer,
  updateMcpServer,
  deleteMcpServer,
  connectMcpServer,
  disconnectMcpServer,
  syncMcpTools,
  getMcpTools,
} from '../../api'

const loading = ref(false)
const saving = ref(false)
const list = ref([])

const STATUS = {
  unknown: { label: '未连接', type: 'info' },
  connected: { label: '已连接', type: 'success' },
  error: { label: '连接失败', type: 'danger' },
  disconnected: { label: '已断开', type: 'warning' },
}
function statusLabel(s) { return STATUS[s]?.label || s }
function statusTagType(s) { return STATUS[s]?.type || 'info' }

async function loadList() {
  loading.value = true
  try {
    list.value = await getMcpServers()
  } finally {
    loading.value = false
  }
}

// ========== 表单 ==========
const formDrawer = ref(false)
const formRef = ref()
const emptyForm = () => ({
  id: null,
  name: '',
  display_name: '',
  transport: 'stdio',
  enabled: false,
  command: '',
  argsText: '',
  envText: '',
  url: '',
  headersText: '',
  authToken: '',
})
const form = reactive(emptyForm())
const formRules = {
  name: [
    { required: true, message: '请输入名称', trigger: 'blur' },
    { pattern: /^[a-z][a-z0-9_]*$/, message: '仅限英文小写、数字与下划线', trigger: 'blur' },
  ],
  transport: [{ required: true, message: '请选择 transport', trigger: 'change' }],
  command: [{
    validator: (r, v, cb) => form.transport !== 'stdio' || v?.trim() ? cb() : cb(new Error('stdio 模式需填写 command')),
    trigger: 'blur',
  }],
  url: [{
    validator: (r, v, cb) => form.transport !== 'sse' || v?.trim() ? cb() : cb(new Error('sse 模式需填写 url')),
    trigger: 'blur',
  }],
}

function parseJsonField(text, label) {
  if (!text?.trim()) return null
  try { return JSON.parse(text) } catch { ElMessage.error(`${label} 不是合法 JSON`); throw new Error('bad json') }
}

function buildConfig() {
  if (form.transport === 'stdio') {
    const args = form.argsText?.trim() ? form.argsText.trim().split(/\s+/) : []
    const cfg = { command: form.command.trim(), args }
    const env = parseJsonField(form.envText, 'Env')
    if (env) cfg.env = env
    return cfg
  }
  // sse
  const cfg = { url: form.url.trim() }
  const headers = parseJsonField(form.headersText, 'Headers')
  if (headers) cfg.headers = headers
  if (form.authToken?.trim()) cfg.auth_token = form.authToken.trim()
  return cfg
}

function openForm(row) {
  if (row) {
    const cfg = row.config || {}
    Object.assign(form, emptyForm(), {
      id: row.id,
      name: row.name,
      display_name: row.display_name || '',
      transport: row.transport,
      enabled: row.enabled,
      command: cfg.command || '',
      argsText: (cfg.args || []).join(' '),
      envText: cfg.env ? JSON.stringify(cfg.env, null, 2) : '',
      url: cfg.url || '',
      headersText: cfg.headers ? JSON.stringify(cfg.headers, null, 2) : '',
      authToken: '',  // 不回显敏感字段
    })
  } else {
    Object.assign(form, emptyForm())
  }
  formDrawer.value = true
}

async function handleSave() {
  await formRef.value.validate()
  let config
  try { config = buildConfig() } catch { return }
  saving.value = true
  try {
    const data = {
      display_name: form.display_name,
      transport: form.transport,
      enabled: form.enabled,
      config,
    }
    if (form.id) {
      await updateMcpServer(form.id, data)
      ElMessage.success('已保存')
    } else {
      await createMcpServer({ ...data, name: form.name })
      ElMessage.success('已创建')
    }
    formDrawer.value = false
    loadList()
  } finally {
    saving.value = false
  }
}

async function toggleEnabled(row, val) {
  await updateMcpServer(row.id, { enabled: val })
  row.enabled = val
  ElMessage.success(val ? '已启用' : '已停用')
}

async function connect(row) {
  const loading = ElMessage({ message: `正在连接 ${row.name} ...`, type: 'info', duration: 0 })
  try {
    const result = await connectMcpServer(row.id)
    if (result.ok) {
      ElMessage.success(`连接成功，发现 ${result.tools.length} 个工具`)
    } else {
      ElMessage.error(`连接失败: ${result.error}`)
    }
    loadList()
  } finally {
    loading.close()
  }
}

async function disconnect(row) {
  await disconnectMcpServer(row.id)
  ElMessage.success('已断开')
  loadList()
}

async function syncTools(row) {
  if (!row.discovered_tools?.length) {
    ElMessage.warning('请先点击 [连接] 发现工具')
    return
  }
  const { value: synced } = await ElMessageBox.confirm(
    `将 ${row.discovered_tools.length} 个工具注册为 Skill（type=mcp）？`,
    '同步工具',
    { type: 'info' }
  ).then(() => ({ value: 1 })).catch(() => ({ value: 0 }))
  if (!synced) return
  const result = await syncMcpTools(row.id)
  ElMessage.success(`已同步 ${result.synced} 个工具到 Skill 列表`)
}

// ========== 工具详情 ==========
const toolsDialog = ref(false)
const toolsServerName = ref('')
const toolsList = ref([])
async function showTools(row) {
  toolsServerName.value = row.display_name || row.name
  toolsList.value = row.discovered_tools || []
  toolsDialog.value = true
}

async function handleDelete(row) {
  await ElMessageBox.confirm(
    `确定删除 MCP server「${row.display_name || row.name}」吗？关联的 Skill 会被一并删除。`,
    '删除确认',
    { type: 'warning' }
  )
  await deleteMcpServer(row.id)
  ElMessage.success('已删除')
  loadList()
}

onMounted(loadList)
</script>

<style scoped>
.toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}
.hint {
  color: var(--app-ink-2);
  font-size: 13px;
}
.mcp-name {
  color: var(--app-ink-2);
  font-size: 11px;
  margin-left: 4px;
}
.muted {
  color: var(--app-ink-2);
  font-size: 11px;
  margin-top: 2px;
}
.drawer-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
}
.schema-code {
  font-size: 11px;
  color: var(--app-ink-2);
  background: var(--app-fill-1);
  padding: 2px 4px;
  border-radius: 3px;
}
</style>