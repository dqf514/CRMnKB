<template>
  <div>
    <el-card>
      <div class="toolbar">
        <span class="hint">管理 AI 可调用的工具（Skill）；内置工具仅可配置，自定义 API 可增删</span>
        <el-button type="primary" :icon="Plus" style="margin-left: auto" @click="openForm()">新增自定义 Skill</el-button>
      </div>

      <el-table :data="list" v-loading="loading" stripe>
        <el-table-column label="名称" min-width="150" show-overflow-tooltip>
          <template #default="{ row }">
            {{ row.display_name || row.name }}
            <span class="skill-name">{{ row.name }}</span>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="120">
          <template #default="{ row }">
            <el-tag size="small" :type="enumTagType(skillTypeMap, row.type)">
              {{ enumLabel(skillTypeMap, row.type) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="description" label="描述" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">{{ row.description || '-' }}</template>
        </el-table-column>
        <el-table-column label="启用" width="80">
          <template #default="{ row }">
            <el-switch :model-value="row.enabled" @change="(val) => toggleEnabled(row, val)" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="190" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openForm(row)">配置</el-button>
            <el-button link type="success" size="small" @click="openTest(row)">测试</el-button>
            <el-button v-if="row.type === 'api'" link type="danger" size="small" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && !list.length" description="暂无 Skill" :image-size="80" />
    </el-card>

    <!-- 配置 / 新增 -->
    <el-drawer v-model="formDrawer" :title="form.id ? '配置 Skill' : '新增自定义 Skill'" size="min(92vw, 520px)">
      <el-form :model="form" :rules="formRules" ref="formRef" label-width="110px">
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" :disabled="!!form.id" placeholder="英文小写下划线，如 stock_query" />
        </el-form-item>
        <el-form-item label="显示名" prop="display_name">
          <el-input v-model="form.display_name" placeholder="展示给用户的名称" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" placeholder="工具用途，帮助 AI 判断何时调用" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>

        <!-- 内置 web_search -->
        <template v-if="form.type === 'builtin' && form.name === 'web_search'">
          <el-form-item label="Provider">
            <el-radio-group v-model="form.provider">
              <el-radio-button value="tavily">Tavily</el-radio-button>
              <el-radio-button value="bing">Bing</el-radio-button>
              <el-radio-button value="bocha">博查（国内）</el-radio-button>
              <el-radio-button value="duckduckgo">DuckDuckGo</el-radio-button>
            </el-radio-group>
          </el-form-item>
          <el-form-item v-if="form.provider !== 'duckduckgo'" label="API Key">
            <el-input v-model="form.apiKey" type="password" show-password placeholder="脱敏串原样保留表示不修改" />
          </el-form-item>
          <el-alert v-else type="warning" :closable="false" title="DuckDuckGo 免 API Key，但国内网络不可达（连接会超时失败），请改用 Tavily / 博查" style="margin-bottom: 12px" />
        </template>
        <!-- 其他内置工具（如 web_fetch）无可配项 -->
        <el-alert
          v-else-if="form.type === 'builtin'"
          type="info"
          :closable="false"
          title="该内置工具无可配置项，直接启用即可"
          style="margin-bottom: 12px"
        />

        <!-- 自定义 API -->
        <template v-if="form.type === 'api'">
          <el-form-item label="Method" prop="method">
            <el-select v-model="form.method" style="width: 100%">
              <el-option label="GET" value="GET" />
              <el-option label="POST" value="POST" />
              <el-option label="PUT" value="PUT" />
              <el-option label="DELETE" value="DELETE" />
            </el-select>
          </el-form-item>
          <el-form-item label="URL" prop="url">
            <el-input v-model="form.url" placeholder="https://…" />
          </el-form-item>
          <el-form-item label="Headers">
            <el-input v-model="form.headersText" type="textarea" :rows="3" placeholder='JSON 对象，如 {"Authorization":"Bearer …"}' />
          </el-form-item>
          <el-form-item label="参数 Schema">
            <el-input v-model="form.schemaText" type="textarea" :rows="5" placeholder='JSON Schema，如 {"type":"object","properties":{"q":{"type":"string"}}}' />
          </el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="formDrawer = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
      </template>
    </el-drawer>

    <!-- 测试 -->
    <el-dialog v-model="testDialog" :title="`测试 - ${testingSkill?.display_name || testingSkill?.name || ''}`" width="min(92vw, 560px)">
      <el-input v-model="testArgs" type="textarea" :rows="4" placeholder='调用参数 JSON，如 {"query":"…"}' />
      <div v-if="testResult" class="test-result">
        <template v-if="testResult.ok">
          <el-alert type="success" :closable="false" :title="`调用成功，耗时 ${testResult.latency_ms ?? '-'} ms`" />
          <pre class="test-pre">{{ testResultText }}</pre>
        </template>
        <el-alert v-else type="error" :closable="false" :title="testResult.error || '调用失败'" />
      </div>
      <template #footer>
        <el-button @click="testDialog = false">关闭</el-button>
        <el-button type="primary" :loading="testing" @click="runTest">运行</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, nextTick } from 'vue'
import { Plus } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { confirmDanger } from '../../utils/confirmDanger'
import { validateForm } from '../../utils/validateForm'
import { getAdminSkills, createSkill, updateSkill, deleteSkill, testSkill } from '../../api'
import { enumLabel, enumTagType } from '../../utils/format'

const skillTypeMap = {
  builtin: { label: '内置', type: 'primary' },
  api: { label: '自定义 API', type: 'warning' },
}

const loading = ref(false)
const list = ref([])

async function loadList() {
  loading.value = true
  try {
    const res = await getAdminSkills()
    list.value = Array.isArray(res) ? res : (res?.items || [])
  } finally {
    loading.value = false
  }
}

// ========== 配置 / 新增 ==========
const formDrawer = ref(false)
const saving = ref(false)
const formRef = ref()
const emptyForm = {
  id: null, name: '', display_name: '', description: '', type: 'api', enabled: true,
  provider: 'tavily', apiKey: '', method: 'GET', url: '', headersText: '', schemaText: '',
}
const form = reactive({ ...emptyForm })
const formRules = {
  name: [
    { required: true, message: '请输入名称', trigger: 'blur' },
    { pattern: /^[a-z][a-z0-9_]*$/, message: '仅限英文小写、数字与下划线', trigger: 'blur' },
  ],
  display_name: [{ required: true, message: '请输入显示名', trigger: 'blur' }],
  method: [{ required: true, message: '请选择 Method', trigger: 'change' }],
  url: [
    { required: true, message: '请输入 URL', trigger: 'blur' },
    {
      validator: (r, v, cb) => {
        if (!v) return cb()
        try {
          const u = new URL(v)
          return ['http:', 'https:'].includes(u.protocol) ? cb() : cb(new Error('URL 必须是合法的 http(s) 地址'))
        } catch {
          cb(new Error('URL 必须是合法的 http(s) 地址'))
        }
      },
      trigger: 'blur',
    },
  ],
}

function openForm(row) {
  if (row) {
    Object.assign(form, emptyForm, {
      id: row.id,
      name: row.name,
      display_name: row.display_name || row.name,
      description: row.description || '',
      type: row.type,
      enabled: row.enabled,
      provider: row.config?.provider || 'tavily',
      // 脱敏串原样回传表示不修改（沿用模型管理的 api_key 惯例）
      apiKey: row.config?.api_key || '',
      method: row.config?.method || 'GET',
      url: row.config?.url || '',
      headersText: row.config?.headers ? JSON.stringify(row.config.headers, null, 2) : '',
      schemaText: row.config?.params_schema ? JSON.stringify(row.config.params_schema, null, 2) : '',
    })
  } else {
    Object.assign(form, emptyForm)
  }
  formDrawer.value = true
  nextTick(() => formRef.value?.clearValidate())
}

function parseJsonField(text, label) {
  if (!text.trim()) return { value: null }
  try {
    return { value: JSON.parse(text) }
  } catch {
    ElMessage.error(`${label} 不是合法 JSON`)
    return { error: true }
  }
}

async function handleSave() {
  if (!(await validateForm(formRef.value))) return
  let config = null
  if (form.type === 'builtin' && form.name === 'web_search') {
    config = { provider: form.provider }
    if (form.provider !== 'duckduckgo' && form.apiKey) config.api_key = form.apiKey
  } else if (form.type === 'api') {
    const headers = parseJsonField(form.headersText, 'Headers')
    if (headers.error) return
    const schema = parseJsonField(form.schemaText, '参数 Schema')
    if (schema.error) return
    config = { method: form.method, url: form.url }
    if (headers.value) config.headers = headers.value
    if (schema.value) config.params_schema = schema.value
  }
  saving.value = true
  try {
    const data = { display_name: form.display_name, description: form.description, enabled: form.enabled }
    if (config) data.config = config
    if (form.type === 'builtin') {
      // 内置技能不可创建：未入库时列表 id 为 null，以 name 作为路径参数（后端按名自动建行）
      await updateSkill(form.id ?? form.name, data)
      ElMessage.success('已保存')
    } else if (form.id) {
      await updateSkill(form.id, data)
      ElMessage.success('已保存')
    } else {
      await createSkill({ ...data, name: form.name, type: 'api', config: config || {} })
      ElMessage.success('创建成功')
    }
    formDrawer.value = false
    loadList()
  } finally {
    saving.value = false
  }
}

async function toggleEnabled(row, val) {
  // 未入库的内置技能 id 为 null，用 name 作为路径参数（后端按名解析并自动建行）
  await updateSkill(row.id ?? row.name, { enabled: val })
  await loadList() // 首次开启会自动建行拿到真实 id，重新拉取列表
  ElMessage.success(val ? '已启用' : '已停用')
}

async function handleDelete(row) {
  const ok = await confirmDanger(`确定删除 Skill「${row.display_name || row.name}」吗？`)
  if (!ok) return
  await deleteSkill(row.id)
  ElMessage.success('删除成功')
  loadList()
}

// ========== 测试 ==========
const testDialog = ref(false)
const testing = ref(false)
const testingSkill = ref(null)
const testArgs = ref('{}')
const testResult = ref(null)

const testResultText = computed(() => {
  const r = testResult.value?.result
  return typeof r === 'string' ? r : JSON.stringify(r, null, 2)
})

function openTest(row) {
  testingSkill.value = row
  testArgs.value = '{}'
  testResult.value = null
  testDialog.value = true
}

async function runTest() {
  let args
  try {
    args = testArgs.value.trim() ? JSON.parse(testArgs.value) : {}
  } catch {
    ElMessage.error('调用参数不是合法 JSON')
    return
  }
  testing.value = true
  try {
    testResult.value = await testSkill(testingSkill.value.id, args)
  } finally {
    testing.value = false
  }
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
.skill-name {
  margin-left: 6px;
  font-size: 12px;
  color: var(--app-ink-2);
  font-family: monospace;
}
.test-result {
  margin-top: 12px;
}
.test-pre {
  margin: 10px 0 0;
  padding: 10px 14px;
  max-height: 300px;
  overflow: auto;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
  background: var(--app-bg);
  border: 1px solid var(--app-line);
  border-radius: 8px;
}
</style>
